import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re

import pandas as pd
from tqdm import tqdm

BASE = Path(__file__).resolve().parent
ENDPOINT = "/v1/chat/completions"
SOURCE_COMMIT = "3111fcf456c6fbcba768dfdce2bd2934d8cb5cef"
DATASET_REVISION = "01843a781bbd839c434c50271a7b1456a59cd656"
BATCH_SIZE = 64
CONCURRENCY = 8
PATHOGENS = {
    "marburg": "Marburg virus",
    "ebola": "Ebola virus",
    "lassa": "Lassa fever or Lassa mammarenavirus",
    "sars": "Severe Acute Respiratory Syndrome coronavirus (SARS-CoV)",
    "zika": "Zika virus",
    "mers": "Middle East respiratory syndrome coronavirus (MERS-CoV)",
    "nipah": "Henipa virus (Nipah virus, Hendra virus)",
}
PROMPT_HASHES = {
    "abstract_screening.md": "18a37aa4d9a42007bb832cd5adebd4878efc31603a323bd267b60bdc217160bc",
    "study_objectives.md": "b6397acf5cdff664e9a00b8b41092bf7431b67fa13717d0b181c84d9f39075bb",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def check_prompts():
    for name, expected in PROMPT_HASHES.items():
        if digest(BASE / "prompts" / name) != expected:
            raise ValueError(f"Original prompt changed: {name}")


def parse_decision(response):
    matches = re.findall(r"<decision>\s*(include|exclude|unclear)\s*</decision>", str(response), re.IGNORECASE)
    return matches[0].upper() if len(matches) == 1 else "UNCLEAR"


def prepare_requests(args):
    check_prompts()
    frame = pd.read_csv(args.input)
    if not {"article_id", "title", "abstract"} <= set(frame.columns):
        raise ValueError("Input requires article_id, title and abstract columns")
    if frame.article_id.isna().any() or not frame.article_id.is_unique:
        raise ValueError("article_id must be present and unique")
    if args.sample_size is not None:
        if args.sample_size < 1:
            raise ValueError("sample-size must be positive")
        if len(frame) > args.sample_size:
            frame = frame.sample(n=args.sample_size, random_state=7).sort_index()
    if frame.empty:
        raise ValueError("No articles to screen")
    pathogen = PATHOGENS[args.pathogen]
    objectives = (BASE / "prompts/study_objectives.md").read_text(encoding="utf-8").replace("__PATHOGEN_NAME__", pathogen)
    template = (BASE / "prompts/abstract_screening.md").read_text(encoding="utf-8").replace("__PATHOGEN_NAME__", pathogen).replace("__STUDY_OBJECTIVES__", objectives)
    requests = []
    for article, title, abstract in zip(frame.article_id, frame.title.fillna(""), frame.abstract.fillna("")):
        body = {"model": args.model, "messages": [{"role": "user", "content": template.format(title=title, abstract=abstract)}],
                "reasoning_effort": "high", "max_completion_tokens": 65536}
        requests.append({"custom_id": f"{args.pathogen}/{article}", "method": "POST", "url": ENDPOINT, "body": body})
    columns = ["ai4epi_abstract_response", "ai4epi_abstract_decision", "to_ocr", "screening_timestamp"]
    return requests, frame.drop(columns=columns, errors="ignore")


def add_screen_args(parser):
    parser.add_argument("--pathogen", choices=PATHOGENS, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--model", choices=["gpt-6.1-terra", "gpt-5.2-2025-12-11"], default="gpt-6.1-terra")
    parser.add_argument("--sample-size", type=int)


def response_text(content):
    if isinstance(content, str):
        return content.strip()
    parts = []
    for part in content or []:
        if isinstance(part, dict) and part.get("type") == "text":
            parts.append(part.get("text", ""))
        elif getattr(part, "type", None) == "text":
            parts.append(getattr(part, "text", ""))
    return "".join(parts).strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Screen article titles and abstracts.")
    add_screen_args(parser)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    requests, frame = prepare_requests(args)
    key = os.environ.get("OPENAI_API_KEY")
    if not args.dry_run and not key:
        parser.error("Set OPENAI_API_KEY before screening")
    output = args.output or BASE / "outputs" / args.model / args.pathogen
    if output.exists() and any(output.iterdir()):
        parser.error("Output directory must be empty. Use a new directory for a new run")
    output.mkdir(parents=True, exist_ok=True)
    write_jsonl(output / "requests.jsonl", requests)
    save(output / "preflight.json", {"created_utc": now(), "stage": "abstract_screen", "pathogen": args.pathogen,
         "model": args.model, "input_sha256": digest(args.input), "requests": len(requests),
         "requests_sha256": digest(output / "requests.jsonl"), "upstream_commit": SOURCE_COMMIT,
         "dataset_revision": DATASET_REVISION, "prompt_sha256": PROMPT_HASHES,
         "stage_batch_size": BATCH_SIZE, "concurrency": CONCURRENCY, "hit_async": False, "model_calls": 0})
    if args.dry_run:
        print(json.dumps({"requests": len(requests), "output": str(output), "model_calls": 0}))
        return 0
    from openai import OpenAI
    for column in ["ai4epi_abstract_response", "ai4epi_abstract_decision", "screening_timestamp"]:
        frame[column] = pd.Series(None, index=frame.index, dtype=object)
    frame["to_ocr"] = False
    completed = 0
    with OpenAI(api_key=key, base_url="https://api.openai.com/v1") as client, (output / "responses.jsonl").open("w", encoding="utf-8") as traces:
        try:
            for start in tqdm(range(0, len(requests), BATCH_SIZE), desc="Screening papers"):
                for position in range(start, min(start + BATCH_SIZE, len(requests))):
                    request = requests[position]
                    response = client.chat.completions.create(**request["body"])
                    text = response_text(response.choices[0].message.content)
                    decision = parse_decision(text)
                    index = frame.index[position]
                    frame.at[index, "ai4epi_abstract_response"] = text
                    frame.at[index, "ai4epi_abstract_decision"] = decision
                    frame.at[index, "to_ocr"] = decision == "INCLUDE"
                    frame.at[index, "screening_timestamp"] = now()
                    traces.write(json.dumps({"custom_id": request["custom_id"], "body": request["body"],
                                 "response": response.model_dump(mode="json")}, ensure_ascii=False) + "\n")
                    traces.flush()
                    completed += 1
                name = "abstract_screening.csv" if completed == len(frame) else "partial_screening.csv"
                frame.drop(columns="markdown_content", errors="ignore").to_csv(output / name, index=False)
        except Exception:
            frame.drop(columns="markdown_content", errors="ignore").to_csv(output / "partial_screening.csv", index=False)
            raise
    (output / "partial_screening.csv").unlink(missing_ok=True)
    print(json.dumps({"status": "complete", "rows": completed, "output": str(output / "abstract_screening.csv")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
