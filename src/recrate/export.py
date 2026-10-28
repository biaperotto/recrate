"""Export a research object's current RO-Crate: the description of the research ReCRATE works from.

    recrate export-crate agents/my_paper [--out my_paper-ro-crate.zip]

Whatever the start, the research object has one input crate, and this is it, ready to share, deposit or open
in any RO-Crate tool:

* **found** for the paper (WorkflowHub, Zenodo, DataCite, its code) and fetched;
* **supplied** (a crate, or a link to one), or **derived** from a Galaxy Workflow RO-Crate (whose original is kept
  unchanged and linked with ``isBasedOn``);
* **recreated** from the paper as of its publication (software at the versions available then, each with how its
  version was determined);
* **drafted** by the /recrate agent, labelled draft until a named person confirms it.

The export is a copy: the crate as ReCRATE holds it, plus a record of how it was obtained (a ``CreateAction`` by
ReCRATE, naming its source and date) and a readable ``ro-crate-preview.html``. The research object itself is not
changed. Runs are not included: each run has its own crate (export them from the record).
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__

HOW = {
    "supplied": "Supplied as an RO-Crate and used as published.",
    "derived from a Galaxy Workflow RO-Crate": "Derived by ReCRATE from a Galaxy Workflow RO-Crate: parameters read "
                                               "from the workflow file, published data and results from its test. "
                                               "The original crate is unchanged and linked with isBasedOn.",
    "recreated from the paper": "Recreated by ReCRATE from the paper as of its publication: the software it names, "
                                "at the versions available then, each with how its version was determined, its code "
                                "and the data it cites.",
    "drafted": "Drafted from the paper and its code by the ReCRATE agent (/recrate): every parameter quoted from the "
               "paper, every published value checked against it. A draft until a named person confirms it.",
}


def _how(manifest: dict[str, Any]) -> str:
    source = manifest.get("crate_source") or "supplied"
    found = manifest.get("crate_found")
    if found:
        return (f"Found for the paper on {found['where']} ({found['label']}, {found['url']}): {found['evidence']}. "
                + HOW.get("derived from a Galaxy Workflow RO-Crate" if "Galaxy" in source else "supplied", ""))
    how = next((v for k, v in HOW.items() if source.startswith(k)), f"Source: {source}.")
    if source.startswith("recreated") and manifest.get("methods"):  # then the /recrate agent described its methods
        how += " Its methods were then " + HOW["drafted"][0].lower() + HOW["drafted"][1:]
    return how


def export_crate(agent_dir: str | Path, out: str | Path | None = None) -> Path:
    """Write ``<out>.zip`` (default ``<agent>/exports/<name>-ro-crate.zip``). Returns its path."""
    from .agent import PaperAgent
    from .runcrate import render_preview

    agent = PaperAgent.load(agent_dir)
    if agent.crate_dir is None:
        raise ValueError("This research object has no RO-Crate yet.")
    out = Path(out) if out else agent.path / "exports" / f"{agent.name[:60]}-ro-crate.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="recrate-export-") as tmp:
        crate = Path(tmp) / "crate"
        shutil.copytree(agent.crate_dir, crate, ignore=shutil.ignore_patterns(".*", "__pycache__", "*.pyc"))
        meta_path = crate / "ro-crate-metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        graph = meta["@graph"]
        ids = {e.get("@id") for e in graph}
        root = next(e for e in graph if e.get("@id") == "./")
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        source = agent.manifest.get("crate_found", {}).get("url") or root.get("isBasedOn") or root.get("citation")
        source_ref = source if isinstance(source, dict) else ({"@id": source} if source else None)
        if "#recrate-software" not in ids:
            graph.append({"@id": "#recrate-software", "@type": "SoftwareApplication", "name": "ReCRATE",
                          "softwareVersion": __version__})
        graph.append({"@id": "#how-this-crate-was-made", "@type": "CreateAction",
                      "name": "How this RO-Crate was obtained",
                      "description": _how(agent.manifest) + (" Draft: a named person must confirm it." if
                                                            agent.manifest.get("draft") else ""),
                      "instrument": {"@id": "#recrate-software"}, "result": {"@id": "./"}, "endTime": now,
                      **({"object": source_ref} if source_ref else {}),
                      "actionStatus": {"@id": "http://schema.org/CompletedActionStatus"}})
        root.setdefault("mentions", [])
        root["mentions"] = (root["mentions"] if isinstance(root["mentions"], list) else [root["mentions"]]) + \
            [{"@id": "#how-this-crate-was-made"}]
        root.setdefault("datePublished", now)  # RO-Crate 1.1 requires one; the paper's date wins when known
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        (crate / "ro-crate-preview.html").write_text(render_preview(meta), encoding="utf-8")
        base = shutil.make_archive(str(out.with_suffix("")), "zip", crate)
    return Path(base)


VALIDATOR_PROFILES = {"https://w3id.org/workflowhub/workflow-ro-crate/1.0": "workflow-ro-crate-1.0",
                      "https://w3id.org/ro/wfrun/workflow/0.5": "workflow-run-crate-0.5",
                      "https://w3id.org/ro/wfrun/process/0.5": "process-run-crate-0.5"}


def validator() -> str | None:
    """The community RO-Crate validator (``pip install roc-validator``), if installed."""
    import sys

    exe = shutil.which("rocrate-validator") or str(Path(sys.executable).parent / "rocrate-validator")
    return exe if Path(exe).exists() else None


def validate_crate(agent_dir: str | Path) -> dict[str, Any]:
    """Validate exactly what an export contains with the RO-Crate validator, against the most specific profile the
    crate declares (e.g. Workflow RO-Crate 1.0, which includes RO-Crate 1.1). Required checks decide ``passed``; the
    recommended level is reported too, as things that could be better."""
    import subprocess
    import zipfile

    exe = validator()
    if exe is None:
        raise RuntimeError("The RO-Crate validator is not installed: pip install roc-validator")
    with tempfile.TemporaryDirectory(prefix="recrate-validate-") as tmp:
        z = export_crate(agent_dir, Path(tmp) / "crate.zip")
        crate = Path(tmp) / "crate"
        with zipfile.ZipFile(z) as f:
            f.extractall(crate)
        meta = json.loads((crate / "ro-crate-metadata.json").read_text(encoding="utf-8"))
        root = next(e for e in meta["@graph"] if e.get("@id") == "./")
        declared = root.get("conformsTo") or []
        declared = [d.get("@id") if isinstance(d, dict) else d for d in (declared if isinstance(declared, list) else [declared])]
        profile = next((VALIDATOR_PROFILES[d] for d in declared if d in VALIDATOR_PROFILES), "ro-crate-1.1")
        out: dict[str, Any] = {"profile": profile}
        for level in ("required", "recommended"):
            report = Path(tmp) / f"{level}.json"
            subprocess.run([exe, "validate", "-p", profile, "-l", level, "--no-paging", "-f", "json", "-o", str(report),
                            str(crate)], capture_output=True, text=True, timeout=600)
            r = json.loads(report.read_text(encoding="utf-8"))
            stats = r.get("statistics", {})
            out[level] = {"passed": bool(r.get("passed")), "checks": stats.get("total_checks", 0),
                          "passed_checks": stats.get("total_passed_checks", 0),
                          "issues": [{"check": (i.get("check") or {}).get("identifier", ""),
                                      "severity": i.get("severity", ""), "message": i.get("message", "")}
                                     for i in r.get("issues", [])]}
        out["passed"] = out["required"]["passed"]
        return out


def describe_validation(v: dict[str, Any]) -> str:
    req, rec = v["required"], v["recommended"]
    lines = [f"{'Valid' if v['passed'] else 'NOT valid'} {v['profile']}: {req['passed_checks']}/{req['checks']} required "
             f"checks pass; recommended level {rec['passed_checks']}/{rec['checks']}."]
    for i in req["issues"][:10]:
        lines.append(f"  ✗ {i['check']}: {i['message']}")
    if rec["issues"]:
        lines.append("Could be better (recommended):")
        lines += [f"  · {i['message']}" for i in rec["issues"][:8]]
    return "\n".join(lines)


def crate_fingerprint(agent_dir: str | Path) -> str:
    """Changes whenever the crate does: its metadata, and the names and sizes of its files."""
    import hashlib

    crate = Path(agent_dir) / "crate"
    h = hashlib.sha256()
    meta = crate / "ro-crate-metadata.json"
    if meta.exists():
        h.update(meta.read_bytes())
    for f in sorted(p for p in crate.rglob("*") if p.is_file() and not _incidental(p)):
        h.update(f"{f.relative_to(crate)}:{f.stat().st_size}".encode())
    return h.hexdigest()[:16]


def _incidental(p: Path) -> bool:
    """Files that appear on their own and say nothing about the crate (Finder's .DS_Store, Python's bytecode): they
    must not make a validated crate look changed, which used to re-run the validator while the app drew its page."""
    return p.name == ".DS_Store" or p.suffix == ".pyc" or "__pycache__" in p.parts


def crate_validation(agent_dir: str | Path, refresh: bool = False, run: bool = True) -> dict[str, Any] | None:
    """The validation of the research object's current RO-Crate, run when the crate is created and again whenever it
    changes. Stored in agent.json (``crate_validation``) with the crate's fingerprint. None without a crate or the
    validator, or with RECRATE_NO_VALIDATE set. ``run=False`` never starts the validator (it takes a minute or more:
    it fetches its profiles): the stored result comes back, marked ``stale`` when the crate changed since."""
    import os

    agent_dir = Path(agent_dir)
    manifest_path = agent_dir / "agent.json"
    if not (agent_dir / "crate" / "ro-crate-metadata.json").exists() or not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    stored = manifest.get("crate_validation")
    fp = crate_fingerprint(agent_dir)
    if stored and stored.get("fingerprint") == fp and not refresh:
        return stored
    if not run or os.environ.get("RECRATE_NO_VALIDATE") or validator() is None:
        return {**stored, "stale": True} if stored else None
    v = validate_crate(agent_dir)
    v.update(fingerprint=fp, when=datetime.now(timezone.utc).replace(microsecond=0).isoformat())
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))  # re-read: another step may have written it
    manifest["crate_validation"] = v
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return v
