import argparse
import json
import os
from pathlib import Path

import pandas as pd

from scripts.common import BASE, ENDPOINT, digest, now, read_json, read_jsonl, save, verify_vendor, write_jsonl
from scripts.screen import add_screen_args, capture_requests, screening_config

TERMINAL = {"completed", "failed", "expired", "cancelled"}


def prepare(args):
    config = screening_config(args, offline=True)
    requests, selected = capture_requests(config)
    if not requests:
        raise SystemExit("No eligible articles after author filtering")
    if args.run_dir.exists() and any(args.run_dir.iterdir()):
        raise SystemExit("Run directory is not empty. Select a new directory to freeze new requests")
    args.run_dir.mkdir(parents=True, exist_ok=True)
    selected.to_csv(args.run_dir / "rows.csv", index=False)
    jobs = []
    for start in range(0, len(requests), args.shard_rows):
        name = f"part_{len(jobs):03d}"
        path = args.run_dir / "inputs" / f"{name}.jsonl"
        rows = requests[start:start + args.shard_rows]
        write_jsonl(path, rows)
        jobs.append({"name": name, "file": str(path.relative_to(args.run_dir)), "sha256": digest(path),
                     "rows": len(rows), "status": "prepared"})
    manifest = verify_vendor()
    plan = {"created_utc": now(), "stage": "abstract_screen", "pathogen": args.pathogen, "model": args.model,
            "endpoint": ENDPOINT, "rows": len(requests),
            "source_input_sha256": digest(args.input), "rows_sha256": digest(args.run_dir / "rows.csv"),
            "upstream_commit": manifest["upstream_commit"], "dataset_revision": manifest["dataset_revision"],
            "jobs": jobs, "model_calls": 0, "transport": "openai_batch_api"}
    save(args.run_dir / "plan.json", plan)
    print(json.dumps({"rows": len(requests), "jobs": len(jobs), "run_dir": str(args.run_dir), "model_calls": 0}))


def load_state(directory):
    verify_vendor()
    plan = read_json(directory / "plan.json")
    if plan["stage"] != "abstract_screen":
        raise ValueError("Only title and abstract Batch screening is supported")
    state_path = directory / "state.json"
    if state_path.exists():
        state = read_json(state_path)
        if state["plan_sha256"] != digest(directory / "plan.json"):
            raise RuntimeError("Frozen plan changed")
    else:
        state = {"plan_sha256": digest(directory / "plan.json"), "jobs": plan["jobs"]}
    if digest(directory / "rows.csv") != plan["rows_sha256"]:
        raise RuntimeError("Frozen input rows changed")
    for job in state["jobs"]:
        if digest(directory / job["file"]) != job["sha256"]:
            raise RuntimeError(f"Frozen requests changed: {job['file']}")
    return plan, state


def submit(client, directory, state, max_jobs):
    submitted = 0
    for job in state["jobs"]:
        if submitted >= max_jobs:
            break
        if job["status"] not in {"prepared", "uploaded", "creating"}:
            continue
        if job["status"] == "creating":
            matches = [item for item in client.batches.list(limit=100) if item.input_file_id == job["input_file_id"]]
            if len(matches) != 1:
                raise RuntimeError(f"Batch creation unresolved for {job['name']}. Found {len(matches)} matches. Query later before submitting again")
            batch = matches[0]
        else:
            if job["status"] == "prepared":
                with (directory / job["file"]).open("rb") as handle:
                    upload = client.files.create(file=handle, purpose="batch")
                job.update(status="uploaded", input_file_id=upload.id)
                save(directory / "state.json", state)
            job["status"] = "creating"
            save(directory / "state.json", state)
            batch = client.batches.create(input_file_id=job["input_file_id"], endpoint=ENDPOINT,
                     completion_window="24h", metadata={"run": directory.name[:128], "shard": job["name"]})
        job.update(status=batch.status, batch_id=batch.id)
        save(directory / "state.json", state)
        submitted += 1
    return submitted


def poll(client, directory, state):
    for job in state["jobs"]:
        if not job.get("batch_id") or job.get("collected"):
            continue
        batch = client.batches.retrieve(job["batch_id"])
        job["status"] = batch.status
        job["receipt"] = batch.model_dump(mode="json")
        save(directory / "state.json", state)


def download_output(client, directory, file_id, name):
    if not file_id:
        return []
    path = directory / "outputs" / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(client.files.content(file_id).content)
        temporary.replace(path)
    return read_jsonl(path)


def collect(client, directory, plan, state):
    from src.screening.abstract import parse_decision
    for job in state["jobs"]:
        if job.get("collected") or job["status"] not in TERMINAL:
            continue
        receipt = job["receipt"]
        rows = download_output(client, directory, receipt.get("output_file_id"), job["name"] + "_output.jsonl")
        rows += download_output(client, directory, receipt.get("error_file_id"), job["name"] + "_errors.jsonl")
        expected = {row["custom_id"] for row in read_jsonl(directory / job["file"])}
        seen, decisions, errors = set(), [], []
        for row in rows:
            key = row["custom_id"]
            if key not in expected or key in seen:
                raise RuntimeError(f"Unexpected/duplicate output ID {key}")
            seen.add(key)
            response = row.get("response") or {}
            if response.get("status_code") != 200 or row.get("error"):
                errors.append(row)
                continue
            body = response["body"]
            if body["model"] != plan["model"]:
                raise RuntimeError(f"Returned model differs for {key}: {body['model']}")
            choice = body["choices"][0]
            content = choice["message"].get("content") or ""
            decisions.append({"custom_id": key, "decision": parse_decision(content) if choice["finish_reason"] == "stop" else "UNCLEAR",
                              "raw_output": content, "model": body["model"], "response_id": body["id"],
                              "batch_id": job["batch_id"], "finish_reason": choice["finish_reason"], "usage": body.get("usage", {})})
        errors.extend({"custom_id": key, "error": {"code": "missing_output"}} for key in sorted(expected - seen))
        write_jsonl(directory / "outputs" / (job["name"] + "_decisions.jsonl"), decisions)
        write_jsonl(directory / "outputs" / (job["name"] + "_unresolved.jsonl"), errors)
        job.update(collected=True, decided=len(decisions), unresolved=len(errors))
        save(directory / "state.json", state)
    combined = []
    for job in state["jobs"]:
        if job.get("collected"):
            combined.extend(read_jsonl(directory / "outputs" / (job["name"] + "_decisions.jsonl")))
    mapping = {row["custom_id"]: row for row in combined}
    if len(mapping) != len(combined):
        raise RuntimeError("Duplicate successful decisions")
    write_jsonl(directory / "decisions.jsonl", combined)
    frame = pd.read_csv(directory / "rows.csv")
    prefix = "ai4epi_abstract"
    keys = [f"{plan['pathogen']}/{article}" for article in frame.article_id]
    frame[prefix + "_response"] = [mapping.get(key, {}).get("raw_output") for key in keys]
    frame[prefix + "_decision"] = [mapping.get(key, {}).get("decision") for key in keys]
    frame["to_ocr"] = frame[prefix + "_decision"].eq("INCLUDE")
    complete = len(mapping) == len(frame) and all(job.get("collected") and not job["unresolved"] for job in state["jobs"])
    name = "abstract_screening.csv" if complete else "partial_screening.csv"
    frame.to_csv(directory / name, index=False)
    return {"complete": complete, "decisions": len(mapping), "expected": len(frame), "output": name}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Freeze author-generated requests and operate the OpenAI Batch API.')
    commands = parser.add_subparsers(dest="action", required=True)
    prep = commands.add_parser("prepare", help="Freeze requests offline")
    add_screen_args(prep)
    prep.add_argument("--run-dir", type=Path, required=True)
    prep.add_argument("--shard-rows", type=int, default=1000)
    for name in ["submit", "status", "collect"]:
        sub = commands.add_parser(name)
        sub.add_argument("--run-dir", type=Path, required=True)
        if name == "submit":
            sub.add_argument("--max-jobs", type=int, default=1)
    args = parser.parse_args(argv)
    if args.action == "prepare":
        if args.shard_rows < 1:
            parser.error("--shard-rows must be positive")
        prepare(args)
        return 0
    plan, state = load_state(args.run_dir)
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("Set OPENAI_API_KEY to access Batch jobs")
    from openai import OpenAI
    with OpenAI(api_key=key, base_url="https://api.openai.com/v1", max_retries=0, timeout=120) as client:
        if args.action == "submit":
            if args.max_jobs < 1:
                parser.error("--max-jobs must be positive")
            submit(client, args.run_dir, state, args.max_jobs)
        else:
            poll(client, args.run_dir, state)
        result = collect(client, args.run_dir, plan, state) if args.action == "collect" else {"jobs": [{"name": j["name"], "status": j["status"]} for j in state["jobs"]]}
    save(args.run_dir / "progress.json", {"checked_utc": now(), **result})
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
