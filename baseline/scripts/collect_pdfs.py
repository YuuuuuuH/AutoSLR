
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
from difflib import SequenceMatcher
from hashlib import md5, sha256
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import threading
from urllib.parse import parse_qs, quote, urljoin, urlparse
import xml.etree.ElementTree as ET

import pandas as pd
import pymupdf
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from scripts.common import BASE, digest, now, read_json, save

ROOT = BASE
RUN = BASE / "outputs/pdfs"
OFFLINE = False
RETRY_MISSING = False
SUPPLEMENTS = {}

LOCAL = threading.local()
S3 = "https://pmc-oa-opendata.s3.amazonaws.com/"
NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}


def session():
    if not hasattr(LOCAL, "session"):
        client = requests.Session()
        client.headers["User-Agent"] = "AgentSLR/1.0 (scholarly research tool)"
        client.mount("https://", HTTPAdapter(max_retries=Retry(total=0)))
        LOCAL.session = client
    return LOCAL.session


def normalize(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def doi(value):
    return re.sub(r"^https?://(dx\.)?doi.org/", "", str(value).strip().lower())


def identity(record, metadata):
    if record.get("doi") and metadata.get("doi"):
        return doi(record["doi"]) == doi(metadata["doi"])
    target, actual = normalize(record["title"]), normalize(metadata.get("title", ""))
    return bool(actual) and SequenceMatcher(None, target, actual).ratio() >= .90


def inspect_pdf(path, record, metadata_match=False):
    try:
        with pymupdf.open(path) as pdf:
            if not pdf.is_pdf or not pdf.page_count or pdf.needs_pass:
                return None
            pages = pdf.page_count
            for index in range(pages):
                pdf.load_page(index)
            text = " ".join(pdf[index].get_text() for index in range(min(3, pages)))
        target = normalize(record["title"])
        actual = normalize(text)
        title_match = target in actual
        if not title_match and len(target) > 25:
            longest = SequenceMatcher(None, target, actual[:10000], autojunk=False).find_longest_match()
            title_match = longest.size >= .8 * len(target)
        doi_match = bool(record.get("doi")) and normalize(doi(record["doi"])) in actual
        if not metadata_match and not title_match and not doi_match:
            return None
        return {"pages": pages, "bytes": path.stat().st_size, "sha256": sha256(path.read_bytes()).hexdigest(),
                "identity_check": "repository_metadata" if metadata_match else "doi_in_pdf" if doi_match else "title_in_pdf"}
    except Exception:
        return None


class PDFMeta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == "meta" and data.get("name", "").lower() in {"citation_pdf_url", "eprints.document_url"}:
            if data.get("content"):
                self.urls.append(data["content"])


def download(url, destination, record, metadata_match=False, checksum=None):
    temporary = destination.with_suffix(".download")
    try:
        with session().get(url, stream=True, timeout=(6, 25)) as response:
            response.raise_for_status()
            with temporary.open("wb") as handle:
                size = 0
                for block in response.iter_content(65536):
                    if not size and b"%PDF" not in block[:1024]:
                        return None, "returned_non_pdf"
                    size += len(block)
                    if size > 500 * 1024 * 1024:
                        return None, "oversize"
                    handle.write(block)
        if checksum and md5(temporary.read_bytes()).hexdigest() != checksum:
            return None, "checksum_mismatch"
        details = inspect_pdf(temporary, record, metadata_match)
        if details is None:
            return None, "identity_or_structure_unverified"
        temporary.replace(destination)
        return details, None
    except requests.RequestException as error:
        return None, f"HTTP_{error.response.status_code}" if error.response is not None else type(error).__name__
    finally:
        temporary.unlink(missing_ok=True)


def recover(record):
    cohort, article = record["pathogen"], record["article_id"]
    candidates = SUPPLEMENTS.get((cohort, article), [])
    candidate_hash = sha256(json.dumps(candidates, sort_keys=True).encode()).hexdigest()
    fingerprint = sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    saved = RUN / "records" / cohort / f"{article}.json"
    if saved.exists():
        existing = json.loads(saved.read_text(encoding="utf-8"))
        if existing.get("input_sha256") == fingerprint:
            if existing["status"] == "ready" and Path(existing["path"]).is_file() and digest(existing["path"]) == existing["sha256"]:
                return existing
            if existing["status"] == "missing" and not RETRY_MISSING and not OFFLINE and (
                not candidates or existing.get("supplements_sha256") == candidate_hash
            ):
                return existing
    result = {"pathogen": cohort, "article_id": article, "title": record["title"], "doi": record.get("doi", ""),
              "pmid": record.get("pmid", ""), "pmcid": record.get("pmcid", ""),
              "openalex_id": record.get("openalex_id", ""),
              "status": "missing", "attempts": [], "input_sha256": fingerprint,
              "supplements_sha256": candidate_hash}
    canonical = ROOT / "harvests" / cohort / "pdfs" / f"{article}.pdf"
    destination = RUN / "pdfs" / cohort / f"{article}.pdf"
    for path in [destination, canonical]:
        if path.is_file():
            details = inspect_pdf(path, record)
            if details:
                if path != destination:
                    shutil.copy2(path, destination)
                result.update(status="ready", path=str(destination), source="existing_pdf", **details)
                save(saved, result)
                return result
            result["attempts"].append({"source": "existing_pdf", "result": "identity_or_structure_unverified"})

    def accept(url, source, verified=False, checksum=None):
        details, error = download(url, destination, record, verified, checksum)
        result["attempts"].append({"source": source, "url": url, "result": error or "ready"})
        if details:
            result.update(status="ready", path=str(destination), source=source, source_url=url, **details)
            return True
        return False

    for candidate in candidates:
        if candidate.get("local_path"):
            local = Path(candidate["local_path"])
            details = inspect_pdf(local, record)
            result["attempts"].append({"source": "supplement_local", "path": str(local),
                                       "result": "ready" if details else "identity_or_structure_unverified"})
            if details:
                if local.resolve() != destination.resolve():
                    shutil.copy2(local, destination)
                result.update(status="ready", path=str(destination), source="supplement_local",
                              evidence_url=candidate["evidence_url"], version=candidate.get("version", ""), **details)
                save(saved, result)
                return result
        if not OFFLINE and candidate.get("pdf_url") and accept(candidate["pdf_url"], "supplement_url"):
            result.update(evidence_url=candidate["evidence_url"], version=candidate.get("version", ""))
            save(saved, result)
            return result

    if OFFLINE:
        return result

    try:
        query = f'DOI:"{record["doi"]}"' if record.get("doi") else f'EXT_ID:{record.get("pmid")} SRC:MED'
        response = session().get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                                 params={"query": query, "format": "json", "resultType": "core", "pageSize": 5}, timeout=(6, 20))
        response.raise_for_status()
        matches = [item for item in response.json().get("resultList", {}).get("result", []) if identity(record, item)]
        pmcids = list(dict.fromkeys(item["pmcid"] for item in matches if item.get("pmcid")))
        for pmcid in pmcids:
            response = session().get(S3, params={"list-type": "2", "prefix": pmcid + ".", "max-keys": 250}, timeout=(6, 20))
            response.raise_for_status()
            keys = [node.text for node in ET.fromstring(response.content).findall("s:Contents/s:Key", NS)]
            metadata = []
            for key in keys:
                if key.endswith(".json") and key.split("/")[-1].startswith(pmcid + "."):
                    reply = session().get(S3 + key, timeout=(6, 20))
                    reply.raise_for_status()
                    data = reply.json()
                    if data.get("pdf_url") and identity(record, data):
                        metadata.append(data)
            metadata.sort(key=lambda item: (bool(item.get("is_manuscript")), -int(item.get("version", 1))))
            for data in metadata:
                origin = data["pdf_url"].replace("s3://pmc-oa-opendata/", S3)
                checksum = (parse_qs(urlparse(origin).query).get("md5") or [None])[0]
                if accept(origin, "PMC_open_data", True, checksum):
                    result["pmcid"] = pmcid
                    result["license"] = data.get("license_code")
                    result["repository_version"] = data.get("version")
                    save(saved, result)
                    return result
    except (requests.RequestException, ValueError, ET.ParseError) as error:
        result["attempts"].append({"source": "PMC_open_data", "result": type(error).__name__})

    try:
        if record.get("doi"):
            lookup = "https://api.openalex.org/works/https://doi.org/" + quote(record["doi"], safe="")
        elif record.get("openalex_id"):
            lookup = "https://api.openalex.org/works/" + record["openalex_id"].rsplit("/", 1)[-1]
        else:
            raise ValueError("no_openalex_identifier")
        api_key = os.environ.get("OPENALEX_API_KEY")
        response = session().get(lookup, params={"api_key": api_key} if api_key else {}, timeout=(6, 20))
        response.raise_for_status()
        metadata = response.json()
        if not identity(record, {"doi": metadata.get("doi"), "title": metadata.get("display_name")}):
            raise ValueError("openalex_identity_mismatch")
        locations = [metadata.get("best_oa_location") or {}, *metadata.get("locations", [])]
        urls, landing_pages = [], []
        for location in locations:
            if location.get("is_oa"):
                if location.get("pdf_url"):
                    urls.append(location["pdf_url"])
                elif location.get("landing_page_url"):
                    landing_pages.append(location["landing_page_url"])
        for url in dict.fromkeys(urls):
            if urlparse(url).hostname in {"pmc.ncbi.nlm.nih.gov", "www.ncbi.nlm.nih.gov", "europepmc.org"}:
                continue
            if accept(url, "OpenAlex_OA_location"):
                save(saved, result)
                return result
        for url in list(dict.fromkeys(landing_pages))[:3]:
            if urlparse(url).hostname in {"pmc.ncbi.nlm.nih.gov", "www.ncbi.nlm.nih.gov", "europepmc.org"}:
                continue
            try:
                with session().get(url, timeout=(6, 15)) as page:
                    page.raise_for_status()
                    parser = PDFMeta()
                    parser.feed(page.text[:2_000_000])
                    for candidate in parser.urls[:2]:
                        if accept(urljoin(page.url, candidate), "OA_publisher_or_repository"):
                            save(saved, result)
                            return result
            except requests.RequestException:
                continue
    except (requests.RequestException, ValueError, AssertionError) as error:
        result["attempts"].append({"source": "OpenAlex_OA_location", "result": type(error).__name__})
    save(saved, result)
    return result


def load_supplements(path, jobs):
    if path is None:
        return {}
    frame = pd.read_csv(path, dtype=str).fillna("")
    if not {"pathogen", "article_id", "pdf_url", "local_path", "evidence_url"} <= set(frame.columns):
        raise ValueError("Supplement CSV requires pathogen, article_id, pdf_url, local_path and evidence_url")
    known = {(row["pathogen"], row["article_id"]) for row in jobs}
    supplements = {}
    for row in frame.to_dict("records"):
        row = {key: value.strip() for key, value in row.items()}
        key = (row["pathogen"], row["article_id"])
        if key not in known:
            raise ValueError(f"Supplement ID is outside the input corpus: {key}")
        if not row["pdf_url"] and not row["local_path"]:
            raise ValueError(f"Supplement requires a PDF URL or local file: {key}")
        if row["pdf_url"] and urlparse(row["pdf_url"]).scheme != "https":
            raise ValueError(f"Supplement PDF URL must use HTTPS: {key}")
        if urlparse(row["evidence_url"]).scheme != "https":
            raise ValueError(f"Supplement evidence URL must use HTTPS: {key}")
        if row["local_path"]:
            local = Path(row["local_path"]).expanduser()
            if not local.is_absolute():
                local = path.parent / local
            if not local.is_file():
                raise ValueError(f"Supplement local PDF is missing: {local}")
            row["local_path"] = str(local.resolve())
        supplements.setdefault(key, []).append(row)
    return supplements


def main(argv=None):
    global ROOT, RUN, OFFLINE, RETRY_MISSING, SUPPLEMENTS
    parser = argparse.ArgumentParser(description='Recover missing OA PDFs and build a checked manifest for OCR.')
    parser.add_argument("--input-dir", type=Path, required=True, help="One CSV per pathogen with article_id, title and optional DOI metadata")
    parser.add_argument("--output-dir", type=Path, default=BASE / "outputs/pdfs")
    parser.add_argument("--data-dir", type=Path, default=BASE / "data/agentslr", help="Reuse existing PDFs from harvests/<pathogen>/pdfs")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--retry-missing", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Validate and reuse existing PDFs without network access")
    parser.add_argument("--supplements", type=Path, help="CSV containing PDF URLs or local files found by an AI web search")
    arguments = parser.parse_args(argv)
    if arguments.workers < 1:
        parser.error("--workers must be positive")
    ROOT, RUN = arguments.data_dir.resolve(), arguments.output_dir.resolve()
    OFFLINE, RETRY_MISSING = arguments.offline, arguments.retry_missing
    paths = sorted(arguments.input_dir.glob("*.csv"))
    if not paths:
        paths = sorted(arguments.input_dir.glob("*/abstract_screening.csv"))
    if not paths:
        parser.error("No cohort CSV files found")
    hashes = {str(path.relative_to(arguments.input_dir)): digest(path) for path in paths}
    manifest_path = RUN / "input_manifest.json"
    if manifest_path.exists() and read_json(manifest_path)["inputs_sha256"] != hashes:
        raise SystemExit("Input files changed. Use a new output directory to preserve the previous run")
    jobs = []
    for path in paths:
        cohort = path.parent.name if path.name == "abstract_screening.csv" else path.stem
        source = pd.read_csv(path, dtype=str).fillna("")
        if not {"article_id", "title"} <= set(source.columns):
            parser.error(f"{path} requires article_id and title")
        if "ai4epi_abstract_decision" in source:
            source = source.loc[source.ai4epi_abstract_decision.eq("INCLUDE")].copy()
        source["pathogen"] = cohort
        for row in source.to_dict("records"):
            if not re.fullmatch(r"[A-Za-z0-9_-]+", row["article_id"]) or not re.fullmatch(r"[a-z]+", row["pathogen"]):
                parser.error("Unsafe pathogen/article_id in input")
            row["doi"] = doi(row.get("doi", ""))
            row["pmid"] = re.sub(r"\.0$", "", row.get("pmid", ""))
            jobs.append(row)
        (RUN / "records" / cohort).mkdir(parents=True, exist_ok=True)
        (RUN / "pdfs" / cohort).mkdir(parents=True, exist_ok=True)
    if len(jobs) != len({(row["pathogen"], row["article_id"]) for row in jobs}):
        parser.error("Duplicate article IDs within a cohort")
    try:
        SUPPLEMENTS = load_supplements(arguments.supplements, jobs)
    except ValueError as error:
        parser.error(str(error))
    save(manifest_path, {"created_utc": now(), "inputs_sha256": hashes, "selected_rows": len(jobs)})
    records = []
    with ThreadPoolExecutor(max_workers=arguments.workers) as workers:
        for future in as_completed([workers.submit(recover, row) for row in jobs]):
            records.append(future.result())
            if len(records) % 10 == 0:
                save(RUN / "progress.json", {"status": "checking" if OFFLINE else "recovering", "checked": len(records),
                     "ready": sum(row["status"] == "ready" for row in records), "target": len(jobs)})
    records.sort(key=lambda row: (row["pathogen"], row["article_id"]))
    ready = [row for row in records if row["status"] == "ready"]
    missing = [row for row in records if row["status"] != "ready"]
    for row in ready:
        if not Path(row["path"]).is_file() or digest(row["path"]) != row["sha256"]:
            raise RuntimeError(f"PDF changed during collection: {row['article_id']}")
    for row in missing:
        row["missing_reason"] = (row.get("attempts") or [{}])[-1].get("result", "no_verified_pdf")
    columns = ["pathogen", "article_id", "title", "doi", "pmid", "pmcid", "openalex_id", "status", "path",
               "pages", "sha256", "source", "source_url", "evidence_url", "version", "missing_reason"]
    for name, values in [("pdf_access_manifest.csv", records), ("ocr_ready_pdfs.csv", ready), ("missing_pdfs.csv", missing)]:
        frame = pd.DataFrame(values).reindex(columns=columns)
        frame.to_csv(RUN / name, index=False)
    missing_by_id = {(row["pathogen"], row["article_id"]): row for row in missing}
    queue = []
    for item in jobs:
        absent = missing_by_id.get((item["pathogen"], item["article_id"]))
        if absent:
            queue.append({key: item.get(key, "") for key in ["pathogen", "article_id", "title", "doi", "pmid", "pmcid", "year", "authors"]} |
                         {"missing_reason": absent["missing_reason"],
                          "search_query": f'"{item["title"]}" {item["doi"]} filetype:pdf'})
    queue_columns = ["pathogen", "article_id", "title", "doi", "pmid", "pmcid", "year", "authors", "missing_reason", "search_query"]
    pd.DataFrame(queue, columns=queue_columns).to_csv(RUN / "pdf_search_queue.csv", index=False)
    ready_by_id = {(row["pathogen"], row["article_id"]): row for row in ready}
    for cohort in sorted({row["pathogen"] for row in jobs}):
        rows = []
        for item in jobs:
            found = ready_by_id.get((cohort, item["article_id"])) if item["pathogen"] == cohort else None
            if found:
                rows.append({**item, "downloaded": True, "downloaded_path": found["path"]})
        frame = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["article_id", "title", "downloaded", "downloaded_path"])
        frame.to_csv(RUN / f"{cohort}_ocr_input.csv", index=False)
    summary = {"status": "complete", "checked": len(records), "ready": len(ready), "missing": len(missing),
               "pages": sum(row["pages"] for row in ready), "bytes": sum(row["bytes"] for row in ready),
               "finished_utc": now(), "offline": OFFLINE, "model_calls": 0}
    save(RUN / "progress.json", summary)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
