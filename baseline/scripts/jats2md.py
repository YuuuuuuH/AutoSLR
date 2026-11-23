import argparse, hashlib, json, os, re, sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

MML = "{http://www.w3.org/1998/Math/MathML}"
XLINK = "{http://www.w3.org/1999/xlink}"
WS = re.compile(r"\s+")


def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def inline(el, skip=("fn", "table-wrap", "fig", "disp-formula", "list", "sec", "table", "boxed-text")):
    out = [el.text or ""]
    for ch in el:
        t = local(ch.tag)
        if t in skip:
            pass
        elif t == "inline-formula":
            out.append(formula(ch))
        elif t == "math":
            out.append(math_text(ch))
        elif t in ("sup",):
            s = inline(ch)
            out.append(f"^{s}" if s.strip() else "")
        elif t in ("sub",):
            s = inline(ch)
            out.append(f"_{s}" if s.strip() else "")
        elif t == "break":
            out.append(" ")
        else:
            out.append(inline(ch, skip))
        out.append(ch.tail or "")
    return "".join(out)


def clean(s):
    return WS.sub(" ", s or "").strip()


def math_text(m):
    alt = m.get("alttext")
    if alt:
        return f"${alt.strip()}$"
    return clean("".join(m.itertext()))


def formula(f):
    for ch in f.iter():
        if local(ch.tag) == "tex-math":
            tex = clean("".join(ch.itertext()))
            tex = re.sub(r"^\\documentclass.*?\\begin\{document\}", "", tex, flags=re.S)
            tex = tex.replace("\\end{document}", "").strip()
            return tex if tex.startswith("$") else f"${tex}$"
    for ch in f.iter():
        if local(ch.tag) == "math":
            return math_text(ch)
    return clean(inline(f, skip=("label",)))


def caption(el, kind):
    lab = el.find("label")
    cap = el.find("caption")
    parts = []
    head = clean(inline(lab)) if lab is not None else ""
    if cap is not None:
        ct = cap.find("title")
        if ct is not None:
            parts.append(clean(inline(ct)))
        for p in cap.findall("p"):
            parts.append(clean(inline(p)))
    s = " ".join(x for x in parts if x)
    if not s and not head:
        return ""
    return f"**{(head or kind).rstrip('.:')}.** {s}".rstrip()


def cell(c):
    return clean(inline(c, skip=())).replace("|", "\\|")


def table_md(tw):
    out = []
    c = caption(tw, "Table")
    if c:
        out.append(c)
    for tab in tw.iter():
        if local(tab.tag) != "table":
            continue
        rows, head_n = [], 0
        for sect in ("thead", "tbody", "tfoot"):
            for s in tab.findall(sect):
                for tr in s.findall("tr"):
                    rows.append([cell(c) for c in tr if local(c.tag) in ("td", "th")])
                if sect == "thead":
                    head_n = len(rows)
        for tr in tab.findall("tr"):
            rows.append([cell(c) for c in tr if local(c.tag) in ("td", "th")])
        rows = [r for r in rows if any(r)]
        if not rows:
            continue
        w = max(len(r) for r in rows)
        rows = [r + [""] * (w - len(r)) for r in rows]
        hdr = rows[0] if head_n == 0 else rows[0]
        out.append("| " + " | ".join(hdr) + " |")
        out.append("|" + "---|" * w)
        for r in rows[1:]:
            out.append("| " + " | ".join(r) + " |")
    foot = tw.find("table-wrap-foot")
    if foot is not None:
        t = clean(" ".join(clean(inline(x, skip=())) for x in foot.iter() if local(x.tag) in ("p", "fn")) or "".join(foot.itertext()))
        if t:
            out.append(t)
    return "\n".join(out)


def blocks(el, depth, out):
    if el.text and el.text.strip() and local(el.tag) not in ("sec", "body", "back", "app", "ack", "boxed-text"):
        out.append(clean(el.text))
    for ch in el:
        t = local(ch.tag)
        if t in ("sec", "app", "ack", "notes", "glossary"):
            ti = ch.find("title")
            lab = ch.find("label")
            head = " ".join(clean(inline(x)) for x in (lab, ti) if x is not None).strip()
            if not head and t == "ack":
                head = "Acknowledgements"
            if head:
                out.append("#" * min(depth, 6) + " " + head)
            blocks(ch, depth + 1, out)
        elif t in ("title", "label", "sec-meta", "object-id"):
            continue
        elif t == "p":
            txt = clean(inline(ch))
            if txt:
                out.append(txt)
            for sub in ch:
                if local(sub.tag) in ("table-wrap", "fig", "disp-formula", "list", "boxed-text"):
                    blocks(_wrap(sub), depth, out)
        elif t in ("list",):
            items = []
            for li in ch.findall("list-item"):
                txt = " ".join(clean(inline(p)) for p in li if local(p.tag) == "p") or clean(inline(li))
                items.append("- " + txt)
            if items:
                out.append("\n".join(items))
        elif t == "def-list":
            items = []
            for di in ch.findall("def-item"):
                term = di.find("term"); d = di.find("def")
                items.append(f"- {clean(inline(term)) if term is not None else ''}: {clean(''.join(d.itertext())) if d is not None else ''}")
            if items:
                out.append("\n".join(items))
        elif t == "table-wrap":
            md = table_md(ch)
            if md:
                out.append(md)
        elif t == "table-wrap-group":
            for tw in ch.findall("table-wrap"):
                md = table_md(tw)
                if md:
                    out.append(md)
        elif t in ("fig", "fig-group"):
            figs = [ch] if t == "fig" else ch.findall("fig")
            for f in figs:
                c = caption(f, "Figure")
                if c:
                    out.append(c)
        elif t == "disp-formula":
            out.append(formula(ch))
        elif t == "disp-quote":
            txt = clean(" ".join(clean(inline(p)) for p in ch.iter() if local(p.tag) == "p"))
            if txt:
                out.append("> " + txt)
        elif t == "boxed-text":
            c = caption(ch, "Box")
            if c:
                out.append(c)
            blocks(ch, depth + 1, out)
        elif t == "supplementary-material":
            c = caption(ch, "Supplementary material")
            if c:
                out.append(c)
        elif t in ("fn-group",):
            fns = [clean(" ".join(clean(inline(p)) for p in fn.iter() if local(p.tag) == "p")) for fn in ch.findall("fn")]
            fns = [f for f in fns if f]
            if fns:
                out.append("\n".join("- " + f for f in fns))
        elif t in ("ref-list",):
            continue
        elif t in ("app-group",):
            blocks(ch, depth, out)
        elif t in ("statement", "verse-group", "speech", "code", "preformat", "array", "chem-struct-wrap"):
            txt = clean("".join(ch.itertext()))
            if txt:
                out.append(txt)
        if ch.tail and ch.tail.strip() and local(el.tag) not in ("p",):
            out.append(clean(ch.tail))


def _wrap(e):
    w = ET.Element("wrap")
    w.append(e)
    return w


def refs(root):
    out = []
    n = 0
    for rl in root.iter():
        if local(rl.tag) != "ref-list":
            continue
        for ref in rl.iter():
            if local(ref.tag) != "ref":
                continue
            cit = None
            for c in ref:
                if local(c.tag) in ("mixed-citation", "element-citation", "citation", "nlm-citation"):
                    cit = c
                    break
            if cit is None:
                continue
            n += 1
            if local(cit.tag) == "element-citation":
                txt = clean(" ".join(x.strip() for x in cit.itertext() if x.strip()))
            else:
                txt = clean("".join(cit.itertext()))
            lab = ref.find("label")
            out.append(f"{clean(lab.text) if lab is not None and lab.text else n}. {txt}")
    return out


def convert(path):
    tree = ET.parse(path)
    root = tree.getroot()
    for sa in list(root.iter()):
        for ch in list(sa):
            if local(ch.tag) in ("sub-article", "response"):
                sa.remove(ch)
    meta = root.find("front/article-meta")
    md = []
    title = clean(inline(meta.find("title-group/article-title"))) if meta is not None and meta.find("title-group/article-title") is not None else ""
    sub = meta.find("title-group/subtitle") if meta is not None else None
    md.append("# " + (title or "(untitled)") + (f": {clean(inline(sub))}" if sub is not None else ""))
    if meta is not None:
        jt = root.find("front/journal-meta/journal-title-group/journal-title")
        doi = next((clean(a.text) for a in meta.findall("article-id") if a.get("pub-id-type") == "doi"), "")
        pmcid = next((clean(a.text) for a in meta.findall("article-id") if a.get("pub-id-type") == "pmcid"), "")
        year = next((clean(d.findtext("year")) for d in meta.findall("pub-date") if d.findtext("year")), "")
        names = []
        for c in meta.iter():
            if local(c.tag) == "contrib" and c.get("contrib-type", "author") == "author":
                nm = c.find("name")
                if nm is not None:
                    names.append(clean(f"{nm.findtext('given-names') or ''} {nm.findtext('surname') or ''}"))
                elif c.find("collab") is not None:
                    names.append(clean("".join(c.find("collab").itertext())))
        line = " | ".join(x for x in [", ".join(names[:12]) + (" et al." if len(names) > 12 else ""),
                                       clean(jt.text) if jt is not None and jt.text else "", year,
                                       f"doi:{doi}" if doi else "", pmcid] if x)
        if line:
            md.append(line)
        for ab in meta.findall("abstract") + meta.findall("trans-abstract"):
            if ab.get("abstract-type") in ("graphical", "teaser") or (ab.get("{http://www.w3.org/XML/1998/namespace}lang") not in (None, "en") and local(ab.tag) == "trans-abstract"):
                continue
            ti = ab.find("title")
            md.append("## " + (clean(inline(ti)) if ti is not None else "Abstract"))
            blocks(ab, 3, md)
        kw = [clean(inline(k)) for g in meta.findall("kwd-group") for k in g.findall("kwd")]
        if kw:
            md.append("**Keywords:** " + "; ".join(k for k in kw if k))
    head_n = len(md)
    body = root.find("body")
    if body is not None:
        blocks(body, 2, md)
    body_chars = sum(len(x) for x in md[head_n:])
    fg = root.find("floats-group")
    if fg is not None:
        blocks(fg, 2, md)
    back = root.find("back")
    if back is not None:
        blocks(back, 2, md)
    r = refs(root)
    if r:
        md.append("## References")
        md.append("\n".join(r))
    text = "\n\n".join(x for x in md if x and x.strip()) + "\n"
    return text, body_chars, len(r), (body is not None)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("xml_dir"); ap.add_argument("out_dir"); ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    files = sorted(f for f in os.listdir(a.xml_dir) if f.endswith(".xml"))
    if a.limit:
        files = files[: a.limit]
    stats = {"n": 0, "ok": 0, "no_body": 0, "short_body": 0, "error": 0}
    with open(os.path.join(a.out_dir, "manifest_xml2md.jsonl"), "w", encoding="utf-8") as man:
        for f in files:
            aid = f[:-4]
            src = os.path.join(a.xml_dir, f)
            rec = {"article_id": aid, "source": "pmc_jats_xml", "source_path": src,
                   "source_sha256": hashlib.sha256(open(src, "rb").read()).hexdigest(),
                   "time_utc": datetime.now(timezone.utc).isoformat()}
            stats["n"] += 1
            try:
                text, body_chars, nref, has_body = convert(src)
                dest = os.path.join(a.out_dir, aid + ".md")
                with open(dest, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
                status = "ok" if body_chars >= 2000 else ("no_body" if body_chars < 200 else "short_body")
                stats[status] += 1
                rec.update(status=status, md_chars=len(text), body_chars=body_chars, n_refs=nref, has_body_element=has_body,
                           md_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
            except Exception as e:
                stats["error"] += 1
                rec.update(status="error", error=f"{type(e).__name__}: {e}"[:300])
            man.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
