import argparse
import copy
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import pymupdf

from scripts.common import BASE, digest, save, verify_vendor, write_jsonl
from scripts.screen import capture_requests, main as screen_main, screening_config


def check_requests(directory):
    from src.screening.prompts import get_prompt
    source = directory / "abstract.csv"
    pd.DataFrame([{"article_id": f"fixture_{index}", "title": f"Fixture title {index}",
                   "abstract": f"Synthetic epidemiology abstract {index}"} for index in range(65)]).to_csv(source, index=False)
    args = SimpleNamespace(pathogen="lassa", model="gpt-6.1-terra", data_dir=directory,
                           input=source, sample_size=None)
    before = digest(source)
    captured = []
    for model in ["gpt-5.2-2025-12-11", "gpt-6.1-terra"]:
        args.model = model
        config = screening_config(args, offline=True)
        assert config.stage == "abstract_screen"
        assert (config.abstract_screening_batch_size, config.abstract_screening_concurrency) == (64, 8)
        requests, selected = capture_requests(config)
        assert len(requests) == 65
        template = get_prompt("abstract_screening", "lassa")
        normalized = []
        for request, row in zip(requests, selected.to_dict("records")):
            body = copy.deepcopy(request["body"])
            assert body.pop("model") == model
            prompt = template.format(title=row["title"], abstract=row["abstract"])
            assert body == {"messages": [{"role": "user", "content": prompt}],
                            "reasoning_effort": "high", "max_completion_tokens": 65536}
            normalized.append(body)
        captured.append(normalized)
    assert captured[0] == captured[1] and digest(source) == before
    with redirect_stderr(StringIO()):
        try:
            screen_main(["--stage", "fulltext_screen", "--pathogen", "lassa", "--input", str(source), "--dry-run"])
        except SystemExit as error:
            assert error.code == 2
        else:
            raise AssertionError("Full-text screening must not be exposed")
    return 130


def check_batch_recovery(directory):
    from scripts.batch import collect, submit
    client = Mock()
    job = {"name": "part_000", "status": "creating", "input_file_id": "file_fixture"}
    state = {"jobs": [job]}
    client.batches.list.return_value = []
    try:
        submit(client, directory, state, 1)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Ambiguous creation must stop before resubmission")
    client.batches.create.assert_not_called()
    client.files.create.assert_not_called()
    client.batches.list.return_value = [SimpleNamespace(input_file_id="file_fixture", id="batch_fixture", status="completed")]
    submit(client, directory, state, 1)
    client.batches.create.assert_not_called()
    assert job["batch_id"] == "batch_fixture"
    pd.DataFrame([{"article_id": "one", "title": "One"}, {"article_id": "two", "title": "Two"}]).to_csv(directory / "rows.csv", index=False)
    write_jsonl(directory / "inputs.jsonl", [{"custom_id": "lassa/one"}, {"custom_id": "lassa/two"}])
    write_jsonl(directory / "outputs/part_000_output.jsonl", [{"custom_id": "lassa/one", "response": {
        "status_code": 200, "body": {"model": "gpt-6.1-terra", "id": "response_fixture", "choices": [{"finish_reason": "stop", "message": {"content": "<decision>INCLUDE</decision>"}}], "usage": {}}}}])
    job.update(file="inputs.jsonl", receipt={"output_file_id": "output_fixture"})
    plan = {"stage": "abstract_screen", "pathogen": "lassa", "model": "gpt-6.1-terra"}
    result = collect(client, directory, plan, state)
    assert not result["complete"] and result["decisions"] == 1 and job["unresolved"] == 1
    assert result["output"] == "partial_screening.csv"
    assert collect(client, directory, plan, state) == result
    client.files.content.assert_not_called()
    return 3


def check_pdf_supplements(directory):
    from scripts import collect_pdfs
    directory.mkdir()
    inputs = directory / "inputs"
    inputs.mkdir()
    valid = directory / "valid.pdf"
    wrong = directory / "wrong.pdf"
    for target, text in [(valid, "Synthetic Lassa transmission study DOI 10.1234/lassa"), (wrong, "Unrelated report DOI 10.9999/other")]:
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((72, 72), text)
            document.save(target)
    jobs = [{"article_id": "one", "title": "Synthetic Lassa transmission study", "doi": "10.1234/lassa", "ai4epi_abstract_decision": "INCLUDE"},
            {"article_id": "two", "title": "Synthetic Ebola incidence estimates", "doi": "10.1234/ebola", "ai4epi_abstract_decision": "INCLUDE"}]
    pd.DataFrame(jobs).to_csv(inputs / "lassa.csv", index=False)
    supplements = directory / "supplements.csv"
    pd.DataFrame([{"pathogen": "lassa", "article_id": article, "pdf_url": "", "local_path": local.name,
                   "evidence_url": "https://example.org/article", "version": "publisher"}
                  for article, local in [("one", valid), ("two", wrong)]]).to_csv(supplements, index=False)
    arguments = ["--input-dir", str(inputs), "--output-dir", str(directory / "output"),
                 "--data-dir", str(directory / "cache"), "--supplements", str(supplements), "--offline", "--workers", "1"]
    with patch("requests.sessions.Session.request", side_effect=AssertionError("Offline PDF check made a network request")), redirect_stdout(StringIO()):
        assert collect_pdfs.main(arguments) == 0
        ready = pd.read_csv(directory / "output/ocr_ready_pdfs.csv")
        missing = pd.read_csv(directory / "output/missing_pdfs.csv")
        queue = pd.read_csv(directory / "output/pdf_search_queue.csv")
        assert list(ready.article_id) == ["one"] and list(missing.article_id) == ["two"]
        assert list(queue.article_id) == ["two"]
        assert ready.iloc[0].sha256 == digest(valid)
        assert missing.iloc[0].missing_reason == "identity_or_structure_unverified"
        first_hash = ready.iloc[0].sha256
        assert collect_pdfs.main(arguments) == 0
        assert pd.read_csv(directory / "output/ocr_ready_pdfs.csv").iloc[0].sha256 == first_hash
    return 4


def check_jats_conversion(directory):
    from scripts import jats2md
    directory.mkdir()
    inputs = directory / "xml"
    inputs.mkdir()
    body = "Synthetic transmission results and confidence intervals. " * 60
    full = ('<article><front><article-meta><title-group><article-title>XML fixture</article-title>'
            '</title-group></article-meta></front><body><sec><title>Results</title><p>' + body + '</p>'
            '<table-wrap><table><tr><th>Parameter</th><th>Value</th></tr><tr><td>R</td><td>1.5</td></tr>'
            '</table></table-wrap><disp-formula><tex-math>R_0 = 1.5</tex-math></disp-formula></sec></body>'
            '<back><ref-list><ref><mixed-citation>Fixture reference.</mixed-citation></ref></ref-list></back>'
            '<sub-article><body><p>Excluded peer review.</p></body></sub-article></article>')
    fixtures = {"01_full.xml": full, "02_short.xml": "<article><body><p>" + "Short body. " * 30 + "</p></body></article>",
                "03_scanned.xml": "<article><front><article-meta><abstract><p>" + body + "</p></abstract></article-meta></front></article>",
                "04_invalid.xml": "<article><broken>"}
    for name, text in fixtures.items():
        (inputs / name).write_text(text)
    with redirect_stdout(StringIO()) as output:
        jats2md.main([str(inputs), str(directory / "markdown")])
    assert json.loads(output.getvalue()) == {"n": 4, "ok": 1, "no_body": 1, "short_body": 1, "error": 1}
    text = (directory / "markdown/01_full.md").read_text()
    assert "| Parameter | Value |" in text and "$R_0 = 1.5$" in text and "## References" in text
    assert "Fixture reference." in text and "Excluded peer review." not in text
    for row in [json.loads(line) for line in (directory / "markdown/manifest_xml2md.jsonl").read_text().splitlines()]:
        assert row["source_sha256"] == digest(inputs / (row["article_id"] + ".xml"))
        if row["status"] != "error":
            assert row["md_sha256"] == digest(directory / "markdown" / (row["article_id"] + ".md"))
    with redirect_stdout(StringIO()) as output:
        jats2md.main([str(inputs), str(directory / "limited"), "--limit", "1"])
    assert json.loads(output.getvalue())["n"] == 1
    return 4


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify title-screening requests, Batch recovery and PDF supplements offline.")
    parser.add_argument("--output", type=Path, default=BASE / "outputs/verification.json")
    args = parser.parse_args(argv)
    manifest = verify_vendor()
    for name, expected in manifest["upstream_original_files_sha256"].items():
        if "/prompt_templates/" in name:
            assert digest(BASE / "vendor/agentslr" / name) == expected
    with TemporaryDirectory(prefix="agentslr-verify-") as temporary:
        directory = Path(temporary)
        requests = check_requests(directory)
        recovery = check_batch_recovery(directory)
        pdf_checks = check_pdf_supplements(directory / "pdf_checks")
        xml_checks = check_jats_conversion(directory / "xml_checks")
    result = {"status": "passed", "vendor_files": len(manifest["vendor_files_sha256"]),
              "captured_requests": requests, "batch_recovery_checks": recovery, "pdf_supplement_checks": pdf_checks,
              "jats_conversion_checks": xml_checks,
              "differing_request_fields": ["model"], "model_calls": 0}
    save(args.output, result)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
