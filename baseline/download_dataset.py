import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd
from huggingface_hub import snapshot_download

BASE = Path(__file__).resolve().parent
REPO_ID = "OxRML/AgentSLR"
REVISION = "01843a781bbd839c434c50271a7b1456a59cd656"
PATHOGENS = {"marburg": "Marburg", "ebola": "Ebola", "lassa": "Lassa", "sars": "SARS", "zika": "Zika", "mers": "MERS", "nipah": "Nipah"}


def write_harvest_and_screening(pathogen, dataframe, output_root):
    harvest = dataframe.copy()
    if "Covidence #" not in harvest.columns and "covidence_id" in harvest.columns:
        harvest["Covidence #"] = harvest["covidence_id"]
    if "downloaded" in harvest.columns:
        harvest["downloaded"] = False
    for column in ["downloaded_path", "download_source", "download_error"]:
        if column in harvest.columns:
            harvest[column] = pd.NA
    harvest_path = output_root / "agentslr/harvests" / pathogen / "harvest_metadata.csv"
    harvest_path.parent.mkdir(parents=True, exist_ok=True)
    harvest.to_csv(harvest_path, index=False)
    columns = {"Title": "title", "Authors": "authors", "Abstract": "abstract", "Published Year": "year", "Journal": "journal",
               "DOI": "doi", "Covidence #": "covidence_id", "perg_abstract_result": "perg_abstract_result", "perg_fulltext_result": "perg_fulltext_result"}
    labels = pd.DataFrame({target: dataframe.get(source, pd.Series(dtype=object)) for target, source in columns.items()})
    label_path = output_root / "perg/screening" / f"{PATHOGENS[pathogen]}_filtered.csv"
    label_path.parent.mkdir(parents=True, exist_ok=True)
    labels.to_csv(label_path, index=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Download the pinned screening dataset and human labels.")
    parser.add_argument("--pathogen", choices=[*PATHOGENS, "all"], default="all")
    parser.add_argument("--output-root", type=Path, default=BASE / "data")
    parser.add_argument("--hf-local-dir", type=Path)
    args = parser.parse_args(argv)
    pathogens = list(PATHOGENS) if args.pathogen == "all" else [args.pathogen]
    local = args.hf_local_dir or Path(snapshot_download(repo_id=REPO_ID, repo_type="dataset", revision=REVISION,
                 allow_patterns=[f"data/harvest_metadata_and_screening/{pathogen}.parquet" for pathogen in pathogens]))
    receipt = {"created_utc": datetime.now(timezone.utc).isoformat(), "dataset_id": REPO_ID, "requested_revision": REVISION,
               "source": "provided_local_snapshot" if args.hf_local_dir else "pinned_huggingface_download", "cohorts": {}}
    for pathogen in pathogens:
        source = local / "data/harvest_metadata_and_screening" / f"{pathogen}.parquet"
        frame = pd.read_parquet(source)
        write_harvest_and_screening(pathogen, frame, args.output_root)
        receipt["cohorts"][pathogen] = {"rows": len(frame), "parquet_sha256": sha256(source.read_bytes()).hexdigest()}
        print(f"{pathogen} {len(frame)} rows")
    args.output_root.mkdir(parents=True, exist_ok=True)
    receipt_path = args.output_root / "dataset_receipt.json"
    temporary = receipt_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, indent=2) + "\n")
    temporary.replace(receipt_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
