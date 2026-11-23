import argparse
import importlib.util
from pathlib import Path

from scripts.common import BASE, COHORTS, VENDOR, digest, now, save, verify_vendor


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fetch the pinned screening dataset, retaining the author's CSV conversion.")
    parser.add_argument("--pathogen", choices=[*COHORTS, "all"], default="all")
    parser.add_argument("--output-root", type=Path, default=BASE / "data")
    parser.add_argument("--hf-local-dir", type=Path, help="Read an existing pinned Hugging Face snapshot")
    args = parser.parse_args(argv)
    manifest = verify_vendor()
    pathogens = COHORTS if args.pathogen == "all" else [args.pathogen]
    if args.hf_local_dir:
        local = args.hf_local_dir
    else:
        from huggingface_hub import snapshot_download
        local = Path(snapshot_download(repo_id=manifest["dataset_id"], repo_type="dataset",
                     revision=manifest["dataset_revision"], allow_patterns=[
                         f"data/harvest_metadata_and_screening/{p}.parquet" for p in pathogens]))
    spec = importlib.util.spec_from_file_location("author_dataset", VENDOR / "scripts/data/download_hf_dataset.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    receipt = {"created_utc": now(), "dataset_id": manifest["dataset_id"],
               "requested_revision": manifest["dataset_revision"],
               "source": "provided_local_snapshot" if args.hf_local_dir else "pinned_huggingface_download", "cohorts": {}}
    for pathogen in pathogens:
        relative = f"harvest_metadata_and_screening/{pathogen}.parquet"
        frame = module.download_parquet(manifest["dataset_id"], relative, local)
        module.write_harvest_and_screening(pathogen, frame, args.output_root)
        receipt["cohorts"][pathogen] = {"rows": len(frame), "parquet_sha256": digest(local / "data" / relative)}
        print(f"{pathogen} {len(frame)} rows")
    save(args.output_root / "dataset_receipt.json", receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
