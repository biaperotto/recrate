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

Then every method with example data and reported results is **reproduced**: it
is run on the paper's data and compared with what the paper reports, and climbs the verdict ladder
(materials found, runs, same numbers, same conclusion; see model.py). The result is a paper agent folder:

    paper_agents/my_paper/
      agent.json      manifest: paper, crate source, methods with their readiness and verdict ladder
      paper/          paper.md (text) and paper.json (title, DOI, code links)
      crate/          the input RO-Crate (supplied or drafted)
      code/           cloned repository, if any
      runs/           validation runs and every later run (Workflow Run Crates)

``recrate serve paper_agents/my_paper`` exposes it over MCP.
"""

from __future__ import annotations

import json
import os
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
    status: str  # validated | differs | not validated | failed: derived from the ladder, kept for compatibility
    detail: str = ""
    run_crate: str | None = None
    readiness: str = ""  # ready | ready-with-gaps | blocked (can it run here? not whether it reproduced)
    gaps: list[str] = field(default_factory=list)  # what the crate leaves open, or why it is blocked
    ladder: dict[str, dict[str, str]] = field(default_factory=dict)  # see model.py
    review: dict[str, Any] | None = None  # a person's review of the conclusion (the ladder's last step)


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

    def tables(self) -> list[dict]:
        from .reading import tables

        return tables(self.path / "paper")

    def figures(self) -> list[dict]:
        from .reading import figures

        return figures(self.path / "paper")

    @property
    def has_methods(self) -> bool:
        return self.crate_dir is not None and bool(list_methods(self.crate_dir))

    @property
    def environment(self):
        from .environment import load

        return load(self.path / "environment.json")

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
        from .model import verdict_sentence

        return {**{k: self.manifest.get(k) for k in ("name", "title", "doi", "crate_source", "code", "draft",
                                                      "published")},
                "verdict": (verdict_sentence({m.name: m.ladder for m in self.methods if m.ladder})
                            if any(m.ladder for m in self.methods) or not self.methods
                            else "Not checked yet: nothing reproduced so far."),
                "methods": [m.__dict__ for m in self.methods],
                "code_and_data_links": self.paper.repos if self.paper else []}

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


def runs_own_code(language: str | None) -> bool:
    """Whether running this method executes the workflow's own code (a script or macro), rather than
    ReCRATE's ports (Galaxy imaging tools). Code from an unknown source should only run when trusted."""
    return "galaxy" not in (language or "").lower()


def diagnose(spec: MethodSpec) -> tuple[str, list[str]]:
    """Readiness before anything runs: ``blocked`` (with why), ``ready-with-gaps`` (with the gaps) or ``ready``."""
    from .env import env_python, env_rscript
    from .profiles import get_profile
    from .runners import readiness

    blocked = readiness(spec.workflow_language, spec.workflow_path, env_python(spec.path.parent),
                        env_rscript(spec.path.parent), invocation=spec.invocation, entry=spec.entry, spec=spec)
    if blocked:
        return "blocked", [blocked]
    gaps = []
    if spec.needs_data and not (spec.example_data and spec.example_data.exists()):
        gaps.append("No published data in the crate: the method can be reused but not reproduced.")
    if not (spec.expected_results and spec.expected_results.exists()):
        gaps.append("No published results in the crate to compare with.")
    unsourced = [t.column for t in spec.tolerance_specs() if not t.source]
    if unsourced:
        gaps.append(f"Tolerance with no stated source: {', '.join(unsourced)} (no verdict on those numbers).")
    gaps += get_profile(spec.profile).diagnose_inputs(spec)
    return ("ready-with-gaps" if gaps else "ready"), gaps


def validate_methods(crate_dir: Path, runs_dir: Path, runner: str = "auto", who: str = "ReCRATE validation",
                     validate: str = "all") -> list[MethodStatus]:
    """Reproduce every method that has example data and reported results, and climb its verdict ladder.

    ``validate``: ``all``; ``safe`` (only methods that don't execute the workflow's own code); ``none``.
    Methods run independently: one failing doesn't stop the others.
    """
    from .model import build_ladder, derived_status
    from .pipeline import reproduce  # local import: pipeline imports heavy deps

    out = []
    for m in list_methods(crate_dir):
        spec = load_crate(crate_dir, m["id"])
        st = MethodStatus(m["name"], m["id"], m["description"], m["language"], "not validated")
        st.readiness, st.gaps = diagnose(spec)
        comparison, error, blocked = None, None, None
        if st.readiness == "blocked":
            blocked = st.detail = st.gaps[0]
        elif spec.needs_data and not (spec.example_data and spec.example_data.exists()):
            st.detail = "No example data in the crate."
        elif validate == "none" or (validate == "safe" and runs_own_code(m["language"])):
            st.detail = ("Not run yet: validating executes the workflow's own code. "
                         "Run it when you trust the source.")
        else:
            try:
                r = reproduce(spec, runs_dir, runner=runner, agent=who)
                comparison = r.comparison
                st.detail = r.comparison.headline() if r.comparison else \
                    "Ran on the published data; the paper publishes no results for this method to compare with."
                st.run_crate = str(r.run_dir)
            except Exception as e:  # noqa: BLE001 - report any failure as the method's status
                error = st.detail = f"{type(e).__name__}: {e}"
        st.ladder = build_ladder(spec, comparison, error=error, blocked=blocked, ran=st.run_crate is not None)
        st.status = derived_status(st.ladder)
        out.append(st)
    return out


def record_reproduction(agent_dir: str | Path, method: str, outcome) -> MethodStatus | None:
    """Make a new reproduction the method's current one in agent.json (its ladder, status and run crate). Earlier
    runs stay in runs/: the record is append-only. A bare crate folder (no agent.json) is left alone."""
    from .model import build_ladder, derived_status

    agent_dir = Path(agent_dir)
    if not (agent_dir / MANIFEST).exists():
        return None
    manifest = json.loads((agent_dir / MANIFEST).read_text(encoding="utf-8"))
    m = next((x for x in manifest.get("methods", []) if method in (x["id"], x["name"])), None)
    if m is None:
        return None
    spec = load_crate(agent_dir / "crate", m["id"])
    m["ladder"] = outcome.ladder or build_ladder(spec, outcome.comparison, ran=True)
    m["status"] = derived_status(m["ladder"])
    m["detail"] = outcome.comparison.headline() if outcome.comparison else ""
    m["run_crate"], m["review"] = str(outcome.run_dir), None
    (agent_dir / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return MethodStatus(**m)


def review_conclusion(agent_dir: str | Path, method: str, who: str, agrees: str, note: str = "") -> MethodStatus:
    """A person's answer to the ladder's last step for one method: does the reproduction support the paper's
    conclusion? (``agrees``: yes | partly | no). Stored in agent.json and in the run crate of the reproduction."""
    from .model import build_ladder, derived_status
    from .runcrate import review_conclusion as record

    agent_dir = Path(agent_dir)
    manifest = json.loads((agent_dir / MANIFEST).read_text(encoding="utf-8"))
    m = next((x for x in manifest.get("methods", []) if method in (x["id"], x["name"])), None)
    if m is None:
        raise CrateError(f"No method {method!r} in this agent.")
    if not m.get("run_crate"):
        raise CrateError(f"{m['name']} has no reproduction to review yet: reproduce it first.")
    record(Path(m["run_crate"]), who, agrees, note)
    m["review"] = {"by": who, "agrees": agrees, "note": note,
                   "when": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "run": m["run_crate"]}
    ladder = m.get("ladder") or build_ladder(load_crate(agent_dir / "crate", m["id"]))
    ladder["conclusion"] = build_ladder(load_crate(agent_dir / "crate", m["id"]), review=m["review"])["conclusion"]
    m["ladder"], m["status"] = ladder, derived_status(ladder)
    (agent_dir / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return MethodStatus(**m)


def agentify(out: str | Path, paper: str | Path | None = None, crate: str | Path | None = None,
             repo: str | None = None, fetch_code: bool = False, draft: str = "none",
             runner: str = "auto", strict: bool = False, validate: str = "all",
             online: bool = True, search_crate: bool = True, paper_doi: str | None = None) -> PaperAgent:
    """Build a paper agent folder. See the module docstring."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    online = online and not os.environ.get("RECRATE_OFFLINE")  # registries and DOI metadata need the network
    manifest: dict[str, Any] = {"recrate_version": __version__,
                                "created": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}

    # A paper without a crate: first look for one already published (WorkflowHub, Zenodo, DataCite, its code)
    if paper and not crate and online and search_crate:
        crate = _find_published_crate(Path(paper), out, manifest, paper_doi, repo)

    # A Galaxy Workflow RO-Crate (e.g. from WorkflowHub): read its method from the workflow and its tests
    galaxy_src = None
    if crate:
        from .galaxy import is_galaxy_crate

        if is_galaxy_crate(resolve_crate_dir(crate)):
            galaxy_src = resolve_crate_dir(crate)
            if not paper and (galaxy_src / "README.md").exists():
                paper = galaxy_src / "README.md"  # no article: answer from the workflow's own documentation

    # A Nextflow (nf-core), CWL or Snakemake crate: read its method in that community's own conventions
    eco = None
    if crate and galaxy_src is None:
        from .adapters import detect_ecosystem

        eco_src = resolve_crate_dir(crate)
        eco = detect_ecosystem(eco_src)
        if eco and not paper and (eco_src / "README.md").exists():
            paper = eco_src / "README.md"

    # 1. The paper, as an agent can read it: text by section, tables, figures (see reading.py)
    p: Paper | None = None
    if paper:
        p = read_paper(paper)
        save_paper(p, out / "paper")
        p = _reading_package(p, Path(paper), out / "paper", manifest, paper_doi, online)

    # 2. What the paper links to (its own code and data, and what it cites), and its own code, cloned
    code_dir = None
    links: list[dict[str, Any]] = []
    if p is not None:
        from .links import classify, survey_code, zenodo_title

        links = classify(p)
        if online:
            from .sources import get_json

            for link in links:
                if "zenodo" in link["url"]:
                    try:
                        link.update({k: v for k, v in zenodo_title(link["url"], get_json).items() if v})
                    except Exception:  # noqa: BLE001 - metadata is a nicety
                        pass
        manifest["links"] = links
        own_code = next((l["url"] for l in links if l["kind"] == "code" and l["own"]), None)
        if repo is None and (fetch_code or (online and not crate)):  # no crate to run from: the authors' code
            repo = own_code or (next((l["url"] for l in links if l["kind"] == "code"), None) if fetch_code else None)
    if repo:
        try:
            manifest["code"] = _clone(repo, out / "code")
            code_dir = out / "code"
            from .links import survey_code

            manifest["code_survey"] = survey_code(code_dir)
            readme = code_dir / (manifest["code_survey"].get("readme") or "README.md")
            if readme.exists():  # the code's archived, citable release, when its README gives one
                for doi in dict.fromkeys(re.findall(r"10\.5281/zenodo\.\d+", readme.read_text(errors="replace"))):
                    url = f"https://doi.org/{doi}"
                    if all(l["url"] != url for l in links):
                        links.append({"url": url, "kind": "code", "own": True,
                                      "why": "the archived release of the code (from its README)"})
            manifest["links"] = links
        except Exception as e:  # noqa: BLE001 - the paper is still read; say why the code isn't there
            manifest["code_error"] = f"{repo}: {e}"

    # 3. The crate: supplied, drafted, or none yet
    crate_out = out / "crate"
    if galaxy_src is not None:
        from .galaxy import import_galaxy_crate

        for d in (crate_out, out / "source-crate"):
            if d.exists():
                shutil.rmtree(d)
        shutil.copytree(galaxy_src, out / "source-crate")  # the original, unchanged
        import_galaxy_crate(galaxy_src, crate_out)
        manifest["crate_source"] = "derived from a Galaxy Workflow RO-Crate"
        if manifest.get("crate_found"):
            manifest["crate_source"] = f"found on {manifest['crate_found']['where']}, derived from its Galaxy workflow"
    elif eco:
        from .adapters import LABELS, import_crate

        src = resolve_crate_dir(crate)
        for d in (crate_out, out / "source-crate"):
            if d.exists():
                shutil.rmtree(d)
        shutil.copytree(src, out / "source-crate")  # the original, unchanged
        import_crate(src, crate_out)
        manifest["crate_source"] = f"derived from a {LABELS[eco]} Workflow RO-Crate"
        if manifest.get("crate_found"):
            manifest["crate_source"] = f"found on {manifest['crate_found']['where']}, derived from its {LABELS[eco]} workflow"
    elif crate:
        src = resolve_crate_dir(crate)
        if src.resolve() != crate_out.resolve():
            if crate_out.exists():
                shutil.rmtree(crate_out)
            shutil.copytree(src, crate_out)
        manifest["crate_source"] = (f"found on {manifest['crate_found']['where']}" if manifest.get("crate_found")
                                    else "supplied")
    elif draft == "claude":
        if p is None:
            raise ValueError("Drafting a crate needs the paper (--paper).")
        from .draft import build_crate
        from .drafter import draft_description

        desc = draft_description(p.text, base=out, code_dir=code_dir)
        (out / "crate_description.json").write_text(json.dumps(desc, indent=2, ensure_ascii=False), encoding="utf-8")
        build_crate(desc, crate_out, base_dir=out)
        manifest["crate_source"] = "drafted"
    else:
        manifest["crate_source"] = "none"
        if p is not None:
            (out / "DRAFT_ME.md").write_text(_draft_instructions(p, code_dir, out), encoding="utf-8")

    # 3b. The software environment as of publication
    from . import environment as envmod
    from .paper_crate import add_environment, build_paper_crate

    env = None
    if p is not None and manifest["crate_source"] in {"none", "drafted"}:
        fetch_json = None
        if online:
            from .sources import get_json as fetch_json
        arxiv = next((d.split("arxiv.", 1)[-1] for d in p.dois if d.lower().startswith("10.48550/arxiv.")), None)
        if arxiv is None:  # "arXiv:1806.03535" in the text, or a file named after its arXiv identifier
            m = re.search(r"arxiv[-:_ ]?\s*(\d{4}\.\d{4,5})", f"{Path(p.source).name} {p.text[:4000]}", re.I)
            arxiv = m.group(1) if m else None
        when, when_src = envmod.publication_date(p.text, doi=p.doi, arxiv=arxiv, pdf=Path(p.source),
                                                 fetch_json=fetch_json)
        env = envmod.environment_for(p.text, when, when_src, code_dir=code_dir, fetch_json=fetch_json)
        if manifest["crate_source"] == "none":
            build_paper_crate(p, env, crate_out, code_url=manifest.get("code"), paper_file=Path(p.source),
                              paper_id=None if p.doi or not arxiv else f"https://arxiv.org/abs/{arxiv}",
                              links=manifest.get("links"), meta=_paper_metadata(own_doi(p, paper_doi), fetch_json))
            manifest["crate_source"] = "recreated from the paper"
        else:
            add_environment(crate_out, env)
    elif (crate_out / "ro-crate-metadata.json").exists():
        env = envmod.from_crate(crate_out)
        if env.published is None and p is not None:  # the crate has no date: use the paper's
            when, when_src = envmod.publication_date(p.text, pdf=Path(p.source))
            env.published, env.published_source = (when.isoformat() if when else None), when_src
    if env is not None:
        envmod.save(env, out / "environment.json")
        manifest["published"] = env.published

    # 4. Names
    has_crate = (crate_out / "ro-crate-metadata.json").exists()
    spec0 = load_crate(crate_out) if has_crate and list_methods(crate_out) else None
    root0 = _crate_root(crate_out) if has_crate else {}
    manifest["title"] = (spec0.name if spec0 else None) or root0.get("name") or (p.title if p else out.name)
    manifest["name"] = _slug(manifest["title"])
    cit = (root0.get("citation") or {}).get("@id") if isinstance(root0.get("citation"), dict) else root0.get("citation")
    manifest["doi"] = (spec0.citation if spec0 else None) or cit or (f"https://doi.org/{p.doi}" if p and p.doi else None)
    manifest["draft"] = bool(spec0.draft) if spec0 else manifest["crate_source"] == "recreated from the paper"
    manifest["paper"] = str(paper) if paper else None

    # 5. Validate each method against the paper's reported results
    methods: list[MethodStatus] = []
    if spec0 is not None:
        methods = validate_methods(crate_out, out / "runs", runner=runner, validate=validate)
        if strict:
            methods = [m for m in methods if m.status == "validated"]
    manifest["methods"] = [m.__dict__ for m in methods]
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    _validate_crate(out)
    return PaperAgent.load(out)


def _validate_crate(agent_dir: Path) -> None:
    """Validate the RO-Crate as soon as it exists or changes (found, supplied, recreated or drafted)."""
    from .export import crate_validation

    try:
        crate_validation(agent_dir)
    except Exception as e:  # noqa: BLE001 - a validator problem must not stop the research object being built
        manifest = json.loads((agent_dir / MANIFEST).read_text(encoding="utf-8"))
        manifest["crate_validation"] = {"error": f"{type(e).__name__}: {e}"}
        (agent_dir / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def _reading_package(p: Paper, source: Path, pdir: Path, manifest: dict[str, Any], paper_doi: str | None,
                     online: bool) -> Paper:
    """Prefer a structured reading package (Europe PMC) to PDF text; else keep the PDF's figures."""
    from .reading import copy_package, figures_from_pdf, from_europepmc

    if source.name == "paper.md" and copy_package(source.parent, pdir):  # fetched as a package already
        manifest["paper_text"] = "reading package (Europe PMC)"
        return p
    doi = own_doi(p, paper_doi)
    if online and doi and not doi.lower().startswith("10.48550/"):
        from .sources import get_bytes, get_json

        tmp = pdir.parent / ".reading-package"
        try:
            pkg = from_europepmc(doi, tmp, get_json, get_bytes)
        except Exception:  # noqa: BLE001 - not available: keep the PDF text
            pkg = None
        if pkg:
            shutil.copy2(pkg, pdir / "paper.md")
            copy_package(tmp, pdir)
            shutil.rmtree(tmp, ignore_errors=True)
            manifest["paper_text"] = "reading package (Europe PMC structured full text)"
            new = read_paper(pdir / "paper.md")
            return Paper(title=p.title, text=new.text, source=p.source, dois=list(dict.fromkeys(p.dois + new.dois)),
                         repos=list(dict.fromkeys(p.repos + new.repos)))
        shutil.rmtree(tmp, ignore_errors=True)
    if source.suffix.lower() == ".pdf":
        n = len(figures_from_pdf(source, pdir / "figures"))
        manifest["paper_text"] = f"PDF text, with {n} embedded image(s) kept" if n else "PDF text"
    return p


def _paper_metadata(doi: str | None, fetch_json) -> dict[str, Any]:
    """The paper's licence and authors (with ORCIDs) from its DOI metadata; empty offline or without a DOI."""
    if not doi or fetch_json is None:
        return {}
    try:
        csl = fetch_json(f"https://doi.org/{doi}", "application/vnd.citationstyles.csl+json")
    except Exception:  # noqa: BLE001 - the crate then says the licence is not stated
        return {}
    lic = next((l.get("URL") for l in csl.get("license") or [] if l.get("content-version") in (None, "vor", "unspecified")),
               None) or next((l.get("URL") for l in csl.get("license") or []), None)
    authors = [{"name": f"{a.get('given', '')} {a.get('family', '')}".strip() or a.get("name", ""),
                "orcid": (a.get("ORCID") or "").replace("http://", "https://") or None}
               for a in csl.get("author") or []]
    return {"license": lic, "authors": [a for a in authors if a["name"]], "publisher": csl.get("publisher")}


def own_doi(p: Paper, given: str | None = None) -> str | None:
    """The paper's own DOI: as given (its DOI or arXiv link), else one printed on its first page (a DOI further
    down is more likely a reference)."""
    if given:
        return given
    m = re.search(r"arxiv[-:_ ]?\s*(\d{4}\.\d{4,5})", f"{Path(p.source).name} {p.text[:4000]}", re.I)
    if m:
        return f"10.48550/arXiv.{m.group(1)}"
    head = p.text[:3000]
    return next((d for d in p.dois if d in head), None)


def _find_published_crate(paper: Path, out: Path, manifest: dict[str, Any], paper_doi: str | None,
                          repo: str | None) -> Path | None:
    """Search for an RO-Crate already published for this paper; fetch the best one. Records the search."""
    from .crate_search import repos_in, search_crates
    from .sources import crate_root, fetch

    p = read_paper(paper)
    doi = own_doi(p, paper_doi)
    res = search_crates(doi, p.text, repos_in(p.text) + ([repo] if repo else []))
    manifest["crate_search"] = {"doi": doi, "log": res.log, "summary": res.summary(),
                                "candidates": [c.__dict__ | {"ref": c.ref.__dict__} for c in res.candidates]}
    best = res.best
    if best is None:
        return None
    dest = out / "found-crate"
    if dest.exists():
        shutil.rmtree(dest)
    try:
        if best.ref.kind == "repo":
            _clone(best.ref.id, dest / "repo")
            found = crate_root(dest / "repo")
        else:
            f = fetch(best.ref, dest)
            found = f.path if f.kind == "crate" else None
    except Exception as e:  # noqa: BLE001 - a crate that can't be fetched: say so, and recreate instead
        manifest["crate_search"]["fetch_error"] = f"{best.label}: {type(e).__name__}: {e}"
        return None
    if found is None:
        manifest["crate_search"]["fetch_error"] = f"{best.label} is not an RO-Crate after all"
        return None
    manifest["crate_found"] = {"where": best.where, "label": best.label, "url": best.url, "evidence": best.evidence}
    return found


def _crate_root(crate_dir: Path) -> dict[str, Any]:
    meta = json.loads((Path(crate_dir) / "ro-crate-metadata.json").read_text(encoding="utf-8"))
    return next((e for e in meta.get("@graph", []) if e.get("@id") == "./"), {})


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
    from .environment import load as load_env
    from .paper_crate import add_environment

    env = load_env(agent_dir / "environment.json")
    if env is not None and not (crate_dir / "environment").exists():  # drafting rewrote the crate: keep the dates
        add_environment(crate_dir, env)
    spec0 = load_crate(crate_dir)
    manifest["methods"] = [m.__dict__ for m in validate_methods(crate_dir, agent_dir / "runs", runner=runner)]
    manifest["crate_source"] = manifest.get("crate_source") if manifest.get("crate_source") != "none" else "drafted"
    manifest["title"] = spec0.name
    manifest["name"] = _slug(spec0.name)  # the name a PDF's page header gave is replaced by the crate's title
    manifest["draft"] = spec0.draft
    (agent_dir / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    _validate_crate(agent_dir)
    return PaperAgent.load(agent_dir)
