from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parents[1]
VENDOR = BASE / "vendor/agentslr"
sys.path.insert(0, str(VENDOR))
COHORTS = ("marburg", "ebola", "lassa", "sars", "zika", "mers", "nipah")
ENDPOINT = "/v1/chat/completions"


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


def verify_vendor():
    manifest = read_json(BASE / "source_manifest.json")
    for name, expected in manifest["vendor_files_sha256"].items():
        if digest(VENDOR / name) != expected:
            raise RuntimeError(f"Upstream file changed: {name}")
    for name, metadata in manifest.get("local_tools", {}).items():
        if digest(BASE / name) != metadata["packaged_sha256"]:
            raise RuntimeError(f"Local tool changed: {name}")
    return manifest


def author_config(stage, argv, *, offline=False):
    from args import parse_args
    from config import Config
    config = Config(parse_args(["--stage", stage, *argv]))
    if offline:
        config.api_key = "offline-fixture"
    return config


def dispatch(config):
    if config.stage != "abstract_screen":
        raise ValueError("Only title and abstract screening is supported")
    from src.screening.abstract import run_abstract_screening
    return run_abstract_screening(config, None)
