"""A paper as an agent can read it: text by section, every table as a CSV, every figure as an image.

PDF text loses structure: tables run together, symbols are garbled (a minus sign set in a symbol font reads
"2"), figures are gone. When a paper is open access in Europe PMC, ReCRATE builds a reading package from its
structured full text (JATS XML) instead:

    paper/
      paper.md          title, authors, journal, date, abstract, sections; figure and table captions in place
      index.md          what is in the package
      tables/table-1.csv ...   each table, as printed (header rows joined), with its caption in index.md
      figures/figure-1.jpg ... each figure, with its caption
      supplement/...    supplementary files (PDF, spreadsheets)

For other PDFs, ``figures_from_pdf`` keeps the embedded images (by page), so figures are not lost entirely.
"""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
XLINK = "{http://www.w3.org/1999/xlink}href"


def _text(el) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def find_pmcid(doi: str, fetch_json) -> str | None:
    """The PMC id of an open-access paper, from its DOI (None if it isn't in Europe PMC's open-access set)."""
    q = urllib.parse.quote(f'DOI:"{doi}"')
    res = fetch_json(f"{EPMC}/search?query={q}&format=json&resultType=lite", "application/json")
    for r in res.get("resultList", {}).get("result", []):
        if r.get("pmcid") and r.get("isOpenAccess") == "Y":
            return r["pmcid"]
    return None


def _table_rows(tw) -> list[list[str]]:
    """Header rows joined into one, body rows as they are (row spans repeated down)."""
    head, body = [], []
    for sect, target in (("thead", head), ("tbody", body)):
        for part in tw.iter(sect):
            for tr in part.iter("tr"):
                target.append([_text(c) for c in tr if c.tag in ("td", "th")])
    if not head and not body:
        body = [[_text(c) for c in tr if c.tag in ("td", "th")] for tr in tw.iter("tr")]
    if head:
        width = max(len(r) for r in head + body)
        cols = [" · ".join(x for x in dict.fromkeys(r[i] if i < len(r) else "" for r in head) if x) for i in range(width)]
        rows = [cols] + body
    else:
        rows = body
    prev: list[str] = []
    for r in rows[1:]:  # an empty leading cell usually continues the row above (e.g. the species)
        for i, v in enumerate(r):
            if not v and i == 0 and prev:
                r[i] = prev[i]
        prev = r
    return rows


def from_jats(xml: str, dest: Path, assets: dict[str, bytes] | None = None) -> Path:
    """Write the reading package from a JATS article. ``assets``: file name -> bytes (figures, supplements)."""
    dest.mkdir(parents=True, exist_ok=True)
    root = ET.fromstring(xml)
    meta = root.find(".//article-meta")
    title = _text(meta.find(".//article-title")) if meta is not None else "Untitled"
    authors = []
    for group in (meta.iter("contrib-group") if meta is not None else []):
        if group.get("content-type", "author") != "author":  # editors, reviewers
            continue
        for c in group.iter("contrib"):
            if c.get("contrib-type", "author") == "author" and c.find(".//surname") is not None \
                    and "editor" not in _text(c.find("role")).lower():
                authors.append(" ".join(x for x in (_text(c.find(".//given-names")), _text(c.find(".//surname"))) if x))
    journal = _text(root.find(".//journal-title"))
    doi = next((_text(a) for a in (meta.iter("article-id") if meta is not None else []) if a.get("pub-id-type") == "doi"), "")
    date = ""
    for pd in (meta.iter("pub-date") if meta is not None else []):
        y, m, d = _text(pd.find("year")), _text(pd.find("month")), _text(pd.find("day"))
        if y:
            date = "-".join(x.zfill(2) for x in (y, m or "1", d or "1"))
            if pd.get("pub-type") in ("epub", "ppub") or pd.get("date-type") == "pub":
                break
    licence = _text(meta.find(".//license")) if meta is not None else ""
    assets = assets or {}
    index = [f"# {title}", "", f"{', '.join(authors)}. *{journal}* ({date[:4]}). https://doi.org/{doi}", ""]

    figures, tables = [], []
    (dest / "tables").mkdir(exist_ok=True)
    for i, tw in enumerate(root.iter("table-wrap"), 1):
        label = _text(tw.find("label")) or f"Table {i}"
        cap = _text(tw.find("caption"))
        name = f"table-{i}.csv"
        with (dest / "tables" / name).open("w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(_table_rows(tw))
        tables.append((label, cap, name))
        tw.set("data-recrate", f"{label}: {cap} [tables/{name}]")
    figdir = dest / "figures"
    for i, fig in enumerate(root.iter("fig"), 1):
        label = _text(fig.find("label")) or f"Figure {i}"
        cap = _text(fig.find("caption"))
        href = (fig.find(".//graphic").get(XLINK) if fig.find(".//graphic") is not None else "") or ""
        keys = {Path(href).name, Path(href).stem} - {""}  # the href may or may not carry an extension
        hit = next((n for n in sorted(assets, key=lambda n: (not n.endswith(".jpg"), n))
                    if Path(n).name in keys or Path(n).stem in keys), None)
        name = None
        if hit:
            figdir.mkdir(exist_ok=True)
            name = f"figure-{i}{Path(hit).suffix}"
            (figdir / name).write_bytes(assets[hit])
        figures.append((label, cap, name))
        fig.set("data-recrate", f"{label}: {cap}" + (f" [figures/{name}]" if name else ""))
    supp = []
    for s in root.iter("supplementary-material"):
        media = s.find(".//media")
        href = media.get(XLINK) if media is not None else None
        label = _text(s.find("label")) or _text(s.find(".//title")) or (href or "")
        if href and href in assets:
            (dest / "supplement").mkdir(exist_ok=True)
            safe = Path(href).name  # names come from downloaded XML: never let one leave the folder
            (dest / "supplement" / safe).write_bytes(assets[href])
            supp.append((label, _text(s.find("caption")), safe))

    def section(el, depth: int) -> list[str]:
        out = []
        t = _text(el.find("title"))
        if t:
            out.append(f"{'#' * min(depth, 6)} {t}\n")
        for child in el:
            if child.tag == "p":
                for inner in list(child):  # figures and tables nested in a paragraph
                    if inner.get("data-recrate"):
                        out.append(f"> {inner.get('data-recrate')}\n")
                        child.remove(inner)
                out.append(_text(child) + "\n")
            elif child.tag == "sec":
                out += section(child, depth + 1)
            elif child.get("data-recrate"):
                out.append(f"> {child.get('data-recrate')}\n")
        return out

    md = [f"# {title}\n", f"{', '.join(authors)}\n", f"{journal}, {date}. https://doi.org/{doi}\n"]
    if licence:
        md.append(f"Licence: {licence}\n")
    abstract = root.find(".//abstract")
    if abstract is not None:
        md += ["## Abstract\n"] + [_text(p) + "\n" for p in abstract.iter("p")]
    body = root.find("body")
    for sec in (body if body is not None else []):
        if sec.tag == "sec":
            md += section(sec, 2)
        elif sec.tag == "p":
            md.append(_text(sec) + "\n")
    (dest / "paper.md").write_text("\n".join(md), encoding="utf-8")

    index += ["## Tables", ""] + [f"- **{l}** ([tables/{n}](tables/{n})): {c}" for l, c, n in tables] or ["- none"]
    index += ["", "## Figures", ""] + [f"- **{l}**" + (f" ([figures/{n}](figures/{n}))" if n else " (image not available)")
                                       + f": {c}" for l, c, n in figures] or ["- none"]
    if supp:
        index += ["", "## Supplementary material", ""] + [f"- **{l}** ([supplement/{h}](supplement/{h})): {c}" for l, c, h in supp]
    (dest / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    (dest / "package.json").write_text(json.dumps({
        "source": "Europe PMC (JATS full text)", "doi": doi, "title": title, "date_published": date,
        "tables": [{"label": l, "caption": c, "file": f"tables/{n}"} for l, c, n in tables],
        "figures": [{"label": l, "caption": c, "file": f"figures/{n}" if n else None} for l, c, n in figures],
        "supplement": [{"label": l, "caption": c, "file": f"supplement/{h}"} for l, c, h in supp]}, indent=2), encoding="utf-8")
    return dest / "paper.md"


def from_europepmc(doi: str, dest: Path, fetch_json, fetch_bytes) -> Path | None:
    """The reading package of an open-access paper, or None if Europe PMC doesn't have its full text."""
    pmcid = find_pmcid(doi, fetch_json)
    if not pmcid:
        return None
    xml = fetch_bytes(f"{EPMC}/{pmcid}/fullTextXML").decode("utf-8")
    assets: dict[str, bytes] = {}
    try:
        z = fetch_bytes(f"{EPMC}/{pmcid}/supplementaryFiles?includeInlineImage=yes")
        if z[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(z)) as zf:
                assets = {Path(n).name: zf.read(n) for n in zf.namelist() if not n.endswith("/")}
    except Exception:  # noqa: BLE001 - figures are a bonus; the text is what matters
        pass
    path = from_jats(xml, Path(dest), assets)
    meta = json.loads((Path(dest) / "package.json").read_text())
    meta["pmcid"] = pmcid
    (Path(dest) / "package.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return path


def figures_from_pdf(pdf: Path, dest: Path, min_bytes: int = 8_000, limit: int = 40) -> list[Path]:
    """Embedded images of a PDF, by page (figures/page-3-1.png ...). Small images (logos, icons) are skipped."""
    from pypdf import PdfReader

    out: list[Path] = []
    try:
        reader = PdfReader(str(pdf))
    except Exception:  # noqa: BLE001
        return out
    for pno, page in enumerate(reader.pages, 1):
        try:
            images = list(page.images)
        except Exception:  # noqa: BLE001 - unsupported image encodings: skip the page
            continue
        for k, img in enumerate(images, 1):
            data = img.data
            if len(data) < min_bytes:
                continue
            suffix = Path(img.name).suffix or ".png"
            p = Path(dest) / f"page-{pno}-{k}{suffix}"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            out.append(p)
            if len(out) >= limit:
                return out
    return out


def copy_package(src_dir: Path, dest_dir: Path) -> bool:
    """Copy a reading package's parts (tables, figures, supplement, index) next to the agent's paper text."""
    moved = False
    for part in ("tables", "figures", "supplement"):
        if (src_dir / part).is_dir():
            if (dest_dir / part).exists():
                shutil.rmtree(dest_dir / part)
            shutil.copytree(src_dir / part, dest_dir / part)
            moved = True
    for f in ("index.md", "package.json"):
        if (src_dir / f).exists():
            shutil.copy2(src_dir / f, dest_dir / f)
            moved = True
    return moved


def tables(paper_dir: Path) -> list[dict]:
    meta = Path(paper_dir) / "package.json"
    if meta.exists():
        return json.loads(meta.read_text()).get("tables", [])
    return [{"label": p.stem, "caption": "", "file": f"tables/{p.name}"} for p in sorted((Path(paper_dir) / "tables").glob("*.csv"))]


def figures(paper_dir: Path) -> list[dict]:
    meta = Path(paper_dir) / "package.json"
    if meta.exists():
        return [f for f in json.loads(meta.read_text()).get("figures", []) if f.get("file")]
    return [{"label": p.stem, "caption": "", "file": f"figures/{p.name}"}
            for p in sorted((Path(paper_dir) / "figures").glob("*")) if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif"}]


def tables_text(paper_dir: Path) -> str:
    """All tables as text (one line per row), so checks can match published values against clean cells."""
    out = []
    for p in sorted((Path(paper_dir) / "tables").glob("*.csv")):
        with p.open(newline="", encoding="utf-8") as fh:
            out += [" | ".join(r) for r in csv.reader(fh)]
    return "\n".join(out)


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()
