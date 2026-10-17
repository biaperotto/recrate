"""Agentify a paper: turn it into a paper agent you can talk to and run on your data.

    recrate agentify --paper paper.pdf [--crate crate/] [--repo URL] --out paper_agents/my_paper

Inputs, in order of preference:

* **paper + RO-Crate** (from the authors, WorkflowHub or Zenodo): the crate
  declares each method, its inputs, parameters, example data and reported
  results. Nothing is guessed.
* **paper only** (as in Paper2Agent): ReCRATE reads the paper, finds its code
  and DOI, and drafts the crate (with Claude via the API, or interactively via
  the ``/agentify`` Claude Code command). The draft is flagged until confirmed.
* **paper with no runnable code**: the agent still answers questions from the
  paper (Paper2Agent's resource layer); methods can be added later.

Then every method with example data and reported results is **validated**: it
is run on the paper's data and compared with what the paper reports. The result
is a paper agent folder:

    paper_agents/my_paper/
      agent.json      manifest: paper, crate source, methods and their validation
      paper/          paper.md (text) and paper.json (title, DOI, code links)
      crate/          the input RO-Crate (supplied or drafted)
      code/           cloned repository, if any
      runs/           validation runs and every later run (Workflow Run Crates)

``recrate serve paper_agents/my_paper`` exposes it over MCP.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .crate import CrateError, MethodSpec, list_methods, load_crate, resolve_crate_dir
from .paper import Paper, load_saved_paper, read_paper, save_paper, search

MANIFEST = "agent.json"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(s) > 60:
        s = s[:60].rsplit("-", 1)[0]
    return s or "paper"


@dataclass
class MethodStatus:
    name: str
    id: str
    description: str
    language: str
    status: str  # validated | differs | not validated | failed
    detail: str = ""
    run_crate: str | None = None


@dataclass
class PaperAgent:
    path: Path
    manifest: dict[str, Any]
    paper: Paper | None = None
    methods: list[MethodStatus] = field(default_factory=list)
    crate_override: Path | None = None

    @property
    def name(self) -> str:
        return self.manifest.get("name", self.path.name)

    @property
    def crate_dir(self) -> Path | None:
        if self.crate_override is not None:
            return self.crate_override
        d = self.path / "crate"
        return d if (d / "ro-crate-metadata.json").exists() else None

    @property
    def runs_dir(self) -> Path:
        return self.path / "runs"

    def spec(self, method: str | None = None) -> MethodSpec:
        if self.crate_dir is None:
            raise CrateError("This paper agent has no crate yet, so no runnable methods. "
                             "Draft one with `recrate agentify --draft claude` or the /agentify command.")
        return load_crate(self.crate_dir, method)

    def search(self, query: str, k: int = 5) -> list[dict]:
        return search(self.paper.text, query, k) if self.paper else []

    def card(self) -> dict[str, Any]:
        return {**{k: self.manifest.get(k) for k in
                   ("name", "title", "doi", "crate_source", "crate_url", "code", "draft", "readiness")},
                "methods": [m.__dict__ for m in self.methods],
                "code_and_data_links": self.paper.repos if self.paper else []}

    def diagnosis(self) -> dict[str, Any] | None:
        return self.manifest.get("diagnosis")

    @classmethod
    def load(cls, path: str | Path) -> "PaperAgent":
        path = Path(path)
        if not (path / MANIFEST).exists():
            # A bare crate folder also works as a (paper-less) agent
            crate = resolve_crate_dir(path)
            spec = load_crate(crate)
            methods = [MethodStatus(m["name"], m["id"], m["description"], m["language"], "not validated")
                       for m in list_methods(crate)]
            agent = cls(path=crate.parent, manifest={"name": spec.name, "title": spec.name,
                                                    "doi": spec.citation, "crate_source": "supplied"},
                        methods=methods, crate_override=crate)
            return agent
        manifest = json.loads((path / MANIFEST).read_text(encoding="utf-8"))
        methods = [MethodStatus(**m) for m in manifest.get("methods", [])]
        return cls(path=path, manifest=manifest, paper=load_saved_paper(path / "paper"), methods=methods)


def _clone(repo: str, dest: Path) -> str:
    if dest.exists():
        shutil.rmtree(dest)
    proc = subprocess.run(["git", "clone", "--depth", "1", repo, str(dest)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Could not clone {repo}: {proc.stderr.strip()[-500:]}")
    return repo


def validate_methods(crate_dir: Path, runs_dir: Path, runner: str = "auto", who: str = "ReCRATE validation") \
        -> list[MethodStatus]:
    """Reproduce every method that has example data and reported results."""
    from .pipeline import reproduce  # local import: pipeline imports heavy deps

    out = []
    for m in list_methods(crate_dir):
        spec = load_crate(crate_dir, m["id"])
        st = MethodStatus(m["name"], m["id"], m["description"], m["language"], "not validated")
        if not (spec.example_data and spec.example_data.exists()):
            st.detail = "No example data in the crate."
        elif not (spec.expected_results and spec.expected_results.exists()):
            st.detail = "No reported results in the crate to compare with."
        else:
            try:
                r = reproduce(spec, runs_dir, runner=runner, agent=who)
                st.status = "validated" if r.comparison and r.comparison.reproduced else "differs"
                st.detail = r.comparison.headline() if r.comparison else ""
                st.run_crate = str(r.run_dir)
            except Exception as e:  # noqa: BLE001 - report any failure as the method's status
                st.status, st.detail = "failed", f"{type(e).__name__}: {e}"
        out.append(st)
    return out


def agentify(out: str | Path, paper: str | Path | None = None, crate: str | Path | None = None,
             repo: str | None = None, fetch_code: bool = False, draft: str = "none",
             runner: str = "auto", strict: bool = False, locate: bool = False,
             pdf: str | None = None) -> PaperAgent:
    """Build a paper agent folder. See the module docstring.

    ``paper`` may be a local file or a link (DOI / URL / arXiv / bioRxiv). With
    ``locate`` and no supplied ``crate``, the Diagnose step finds the paper's
    RO-Crate; whatever crate results is diagnosed and the diagnosis and crate
    source are stored in the manifest before methods are validated as before.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"recrate_version": __version__,
                                "created": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}

    # 1. The paper: a local file, or a link to fetch (M0).
    p: Paper | None = None
    fetched = None
    if paper:
        if Path(str(paper)).expanduser().is_file():
            p = read_paper(paper)
        else:
            from .fetch import fetch_paper

            fetched = fetch_paper(str(paper), out / "work", pdf=pdf)
            p = fetched.paper
            manifest["landing_url"] = fetched.landing_url
            if not fetched.full_text:
                manifest["paper_note"] = fetched.message
        if p is not None:
            save_paper(p, out / "paper")

    # 2. The code
    code_dir = None
    if repo is None and fetch_code and p is not None:
        repo = next((r for r in p.repos if any(h in r for h in ("github.com", "gitlab.com", "codeberg.org"))), None)
    if repo:
        manifest["code"] = _clone(repo, out / "code")
        code_dir = out / "code"

    # 3. The crate: supplied, drafted, or none yet
    crate_out = out / "crate"
    if crate:
        src = resolve_crate_dir(crate)
        if src.resolve() != crate_out.resolve():
            if crate_out.exists():
                shutil.rmtree(crate_out)
            shutil.copytree(src, crate_out)
        manifest["crate_source"] = "supplied"
    elif locate and p is not None:
        from .locate import locate_crate

        signposting = fetched.signposting if fetched else []
        res = locate_crate(p, signposting, crate_out)
        res.write_json(out / "locate.json")
        if res.found:
            manifest["crate_source"] = "found"
            manifest["crate_url"] = res.source_url
        elif draft == "claude":
            _draft_crate(p, out, crate_out, code_dir)
            manifest["crate_source"] = "drafted"
        else:
            manifest["crate_source"] = "none"
            (out / "DRAFT_ME.md").write_text(_draft_instructions(p, code_dir, out), encoding="utf-8")
    elif draft == "claude":
        if p is None:
            raise ValueError("Drafting a crate needs the paper (--paper).")
        _draft_crate(p, out, crate_out, code_dir)
        manifest["crate_source"] = "drafted"
    else:
        manifest["crate_source"] = "none"
        if p is not None:
            (out / "DRAFT_ME.md").write_text(_draft_instructions(p, code_dir, out), encoding="utf-8")

    # 4. Names
    spec0 = load_crate(crate_out) if (crate_out / "ro-crate-metadata.json").exists() else None
    manifest["title"] = (spec0.name if spec0 else None) or (p.title if p else out.name)
    manifest["name"] = _slug(manifest["title"])
    manifest["doi"] = (spec0.citation if spec0 else None) or (f"https://doi.org/{p.doi}" if p and p.doi else None)
    manifest["draft"] = bool(spec0.draft) if spec0 else False
    manifest["paper"] = str(paper) if paper else None

    # 4b. Diagnose the crate (M2): store readiness and findings for the agent to explain.
    if spec0 is not None:
        from .diagnose import diagnose_crate

        diag = diagnose_crate(crate_out, paper_doi=(p.doi if p else None),
                              crate_source=manifest["crate_source"],
                              source_url=manifest.get("crate_url"))
        manifest["diagnosis"] = diag.to_json()
        manifest["readiness"] = diag.readiness

    # 5. Validate each method against the paper's reported results
    methods: list[MethodStatus] = []
    if spec0 is not None:
        methods = validate_methods(crate_out, out / "runs", runner=runner)
        if strict:
            methods = [m for m in methods if m.status == "validated"]
    manifest["methods"] = [m.__dict__ for m in methods]
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return PaperAgent.load(out)


def _draft_crate(p: Paper, out: Path, crate_out: Path, code_dir: Path | None) -> None:
    from .draft import build_crate
    from .drafter import draft_description

    desc = draft_description(p.text, base=out, code_dir=code_dir)
    (out / "crate_description.json").write_text(json.dumps(desc, indent=2, ensure_ascii=False), encoding="utf-8")
    build_crate(desc, crate_out, base_dir=out)


def _draft_instructions(p: Paper, code_dir: Path | None, out: Path) -> str:
    links = "\n".join(f"- {r}" for r in p.repos[:10]) or "- (none found in the text)"
    return f"""# Draft the crate for: {p.title}

This paper agent can already answer questions about the paper. To make its methods
runnable on new data, describe them in a crate:

1. Open this repository in Claude Code and run `/agentify {out}` (see .claude/commands/agentify.md), or
2. set ANTHROPIC_API_KEY and run `recrate agentify --paper {p.source} --draft claude --out {out}`, or
3. write `crate_description.json` by hand (format: `python -c "import recrate.draft as d; print(d.__doc__)"`)
   and run `recrate draft crate_description.json {out}/crate` then `recrate validate {out}`.

DOI found: {p.doi or 'none'}
Code and data links found in the paper:
{links}
Code cloned: {code_dir or 'no (use --repo URL or --fetch-code)'}
"""


def revalidate(agent_dir: str | Path, runner: str = "auto") -> PaperAgent:
    agent_dir = Path(agent_dir)
    manifest = json.loads((agent_dir / MANIFEST).read_text(encoding="utf-8"))
    crate_dir = agent_dir / "crate"
    spec0 = load_crate(crate_dir)
    manifest["methods"] = [m.__dict__ for m in validate_methods(crate_dir, agent_dir / "runs", runner=runner)]
    manifest["crate_source"] = manifest.get("crate_source") if manifest.get("crate_source") != "none" else "drafted"
    manifest["title"] = spec0.name
    manifest["draft"] = spec0.draft
    (agent_dir / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return PaperAgent.load(agent_dir)
