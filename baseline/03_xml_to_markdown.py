import argparse
from datetime import datetime, timezone
from hashlib import sha256
import html
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

BLOCKS = {"sec", "table", "array", "table-wrap", "table-wrap-group", "fig", "fig-group", "list", "def-list", "disp-formula", "boxed-text"}
HTML_TAGS = {"table", "thead", "tbody", "tfoot", "tr", "td", "th", "colgroup", "col", "p", "sup", "sub", "a", "span", "strong", "em", "br", "ul", "ol", "li"}
HTML_ATTRS = {"id", "rowspan", "colspan", "headers", "scope", "align", "valign", "span", "width", "height", "border", "cellpadding", "cellspacing", "namest", "nameend", "morerows", "colname", "colnum", "cols"}


def tag(element):
    return element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else ""


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def anchor(element):
    identifier = element.get("id")
    return f'<a id="{html.escape(identifier, quote=True)}"></a>' if identifier else ""


def formula(element):
    tex = next((node for node in element.iter() if tag(node) == "tex-math"), None)
    if tex is not None:
        text = clean("".join(tex.itertext()))
        text = re.sub(r"^\\documentclass.*?\\begin\{document\}", "", text)
        text = text.replace("\\end{document}", "").strip()
        return text if text.startswith("$") else f"${text}$"
    math = next((node for node in element.iter() if tag(node) == "math"), None)
    if math is not None and math.get("alttext"):
        return f'${math.get("alttext").strip()}$'
    return clean(" ".join(element.itertext()))


def inline(element):
    if element is None:
        return ""
    name = tag(element)
    if name in {"inline-formula", "disp-formula", "math"}:
        return formula(element)
    parts = [element.text or ""]
    for child in element:
        if tag(child) not in BLOCKS:
            parts.append(inline(child))
        parts.append(child.tail or "")
    text = "".join(parts)
    if name == "sup":
        return "^" + clean(text)
    if name == "sub":
        return "_" + clean(text)
    if name == "break":
        return " "
    if name == "xref" and element.get("rid"):
        targets = element.get("rid").split()
        label = clean(text) or targets[0]
        return f"[{label}](#{targets[0]})" + "".join(f" [{target}](#{target})" for target in targets[1:])
    return text


class Renderer:
    def __init__(self):
        self.tables = set()
        self.arrays = set()
        self.wrappers = set()
        self.figures = set()
        self.figure_groups = set()
        self.captions = set()
        self.warnings = []

    def table_html(self, element):
        def clone(source):
            name = tag(source)
            if name == "table":
                self.tables.add(source)
            if name == "array":
                self.arrays.add(source)
            if name in {"inline-formula", "disp-formula", "math"}:
                result = ET.Element("span")
                result.text = formula(source)
                return result
            mapped = {"array": "table", "bold": "strong", "italic": "em", "break": "br", "xref": "a", "ext-link": "a", "list": "ul", "list-item": "li"}.get(name, name)
            result = ET.Element(mapped if mapped in HTML_TAGS else "span")
            result.attrib.update({key: value for key, value in source.attrib.items() if key in HTML_ATTRS})
            if name == "xref" and source.get("rid"):
                result.set("href", "#" + source.get("rid").split()[0])
                result.set("data-targets", source.get("rid"))
            if name == "ext-link":
                link = source.get("{http://www.w3.org/1999/xlink}href") or source.get("href")
                if link:
                    result.set("href", link)
            result.text = source.text
            for child in source:
                copied = clone(child)
                copied.tail = child.tail
                result.append(copied)
            return result
        if any(tag(node) in {"tgroup", "row", "entry"} for node in element.iter()):
            self.warnings.append("non_html_table_requires_review")
            self.tables.update(node for node in element.iter() if tag(node) == "table")
            return ET.tostring(element, encoding="unicode")
        return ET.tostring(clone(element), encoding="unicode", method="html")

    def caption(self, element, kind, level):
        label = clean(inline(element.find("label"))) or kind
        output = [anchor(element), f"**{label.rstrip('.:')}.**"]
        caption = element.find("caption")
        if caption is not None:
            output.extend(self.render(caption, level))
        title = element.find("title")
        if title is not None:
            output.append(clean(inline(title)))
        for name in ["alt-text", "long-desc"]:
            for node in element.findall(name):
                output.extend(self.render(node, level))
        return output

    def render(self, element, level=2):
        if element is None:
            return []
        name = tag(element)
        if name in {"sub-article", "response", "ref-list", "graphic", "inline-graphic", "media", "label"}:
            return []
        if name == "table-wrap":
            if element in self.wrappers:
                return []
            self.wrappers.add(element)
            output = self.caption(element, "Table", level)
            tables = [node for node in element.iter() if tag(node) in {"table", "array"}]
            for table in tables:
                output.extend(self.render(table, level))
            if not tables:
                self.warnings.append("table_without_structured_cells:" + element.get("id", "unknown"))
            output.extend(self.render(element.find("table-wrap-foot"), level))
            return output
        if name == "table":
            return [] if element in self.tables else [self.table_html(element)]
        if name == "array":
            return [] if element in self.arrays else [anchor(element), self.table_html(element)]
        if name in {"fig", "fig-group"}:
            seen = self.figures if name == "fig" else self.figure_groups
            if element in seen:
                return []
            seen.add(element)
            output = self.caption(element, "Figure" if name == "fig" else "Figure group", level)
            for child in element:
                if tag(child) in {"fig", "fig-group"}:
                    output.extend(self.render(child, level))
            return output
        if name == "caption":
            self.captions.add(element)
        if name in {"inline-formula", "disp-formula", "math"}:
            return [formula(element)]
        if name == "p":
            output = []
            parts = [element.text or ""]
            for child in element:
                if tag(child) in BLOCKS:
                    output.append(clean("".join(parts)))
                    output.extend(self.render(child, level))
                    parts = []
                else:
                    parts.append(inline(child))
                parts.append(child.tail or "")
            output.append(clean("".join(parts)))
            return output
        if name == "list":
            output = []
            for index, item in enumerate(element.findall("list-item"), 1):
                marker = f"{index}." if element.get("list-type") in {"order", "ordered"} else "-"
                content = "\n\n".join(filter(None, self.render(item, level)))
                output.append(f"{marker} {content}")
            return ["\n".join(output)]
        if name == "def-list":
            return ["\n".join(f'- {clean(inline(item.find("term")))}: {clean(" ".join(item.find("def").itertext())) if item.find("def") is not None else ""}' for item in element.findall("def-item"))]
        if name == "fn":
            label = clean(inline(element.find("label")))
            output = [anchor(element), f"**{label}**" if label else ""]
            for child in element:
                if tag(child) != "label":
                    output.extend(self.render(child, level))
            return output
        output = []
        if name in {"sec", "app", "ack", "notes", "boxed-text"}:
            output.append(anchor(element))
            title = clean(inline(element.find("title")))
            label = clean(inline(element.find("label")))
            title = " ".join(filter(None, [label, title])) or ("Acknowledgements" if name == "ack" else "")
            if title:
                output.append("#" * min(level, 6) + " " + title)
            child_level = level + 1
        else:
            child_level = level
            output.append(clean(element.text))
        for child in element:
            if name in {"sec", "app", "ack", "notes", "boxed-text"} and tag(child) in {"title", "label"}:
                continue
            output.extend(self.render(child, child_level))
            output.append(clean(child.tail))
        return output


def convert(path):
    root = ET.parse(path).getroot()
    for node in root.iter():
        node.tag = tag(node)
    if tag(root) != "article":
        articles = root.findall(".//article")
        if len(articles) != 1:
            raise ValueError("Expected one JATS article per XML file")
        root = articles[0]
    for parent in list(root.iter()):
        for child in list(parent):
            language = child.get("{http://www.w3.org/XML/1998/namespace}lang")
            if tag(child) in {"sub-article", "response"} or (tag(child) == "trans-abstract" and language not in {None, "en"}):
                parent.remove(child)
    renderer = Renderer()
    meta = root.find("front/article-meta")
    title = clean(inline(meta.find("title-group/article-title"))) if meta is not None else ""
    subtitle = clean(inline(meta.find("title-group/subtitle"))) if meta is not None else ""
    output = ["# " + (title or "(untitled)") + (": " + subtitle if subtitle else "")]
    identifiers = {}
    if meta is not None:
        identifiers = {node.get("pub-id-type"): clean(node.text) for node in meta.findall("article-id")}
        names = [clean(" ".join(node.itertext())) for node in meta.findall("contrib-group/contrib/name")]
        year = next((node.findtext("year") for node in meta.findall("pub-date") if node.findtext("year")), "")
        journal = root.findtext("front/journal-meta/journal-title-group/journal-title") or ""
        output.append(" | ".join(filter(None, [", ".join(names), journal, year, identifiers.get("doi"), identifiers.get("pmcid")])))
        for abstract in list(meta.findall("abstract")) + list(meta.findall("trans-abstract")):
            if abstract.get("abstract-type") not in {"graphical", "teaser"}:
                output.append("## " + (clean(inline(abstract.find("title"))) or "Abstract"))
                for child in abstract:
                    if tag(child) != "title":
                        output.extend(renderer.render(child, 3))
        keywords = [clean(inline(node)) for node in meta.findall("kwd-group/kwd")]
        if keywords:
            output.append("**Keywords:** " + "; ".join(keywords))
    body = root.find("body")
    body_chars = len(clean(" ".join(body.itertext()))) if body is not None else 0
    for container in [body, root.find("floats-group"), root.find("back")]:
        output.extend(renderer.render(container))
    for node in root.iter():
        if tag(node) in {"table-wrap", "table", "array", "fig", "fig-group"}:
            output.extend(renderer.render(node))
    for node in root.iter():
        if tag(node) == "caption" and node not in renderer.captions:
            output.extend(renderer.render(node))
    references = []
    for reference in root.findall(".//ref-list/ref"):
        citation = next((node for node in reference if tag(node) in {"mixed-citation", "element-citation", "citation", "nlm-citation"}), None)
        if citation is not None:
            text = clean(" ".join(citation.itertext()))
            label = clean(inline(reference.find("label"))) or str(len(references) + 1)
            references.extend([anchor(reference), f"{label}. {text}"])
    if references:
        output.extend(["## References", *references])
    tables = [node for node in root.iter() if tag(node) == "table"]
    figures = [node for node in root.iter() if tag(node) == "fig"]
    captions = [node for node in root.iter() if tag(node) == "caption"]
    details = {"body_chars": body_chars, "has_body_element": body is not None, "n_refs": len(references) // 2, "doi": identifiers.get("doi", ""),
               "pmid": identifiers.get("pmid", ""), "pmcid": identifiers.get("pmcid", identifiers.get("pmc", "")),
               "tables": len(tables), "tables_rendered": len(renderer.tables), "figures": len(figures),
               "arrays": len(renderer.arrays),
               "figures_rendered": len(renderer.figures), "captions": len(captions), "captions_rendered": len(renderer.captions),
               "rowspan_cells": sum(node.get("rowspan", "1") != "1" for node in root.iter()),
               "colspan_cells": sum(node.get("colspan", "1") != "1" for node in root.iter()),
               "warnings": sorted(set(renderer.warnings))}
    if len(tables) != len(renderer.tables) or len(figures) != len(renderer.figures) or len(captions) != len(renderer.captions):
        details["warnings"].append("incomplete_structural_coverage")
    text = "\n\n".join(part for part in output if part and part.strip()) + "\n"
    return text, details


def main(argv=None):
    parser = argparse.ArgumentParser(description="Convert JATS XML to Markdown with HTML tables and complete captions.")
    parser.add_argument("xml_dir", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--recursive", action="store_true")
    args = parser.parse_args(argv)
    if not args.xml_dir.is_dir():
        parser.error("XML input directory does not exist")
    if args.limit is not None and args.limit < 1:
        parser.error("limit must be positive")
    files = sorted(path for path in (args.xml_dir.rglob("*.xml") if args.recursive else args.xml_dir.glob("*.xml")) if path.is_file())
    if args.limit:
        files = files[:args.limit]
    if not files:
        parser.error("No XML files found")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stats = {"n": 0, "ok": 0, "short_body": 0, "no_body": 0, "error": 0, "identity_mismatch": 0, "needs_review": 0,
             "tables": 0, "arrays": 0, "figures": 0, "captions": 0, "rowspan_cells": 0, "colspan_cells": 0}
    manifest = args.out_dir / "manifest_xml2md.jsonl"
    with manifest.open("w", encoding="utf-8", newline="\n") as handle:
        for path in files:
            relative = path.relative_to(args.xml_dir)
            record = {"article_id": path.stem, "source": "pmc_jats_xml", "source_path": str(path.resolve()),
                      "source_sha256": sha256(path.read_bytes()).hexdigest(), "time_utc": datetime.now(timezone.utc).isoformat()}
            stats["n"] += 1
            try:
                text, details = convert(path)
                record.update(details)
                if path.stem.startswith("PMID_") and details["pmid"] and path.stem[5:] != details["pmid"]:
                    raise ValueError("article_id_pmid_mismatch: " + details["pmid"])
                destination = args.out_dir / relative.with_suffix(".md")
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_suffix(".md.tmp")
                temporary.write_text(text, encoding="utf-8", newline="\n")
                temporary.replace(destination)
                status = "ok" if details["body_chars"] >= 2000 else "no_body" if details["body_chars"] < 200 else "short_body"
                record.update(details, status=status, md_chars=len(text), markdown_path=str(destination.resolve()),
                              md_sha256=sha256(text.encode("utf-8")).hexdigest(), needs_review=status != "ok" or bool(details["warnings"]))
                stats[status] += 1
                stats["needs_review"] += record["needs_review"]
                for name in ["tables", "arrays", "figures", "captions", "rowspan_cells", "colspan_cells"]:
                    stats[name] += details[name]
            except Exception as error:
                status = "identity_mismatch" if str(error).startswith("article_id_pmid_mismatch:") else "error"
                record.update(status=status, needs_review=True, error=f"{type(error).__name__}: {error}")
                stats[status] += 1
                stats["needs_review"] += 1
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
    (args.out_dir / "summary.json").write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
