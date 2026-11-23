# Screening baseline

Four scripts cover dataset download, title/abstract screening, Batch inference, and PDF collection.

```text
baseline/
  batch.py
  screen.py
  download_dataset.py
  collect_pdfs.py
  prompts/
    abstract_screening.md
    study_objectives.md
  requirements.txt
  README.md
  .gitignore
```

`batch.py` shares request construction and decision parsing with `screen.py`. The other scripts run directly.

## Setup

Use Python 3.12. Run the following commands from the repository root.

```bash
cd baseline
uv venv --python 3.12 .venv
uv pip sync --python .venv/bin/python requirements.txt
```

Alternatively, create a virtual environment with `python -m venv .venv` and install `requirements.txt` with `pip`. Set `OPENAI_API_KEY` in the terminal or IDE environment before inference or Batch access. `OPENALEX_API_KEY` is optional for metadata lookup. Data, PDFs, responses, and local credentials are ignored by Git.

## Download the dataset

```bash
.venv/bin/python download_dataset.py
```

The script downloads the seven screening cohorts from [OxRML/AgentSLR](https://huggingface.co/datasets/OxRML/AgentSLR/tree/01843a781bbd839c434c50271a7b1456a59cd656), pinned to revision `01843a781bbd839c434c50271a7b1456a59cd656`. It preserves the original metadata and human-label conversion.

Outputs are `data/agentslr/harvests/<pathogen>/harvest_metadata.csv`, `data/perg/screening/<Pathogen>_filtered.csv`, and `data/dataset_receipt.json`. Use `--pathogen lassa` for one cohort, `--output-root` for another destination, or `--hf-local-dir` for an existing Hugging Face snapshot. Local snapshot mode records source hashes without asserting its revision.

## Screen titles and abstracts

Export requests offline first.

```bash
.venv/bin/python screen.py --pathogen lassa \
  --input data/agentslr/harvests/lassa/harvest_metadata.csv \
  --sample-size 10 --dry-run --output outputs/preflight/lassa
```

Run standard inference with a new output directory.

```bash
.venv/bin/python screen.py --pathogen lassa \
  --input data/agentslr/harvests/lassa/harvest_metadata.csv \
  --model gpt-6.1-terra --output outputs/standard/lassa
```

The script handles title and abstract screening only. Supported cohorts are `marburg`, `ebola`, `lassa`, `sars`, `zika`, `mers`, and `nipah`. The default model is `gpt-6.1-terra`; use `--model gpt-5.2-2025-12-11` for comparison. `--sample-size` uses deterministic seed 7.

Each run requires an empty output directory. Preflight saves `requests.jsonl` and `preflight.json` with input, request, and prompt hashes. Standard inference also writes `responses.jsonl` and `abstract_screening.csv`. Interrupted runs retain `partial_screening.csv` and the responses already received.

The request profile comes from [AgentSLR commit 3111fcf](https://github.com/OxRML/AgentSLR/tree/3111fcf456c6fbcba768dfdce2bd2934d8cb5cef). Both prompt files are preserved byte for byte and checked against their original hashes.

| Setting | Value |
|---|---|
| Messages | One user message with original objectives, criteria, title, and abstract |
| API | Chat Completions |
| reasoning_effort | high |
| max_completion_tokens | 65536 |
| temperature, top_p, API seed | Omitted |
| Stage batch / configured concurrency | 64 / 8 |
| Execution | Synchronous, matching the original `hit_async=False` default |

## Run Batch inference

```bash
.venv/bin/python batch.py prepare --pathogen lassa \
  --input data/agentslr/harvests/lassa/harvest_metadata.csv \
  --model gpt-6.1-terra --run-dir outputs/abstract/lassa
.venv/bin/python batch.py submit --run-dir outputs/abstract/lassa
.venv/bin/python batch.py status --run-dir outputs/abstract/lassa
.venv/bin/python batch.py collect --run-dir outputs/abstract/lassa
```

`prepare` is offline and freezes rows, requests, and hashes. `--shard-rows` defaults to 1,000 independent article requests per upload file. Stage batching and Batch API upload shards are separate settings. Batch uses the same per-article request bodies as `screen.py`, with different transport and scheduling.

`submit` submits one pending shard by default. Repeat it for additional shards or set `--max-jobs N`. Submitted shards are skipped. `status` and `collect` do not create inference jobs. After ambiguous creation, submission reconciles the uploaded file ID before proceeding. Unresolved creation stops submission.

Repeat status and collection until complete. The final output is `abstract_screening.csv` plus `decisions.jsonl`, including raw text, returned model, response ID, Batch ID, finish reason, and token usage. Incomplete runs produce `partial_screening.csv`; failed or missing rows are recorded in `outputs/*_unresolved.jsonl`. Inspect them before preparing a separate retry corpus.

Use a separate directory for each model and cohort. For PDF collection, place each cohort's complete results at `outputs/abstract/<pathogen>/abstract_screening.csv`.

## Collect PDFs

```bash
.venv/bin/python collect_pdfs.py --input-dir outputs/abstract \
  --output-dir outputs/pdfs --workers 8
```

Input accepts the layout above or one `<pathogen>.csv` file per cohort. `article_id` and `title` are required. DOI, PMID, PMCID, year, and authors help retrieval. If abstract decisions are present, only `INCLUDE` rows are selected.

The collector reuses existing PDFs, checks Europe PMC identity records and PMC open-data files, and tries OpenAlex open-access locations and publisher or repository links. It validates article identity, page loading, available repository checksums, and SHA-256 hashes.

| Output | Contents |
|---|---|
| `ocr_ready_pdfs.csv` | Verified files, paths, hashes, and source evidence |
| `missing_pdfs.csv` | Missing articles and the latest failure reason |
| `pdf_search_queue.csv` | Article metadata and web-search queries |
| `pdf_access_manifest.csv` | Ready and missing records |
| `records/<pathogen>/<article_id>.json` | Detailed attempts and provenance |
| `<pathogen>_ocr_input.csv` | Original metadata plus downloaded paths |
| `progress.json` | Counts, pages, bytes, and status |

`--data-dir` can reuse files at `harvests/<pathogen>/pdfs/<article_id>.pdf`. `--offline` only uses local files. `--retry-missing` retries automatic retrieval. Changing the selected input files requires a new output directory because the corpus is frozen by hash.

## Find missing PDFs with AI web search

Give an AI with web access `pdf_search_queue.csv` and the corresponding access records. Work in groups of ten articles, preserving `pathogen` and `article_id`. Search the exact title in quotes, the DOI, and the title with `filetype:pdf`. Check publisher, PMC, Europe PMC, university, funder, and author repositories. Verify title, DOI, authors, and year; prefer the published version and record manuscript or preprint versions explicitly.

Save verified discoveries to `outputs/pdf_supplements.csv` using these columns.

```text
pathogen,article_id,pdf_url,local_path,evidence_url,version
```

Each row requires an existing article ID, an HTTPS `evidence_url`, and either an HTTPS `pdf_url` or a `local_path`. Relative local paths are resolved against the CSV directory. Multiple candidates per article are allowed. Keep API keys in environment variables rather than source URLs.

Import discoveries with the same input and output directories.

```bash
.venv/bin/python collect_pdfs.py --input-dir outputs/abstract \
  --output-dir outputs/pdfs --supplements outputs/pdf_supplements.csv --workers 8
```

For files already downloaded by the AI, add `--offline`. Existing verified files are reused; new sources for missing articles pass the same validation. The collector refreshes the manifests and search queue. Wrong articles, damaged files, and unverified scanned PDFs remain unresolved. Keep their search evidence and failure reasons.

The collector does not invoke an AI model; web discovery is a separate agent task. OpenAlex cached full text uses a separate authenticated, metered content API. Check account access and usage before retrieving it. Source documentation is available for [PMC datasets](https://pmc.ncbi.nlm.nih.gov/tools/textmining/), [PMC cloud access](https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/), [Europe PMC](https://dev.europepmc.org/RestfulWebService), and [OpenAlex full text](https://help.openalex.org/access/fulltext/).
