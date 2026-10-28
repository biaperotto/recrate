"""Adapters: read a published workflow crate in its own community's conventions.

Most crates on WorkflowHub are not written for ReCRATE: a Nextflow pipeline takes ``--input samplesheet.csv``, a CWL
workflow a job file, a Snakemake workflow a config file, and none of them ``--input_dir``. Each community does,
however, state the three things ReCRATE needs: the parameters (nf-core's ``nextflow_schema.json``, CWL's ``inputs``,
Snakemake's ``config/config.yaml`` and its schema), an example run (nf-core's ``conf/test.config``, a cwltest
``tests.yml`` with its job, Snakemake's ``.test/config``), and sometimes what that run must produce (cwltest output
checksums). ``import_crate`` reads them and writes a ReCRATE input crate next to the published one, which is left
untouched (as galaxy.import_galaxy_crate does), with an **invocation recipe** on the workflow entity
(``recrate invocation``, docs/crate-spec.md) that tells runners/engines.py how to call the workflow.

What the adapters read, by ecosystem:

* **nf-core / Nextflow**: ``nextflow_schema.json`` (parameters, which are files), ``conf/test.config`` (the published
  test run: local files become example data, remote ones are listed), ``tests/*.nf.test.snap`` (the nf-test snapshot
  of what the test produces: md5 per pinned file, the names of the others, as the expected results); runs
  ``nextflow run <wf> -profile test,<runtime>``, which needs the network and a container runtime (docker, singularity,
  apptainer) or conda / mamba / micromamba (each tool installed into an environment instead).
* **CWL**: the crate's ``FormalParameter``s (WorkflowHub writes them from the workflow), else the workflow's ``inputs``;
  a cwltest ``tests.yml`` gives the job (example data) and the expected outputs' checksums and sizes.
* **Jupyter notebooks** (WorkflowHub's Jupyter class): run as published with papermill; the data folder injected
  into the notebook's path variable (its cell tagged ``parameters`` in the run's copy); the printed output each cell
  stored as the expected results (``code-stated``), its stored images as the published figures.
* **Snakemake**: ``config/config.yaml`` with ``workflow/schemas/config.schema.yaml``; the authors' ``run_test.sh``
  (one ``snakemake --snakefile … --configfile …`` call per step, in order, with their workflow profile and
  ``prep.sh --what check`` as a preflight) as the published test run, else ``.test/config/config.yaml`` or
  ``config/test*.yaml``; ``container:`` directives need singularity or apptainer, ``conda:`` ones conda or mamba;
  test inputs that are DVC pointers are named, with the remote to fetch them from.

Expected results from a test are recorded with the tolerance source ``test-stated`` (the test file is the evidence).
No test, no reproduction: the method is reusable only, and the gaps say so.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

LABELS = {"nfcore": "Nextflow", "cwl": "CWL", "snakemake": "Snakemake", "notebook": "Jupyter"}
# import name -> PyPI name, where they differ (a notebook names what it imports, not what to install)
PYPI = {"sklearn": "scikit-learn", "skimage": "scikit-image", "cv2": "opencv-python", "PIL": "pillow", "yaml": "pyyaml",
        "bs4": "beautifulsoup4", "Bio": "biopython", "IPython": "ipython", "mpl_toolkits": "matplotlib", "nb_conda": "",
        "google": "", "attr": "attrs", "dateutil": "python-dateutil", "dotenv": "python-dotenv", "tf": "tensorflow"}
STDLIB = {"os", "sys", "re", "csv", "json", "glob", "time", "math", "random", "zipfile", "io", "pathlib", "collections",
          "itertools", "functools", "datetime", "shutil", "subprocess", "typing", "warnings", "copy", "pickle", "gzip",
          "string", "struct", "tempfile", "urllib", "statistics", "argparse", "logging", "operator", "textwrap", "abc",
          "enum", "dataclasses", "decimal", "fractions", "hashlib", "heapq", "numbers", "pprint", "queue", "threading",
          "multiprocessing", "concurrent", "html", "http", "xml", "tarfile", "unittest", "traceback", "inspect", "ast"}
RUNTIMES = ["docker", "singularity", "apptainer", "conda", "mamba", "micromamba"]  # the first installed is used
NF_SKIP_GROUPS = {"institutional_config_options", "max_job_request_options", "generic_options"}
NF_FILE_FORMATS = {"file-path", "directory-path", "path", "file-path-pattern"}
MAX_PARAMS = 40  # value parameters exposed as tool arguments; the rest stay at the workflow's defaults
CWL_TYPES = {"int": "Integer", "long": "Integer", "float": "Float", "double": "Float", "boolean": "Boolean",
             "string": "Text", "File": "File", "Directory": "Directory"}
JSON_TYPES = {"integer": "Integer", "number": "Float", "boolean": "Boolean", "string": "Text"}


def _as_list(x: Any) -> list[Any]:
    return x if isinstance(x, list) else [] if x is None else [x]


def _meta(crate_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    raw = json.loads((crate_dir / "ro-crate-metadata.json").read_text(encoding="utf-8"))
    idx = {e["@id"]: e for e in raw["@graph"] if "@id" in e}
    desc = idx.get("ro-crate-metadata.json", {})
    root = idx.get((desc.get("about") or {}).get("@id") or "./") or idx.get("./", {})
    return idx, root


def _main(idx: dict[str, dict[str, Any]], root: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    """(workflow id, its entity, language name) for the crate's main workflow."""
    wid = (root.get("mainEntity") or {}).get("@id") or ""
    if not wid:
        wid = next((i for i, e in idx.items() if "ComputationalWorkflow" in _as_list(e.get("@type"))), "")
    wf = idx.get(wid, {})
    lang = idx.get((wf.get("programmingLanguage") or {}).get("@id") or "", {})
    return wid, wf, str(lang.get("name") or lang.get("@id") or "").lower()


def detect_ecosystem(crate_dir: str | Path) -> str | None:
    """``nfcore``, ``cwl`` or ``snakemake`` when an adapter knows how to call this crate's workflow, else None."""
    crate_dir = Path(crate_dir)
    if not (crate_dir / "ro-crate-metadata.json").exists():
        return None
    idx, root = _meta(crate_dir)
    if any(isinstance(e.get("name"), str) and e["name"] in {"domain profile", "recrate invocation"} for e in idx.values()):
        return None  # already a ReCRATE crate: it says how to run its methods
    wid, _, lang = _main(idx, root)
    name = Path(wid).name.lower()
    if name.endswith(".ga") or "galaxy" in lang:
        return None
    if name.endswith(".ipynb") or "jupyter" in lang:
        return "notebook"
    if name.endswith(".nf") or "nextflow" in lang:
        if (crate_dir / "nextflow_schema.json").exists() or (crate_dir / "conf" / "test.config").exists():
            return "nfcore"
        return None
    if name.endswith(".cwl") or "common workflow language" in lang or re.search(r"\bcwl\b", lang):
        return "cwl"
    if name in {"snakefile"} or name.endswith(".smk") or name.startswith("snakefile") or "snakemake" in lang:
        if (crate_dir / "config" / "config.yaml").exists() or _snakemake_test_config(crate_dir):
            return "snakemake"
        return None
    return None


# ---- shared pieces ----------------------------------------------------------------------------------

def _yaml():
    try:
        import yaml
    except ImportError as e:  # a dependency; older installs lack it
        raise RuntimeError("Reading workflow configs needs PyYAML: run `pip install -e .` (or `pip install pyyaml`).") from e
    return yaml


def _root_info(src: Path, idx: dict[str, dict[str, Any]], root: dict[str, Any], wf: dict[str, Any]) -> dict[str, Any]:
    """Name, licence, authors, citation and date from the published crate (and WorkflowHub's record if saved)."""
    hub: dict[str, Any] = {}
    for cand in (src.parent / "workflowhub.json", src / "workflowhub.json"):
        if cand.exists():
            try:
                hub = json.loads(cand.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                hub = {}
            break
    lic = root.get("license")
    lic_id = lic.get("@id") if isinstance(lic, dict) else lic
    if isinstance(lic_id, str) and "://" not in lic_id:
        lic_id = f"https://spdx.org/licenses/{lic_id}"
    people = []
    # nf-core crates name their authors on the workflow entity (``author``, ORCID ids resolved to Person entities)
    for r in (_as_list(wf.get("creator")) + _as_list(wf.get("author")) + _as_list(root.get("author"))
              + _as_list(root.get("creator"))):
        e = idx.get((r or {}).get("@id", ""), r if isinstance(r, dict) else {})
        n = e.get("name") or (r or {}).get("@id")
        if n and n not in people:
            people.append(n)
    article = next((i for i, e in idx.items() if "ScholarlyArticle" in _as_list(e.get("@type")) and "://" in i), None)
    if article:  # some crates take the article's id from a README badge: cut the badge's query and markup off
        article = re.split(r"[?)\]\s]", article, 1)[0]
    cit = root.get("citation")
    cit = cit.get("@id") if isinstance(cit, dict) else cit
    doi = hub.get("doi")  # the registry's DOI for the workflow itself: what to cite when the crate names no article
    if doi and "://" not in str(doi):
        doi = f"https://doi.org/{doi}"
    if isinstance(cit, str) and re.search(r"[?)\]\s]", cit):  # a citation scraped from a README badge, with its debris
        cit = (doi or re.split(r"[?)\]\s]", cit, 1)[0]) if cit.count("doi.org") > 1 or "labelColor" in cit \
            else re.split(r"[?)\]\s]", cit, 1)[0]
    return {
        "name": re.sub(r"^Research Object Crate for\s+", "", str(root.get("name") or wf.get("name") or src.name)),
        "description": root.get("description") or wf.get("description") or "",
        "license": lic_id,
        "authors": people,
        "citation": cit or doi or article,
        "datePublished": str(root.get("datePublished") or hub.get("created_at") or "")[:10] or None,
        "isBasedOn": (root.get("isBasedOn") or {}).get("@id") if isinstance(root.get("isBasedOn"), dict)
        else root.get("isBasedOn"),
        "topics": [t.get("label") for t in hub.get("topic_annotations") or [] if isinstance(t, dict) and t.get("label")],
    }


def _copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", "ro-crate-metadata.json", "ro-crate-preview.html",
                                                            "*_cache", ".snakemake", "work", ".nextflow*"))


def _finish(src: Path, out_dir: Path, info: dict[str, Any], method: dict[str, Any], eco: str,
            example: dict[str, Path], expected_csv: str | None, work: Path) -> dict[str, Any]:
    """Write the crate: the published tree under ``workflow/``, the method, the example data and expected results."""
    from .draft import build_crate

    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    _copy_tree(src, out_dir / "workflow")
    if example:
        (work / "example").mkdir(parents=True, exist_ok=True)
        for name, p in example.items():
            if p.is_dir():
                shutil.copytree(p, work / "example" / name, dirs_exist_ok=True)
            else:
                shutil.copy2(p, work / "example" / name)
        method["example_data"] = str(work / "example")
    if expected_csv:
        method["expected"] = expected_csv
    method["workflow"]["in_place"] = True
    desc = {
        "name": info["name"],
        "description": (info["description"] + f" Derived by ReCRATE from the {LABELS[eco]} Workflow RO-Crate"
                        + (f" {info['isBasedOn']}" if info.get("isBasedOn") else "") + " (original left unchanged)."),
        "identifier": f"{info.get('isBasedOn') or src.resolve().as_uri()}#recrate",
        "citation": info.get("citation"),
        "license": info.get("license"),
        "authors": info["authors"],
        "profile": "generic",
        "draft": False,
        "datePublished": info.get("datePublished"),
        "methods": [method],
    }
    desc = {k: v for k, v in desc.items() if v is not None}
    build_crate(desc, out_dir)
    mpath = out_dir / "ro-crate-metadata.json"
    m = json.loads(mpath.read_text(encoding="utf-8"))
    r = next(e for e in m["@graph"] if e.get("@id") == "./")
    if info.get("isBasedOn"):
        r["isBasedOn"] = {"@id": info["isBasedOn"]}
    r.setdefault("hasPart", []).append({"@id": "workflow/"})
    m["@graph"].append({"@id": "workflow/", "@type": "Dataset", "name": "The workflow's files, as published",
                        "description": f"The {LABELS[eco]} workflow with everything it runs with (modules, tools, "
                                       f"configuration), copied from the published crate."})
    if info.get("topics"):
        r["keywords"] = ", ".join(info["topics"])
    mpath.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    return desc


def _param(name: str, typ: str, default: Any, description: str, **extra: Any) -> dict[str, Any]:
    p: dict[str, Any] = {"id": f"in-{name}", "name": name, "type": typ, "description": description or name}
    if default is not None and typ not in {"File", "Directory"}:
        p["default"] = default
    p.update({k: v for k, v in extra.items() if v})
    return p


# ---- nf-core / Nextflow ------------------------------------------------------------------------------

def _nf_schema(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Parameters from nextflow_schema.json, in schema order: name, type, default, description, is_file."""
    out = []
    groups = dict(schema.get("$defs") or schema.get("definitions") or {})
    if schema.get("properties"):
        groups["_top"] = {"properties": schema["properties"]}
    for gname, g in groups.items():
        if gname in NF_SKIP_GROUPS:
            continue
        for name, prop in (g.get("properties") or {}).items():
            if not isinstance(prop, dict) or prop.get("hidden") or name == "outdir":
                continue
            is_file = prop.get("format") in NF_FILE_FORMATS or prop.get("mimetype") is not None
            desc = str(prop.get("description") or "")
            if prop.get("enum"):
                desc += f" One of: {', '.join(map(str, prop['enum']))}."
            out.append({"name": name, "type": JSON_TYPES.get(str(prop.get("type")), "Text"),
                        "default": prop.get("default"), "description": desc.strip(), "is_file": is_file,
                        "group": gname})
    return out


NF_TESTDATA_BASE = "https://raw.githubusercontent.com/nf-core/test-datasets/"


def _nf_testdata_base(src: Path) -> str:
    """Where an nf-core pipeline's test profile fetches its data from (``params.pipelines_testdata_base_path`` in
    ``nextflow.config``), else nf-core's test-datasets repository."""
    cfg = src / "nextflow.config"
    if cfg.exists():
        m = re.search(r"pipelines_testdata_base_path\s*=\s*['\"]([^'\"]+)['\"]", cfg.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(1)
    return NF_TESTDATA_BASE


def _nf_test_params(config: Path, base: str = NF_TESTDATA_BASE) -> dict[str, str]:
    """``name = value`` lines of the ``params {}`` block of a Nextflow config, values as written (quotes removed).
    A value built from ``params.pipelines_testdata_base_path`` becomes the full URL the test fetches."""
    text = config.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"params\s*\{(.*?)\n\}", text, re.S)
    block = m.group(1) if m else text
    out: dict[str, str] = {}
    for line in block.splitlines():
        line = line.split("//")[0].strip()
        mm = re.match(r"([A-Za-z_][\w.]*)\s*=\s*(.+)$", line)
        if mm:
            value = mm.group(2).strip()
            if "pipelines_testdata_base_path" in value or "params." in value:
                literals = re.findall(r"['\"]([^'\"]+)['\"]", value)
                if literals:  # remote test data: the base path, then the quoted tail
                    value = base.rstrip("/") + "/" + literals[-1].lstrip("/")
            out[mm.group(1)] = value.strip("'\"")
    return out


def _nf_snapshot(src: Path) -> tuple[Path | None, list[tuple[str, str]]]:
    """What the pipeline's own test says its test profile produces, from its nf-test snapshot
    (``tests/default.nf.test.snap``, else the first ``*.nf.test.snap``): ``(file, md5)`` for each output whose
    content the snapshot pins (``name:md5,<hash>`` entries) and ``(file, "")`` for each whose name only is stable.
    Version tables in the snapshot are not files and are skipped."""
    tests = src / "tests"
    snaps = sorted(tests.glob("*.nf.test.snap")) if tests.is_dir() else []
    snaps.sort(key=lambda p: p.name != "default.nf.test.snap")
    for snap in snaps:
        try:
            data = json.loads(snap.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for case in (v for v in data.values() if isinstance(v, dict) and isinstance(v.get("content"), list)):
            names: list[str] = []
            hashes: dict[str, str] = {}

            def walk(x: Any) -> None:
                if isinstance(x, str):
                    if ":md5," in x:
                        n, h = x.split(":md5,", 1)
                        hashes[Path(n).name] = h.strip()
                    elif x and not x.startswith("/"):
                        names.append(x.strip())
                elif isinstance(x, list):  # dicts are version tables, not files
                    for y in x:
                        walk(y)

            walk(case["content"])
            dirs = {n for n in names if any(o.startswith(n + "/") for o in names)}
            files = [n for n in dict.fromkeys(names) if n not in dirs]
            rows = [(f, hashes.get(Path(f).name, "")) for f in files]
            seen = {Path(f).name for f in files}
            rows += [(n, h) for n, h in hashes.items() if n not in seen]
            if rows:
                return snap, rows
    return None, []


def import_nfcore(src: Path, out_dir: Path) -> dict[str, Any]:
    idx, root = _meta(src)
    wid, wf, _ = _main(idx, root)
    info = _root_info(src, idx, root, wf)
    schema = json.loads((src / "nextflow_schema.json").read_text(encoding="utf-8")) \
        if (src / "nextflow_schema.json").exists() else {}
    params = _nf_schema(schema)
    test = src / "conf" / "test.config"
    test_params = _nf_test_params(test, _nf_testdata_base(src)) if test.exists() else {}
    files = [p for p in params if p["is_file"]]
    values = [p for p in params if not p["is_file"]]
    # the published test's values are the published run: they become the defaults, and come first
    for p in values:
        if p["name"] in test_params:
            p["default"] = test_params[p["name"]]
    values.sort(key=lambda p: p["name"] not in test_params)
    hidden = values[MAX_PARAMS:]
    values = values[:MAX_PARAMS]

    example: dict[str, Path] = {}
    remote: list[str] = []
    inputs: dict[str, str] = {}
    for p in files:
        v = test_params.get(p["name"])
        if v and not v.startswith(("http://", "https://", "s3://", "gs://")):
            local = src / v
            if local.exists():
                example[local.name] = local
                inputs[p["name"]] = local.name
                continue
        if v:
            remote.append(f"{p['name']}: {v}")
        inputs.setdefault(p["name"], f"{p['name']}{'.csv' if p['name'] == 'input' else ''}")

    runtime_note = (" It needs the network and a container runtime (docker, singularity or apptainer), or conda, mamba or "
                    "micromamba, with which each tool is installed into an environment instead.")
    snap, snap_rows = _nf_snapshot(src)
    snap_note = (f" Expected outputs: the pipeline's nf-test snapshot {snap.name} ({sum(1 for _, h in snap_rows if h)} "
                 f"files by md5, {sum(1 for _, h in snap_rows if not h)} by name)." if snap else "")
    description = (wf.get("description") or root.get("description") or info["name"]) + (
        f" Published test run: {test.name}." if test.exists() else " The crate has no test profile.") + (
        f" The test downloads: {'; '.join(remote)}." if remote else "") + (
        f" {len(hidden)} further parameters stay at the workflow's defaults." if hidden else "") + snap_note + runtime_note
    args = ["nextflow", "-q", "run", "{workflow}", "-work-dir", "{work}/work", "--outdir", "{output_dir}"]
    if test.exists():
        args[4:4] = ["-profile", "test,{runtime}"]
    for name in inputs:
        args += [f"--{name}", f"{{input:{name}}}"]
    for p in values:
        args += [f"--{p['name']}", f"{{param:{p['name']}}}"]
    recipe = {"engine": "nextflow", "args": args, "root": "workflow", "cwd": "work", "network": True,
              "needs": RUNTIMES, "inputs": inputs, "results": {"kind": "files"}}
    method = {
        "name": _slug_name(info["name"]),
        "description": description,
        "workflow": {"file": str(out_dir / "workflow" / wid), "language": "Nextflow", "software": _software(src, "nfcore")},
        "inputs": [_param(p["name"], "Directory" if "directory" in str(p.get("format", "")) else "File", None,
                          p["description"]) for p in files]
        + [_param(p["name"], p["type"], p["default"], p["description"], role="free") for p in values],
        "outputs": [{"id": "out-results", "name": "results", "type": "File", "format": "text/csv", "kind": "files"}],
        "properties": {"recrate invocation": recipe},
    }
    work = out_dir.parent / f".{out_dir.name}-import"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    expected_csv = None
    if snap and snap_rows:  # the snapshot is the published result: md5 per pinned file, presence for the others
        exp = work / "expected.csv"
        exp.write_text("file,md5\n" + "".join(f"{f},{h}\n" for f, h in snap_rows), encoding="utf-8")
        expected_csv = str(exp)
        method["outputs"][0]["tolerances"] = {"md5": {
            "value": 0.0, "source": "test-stated", "file": f"tests/{snap.name}",
            "note": "the pipeline's nf-test snapshot pins these outputs' md5, and the names of the others"}}
    if test.exists() and remote and not example:
        # The published test's inputs are fetched by the profile itself when the pipeline runs (nf-core keeps its
        # test data in a separate repository): the published data is then this note, so the test run is
        # reproducible here, with ``-profile test`` supplying the inputs the recipe's input flags leave out.
        note = work / "TEST-PROFILE.md"
        note.write_text(f"# The published test run ({test.name})\n\nThe test profile fetches its inputs when the "
                        "pipeline runs (`-profile test`), so this folder holds no data file. The inputs it uses:\n\n"
                        + "".join(f"- {r}\n" for r in remote)
                        + "\nTo run the method on your own data, give a folder with the files named in the method's "
                        "inputs instead.\n", encoding="utf-8")
        example = {note.name: note}
    try:
        return _finish(src, out_dir, info, method, "nfcore", example, expected_csv, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _slug_name(name: str) -> str:
    """A short method name (one MCP tool is named after it): the title's first clause when it has one
    ("imputation: a genotype QC and ... workflow" -> imputation), at most eight words."""
    name = re.sub(r"^Research Object Crate for\s+", "", name)
    head = name.split(":", 1)[0].strip() if ":" in name else name
    if not 2 <= len(head) <= 40:
        head = name
    words = [w for w in re.sub(r"[^a-z0-9]+", "_", head.lower()).split("_") if w][:8]
    slug = "_".join(words)
    if len(slug) > 60:
        slug = slug[:60].rsplit("_", 1)[0]
    return slug or "workflow"


# ---- the software a workflow declares ------------------------------------------------------------------
# Each community pins its tools in its own files: conda environment files (nf-core modules, Snakemake envs),
# container images (Snakemake rules and configs, CWL DockerRequirement) and CWL SoftwareRequirement hints.
# They become the crate's softwareRequirements, so environment.json says what ran, and in which version.

DEP = re.compile(r"^(?:[\w.-]+::)?([A-Za-z][A-Za-z0-9_.+-]*?)\s*(?:={1,2}|>=|~=)\s*([0-9][A-Za-z0-9_.+*-]*)")
BARE = re.compile(r"^(?:[\w.-]+::)?([A-Za-z][A-Za-z0-9_.+-]*)$")
IMAGE = re.compile(r"(?:docker|oras|shub|library)://[^\s'\"]+|(?:quay\.io|ghcr\.io|docker\.io)/[^\s'\"]+")


def _conda_deps(path: Path) -> list[tuple[str, str | None]]:
    try:
        env = _yaml().safe_load(path.read_text(encoding="utf-8", errors="replace")) or {}
    except Exception:  # noqa: BLE001 - a malformed environment file lists nothing
        return []
    out: list[tuple[str, str | None]] = []
    deps = env.get("dependencies") if isinstance(env, dict) else None
    todo = list(deps or [])
    while todo:
        d = todo.pop(0)
        if isinstance(d, dict):  # {"pip": [...]}
            for v in d.values():
                todo.extend(v if isinstance(v, list) else [])
            continue
        if not isinstance(d, str):
            continue
        m = DEP.match(d.strip()) or BARE.match(d.strip())
        if m and m.group(1) != "pip":
            out.append((m.group(1), m.group(2) if m.lastindex and m.lastindex >= 2 else None))
    return out


def _image(uri: str) -> tuple[str, str | None]:
    """``docker://ghcr.io/lab/plink:1.9`` -> ``("plink", "1.9")``."""
    tail = uri.split("://", 1)[-1].split("@", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    name, _, tag = tail.partition(":")
    return name, (tag or None) if tag != "latest" else None


def _cwl_software(path: Path) -> list[tuple[str, str | None]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    out: list[tuple[str, str | None]] = [_image(m.group(0)) for m in IMAGE.finditer(text)]
    out += [_image(m.group(1)) for m in re.finditer(r"dockerPull:\s*['\"]?([^\s'\"]+)", text)]
    if "SoftwareRequirement" not in text:
        return out
    try:
        doc = _yaml().safe_load(text)
    except Exception:  # noqa: BLE001 - not YAML (or not parseable): the images above are what we have
        return out
    todo: list[Any] = [doc]
    while todo:
        node = todo.pop()
        if isinstance(node, dict):
            pk = node.get("packages") if node.get("class") == "SoftwareRequirement" else None
            if pk is None and isinstance(node.get("SoftwareRequirement"), dict):
                pk = node["SoftwareRequirement"].get("packages")
            if isinstance(pk, dict):
                for name, spec in pk.items():
                    v = (spec or {}).get("version") if isinstance(spec, dict) else None
                    out.append((str(name), str(v[0]) if isinstance(v, list) and v else (str(v) if v else None)))
            elif isinstance(pk, list):
                for spec in pk:
                    if isinstance(spec, dict) and spec.get("package"):
                        v = spec.get("version")
                        out.append((str(spec["package"]), str(v[0]) if isinstance(v, list) and v else (str(v) if v else None)))
            todo.extend(node.values())
        elif isinstance(node, list):
            todo.extend(node)
    return out


def _software(src: Path, eco: str, limit: int = 200) -> list[dict[str, str]]:
    """The tools the workflow declares, with versions where it pins them, as ``build_crate`` takes them."""
    found: dict[str, str | None] = {}

    def add(pairs: list[tuple[str, str | None]]) -> None:
        for n, v in pairs:
            if n and (n not in found or (found[n] is None and v)):
                found[n] = v

    if eco == "nfcore":
        envs = [(f, _conda_deps(f)) for f in [src / "environment.yml", *sorted((src / "modules").rglob("environment.yml"))]
                if f.exists()]
        # a module's few pinned tools first; a full environment export (hundreds of packages) fills what room is left
        for _, deps in sorted(envs, key=lambda fd: len(fd[1])):
            add(deps)
    elif eco == "snakemake":
        for folder in (src / "workflow" / "envs", src / "envs"):
            for f in sorted(folder.glob("*.y*ml")) if folder.is_dir() else []:
                add(_conda_deps(f))
        for f in [*src.glob("Snakefile*"), *(src / "workflow").glob("Snakefile*"),
                  *(src / "workflow" / "rules").glob("*.smk"), *(src / "config").glob("*.y*ml")]:
            if f.is_file():
                add([_image(m.group(0)) for m in IMAGE.finditer(f.read_text(encoding="utf-8", errors="replace"))])
    elif eco == "cwl":
        for f in sorted(src.rglob("*.cwl"))[:400]:
            add(_cwl_software(f))
    # the cap keeps what was found first (pinned tools before a full export), then the list is sorted by name
    return [{"name": n, **({"version": v} if v else {})} for n, v in sorted(list(found.items())[:limit])]


# ---- CWL ----------------------------------------------------------------------------------------------

def _cwl_type(t: Any) -> tuple[str, bool]:
    """(ReCRATE type, optional) for a CWL type expression."""
    optional = False
    if isinstance(t, list):
        kinds = [x for x in t if x != "null"]
        optional = len(kinds) < len(t)
        t = kinds[0] if kinds else "string"
    if isinstance(t, dict):
        if t.get("type") == "array":
            return _cwl_type(t.get("items"))[0], optional
        t = t.get("type", "string")
    s = str(t)
    if s.endswith("?"):
        s, optional = s[:-1], True
    if s.endswith("[]"):
        s = s[:-2]
    return CWL_TYPES.get(s, "Text"), optional


def _cwl_inputs_from_file(wf_path: Path) -> list[dict[str, Any]]:
    yaml = _yaml()
    doc = yaml.safe_load(wf_path.read_text(encoding="utf-8", errors="replace")) or {}
    if "$graph" in doc:
        doc = next((d for d in doc["$graph"] if d.get("id") in {"#main", "main"}), doc["$graph"][0])
    ins = doc.get("inputs") or {}
    items = list(ins.items()) if isinstance(ins, dict) else [(i.get("id"), i) for i in ins]
    out = []
    for name, spec in items:
        spec = spec if isinstance(spec, dict) else {"type": spec}
        typ, _ = _cwl_type(spec.get("type"))
        out.append({"name": str(name).lstrip("#").split("/")[-1], "type": typ, "default": spec.get("default"),
                    "description": str(spec.get("doc") or spec.get("label") or "")})
    return out


def _cwl_inputs_from_crate(idx: dict[str, dict[str, Any]], wf: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for r in _as_list(wf.get("input")):
        e = idx.get((r or {}).get("@id", ""), {})
        if not e:
            continue
        typ, _ = _cwl_type(e.get("additionalType") or "string")
        out.append({"name": e.get("name") or e["@id"].lstrip("#"), "type": typ, "default": e.get("defaultValue"),
                    "description": str(e.get("description") or "")})
    return out


def _cwl_test(src: Path, wf_rel: str) -> tuple[Path | None, dict[str, Any]]:
    """The cwltest entry for the main workflow (else the first), and the file it came from."""
    yaml = _yaml()
    for tests in sorted(src.rglob("*test*.y*ml")):
        if tests.name.startswith("."):
            continue
        try:
            entries = yaml.safe_load(tests.read_text(encoding="utf-8", errors="replace"))
        except Exception:  # noqa: BLE001 - not a cwltest file
            continue
        if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict) or "job" not in entries[0]:
            continue
        main = next((e for e in entries if str(e.get("tool", "")).endswith(Path(wf_rel).name)), None)
        if main is None and len(entries) == 1 and not entries[0].get("tool"):
            main = entries[0]  # one test, of the only workflow
        return tests, main or {}  # tests of the workflow's tools say nothing about the workflow's own run
    return None, {}


def _cwl_missing(src: Path, wid: str) -> list[str]:
    """The files the workflow runs or imports (``run``, ``$import``, ``$include``) that the crate does not hold, as
    written. A crate registered from one file keeps the workflow but not its tools; cwltool stops at the first."""
    yaml = _yaml()
    seen: set[Path] = set()
    missing: list[str] = []

    def refs(node: Any, out: list[str]) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and (k in {"$import", "$include"} or (
                        k == "run" and ("/" in v or v.endswith((".cwl", ".yml", ".yaml", ".json"))))):
                    out.append(v)
                else:
                    refs(v, out)
        elif isinstance(node, list):
            for v in node:
                refs(v, out)

    def walk(path: Path) -> None:
        if path in seen or not path.exists():
            return
        seen.add(path)
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:  # noqa: BLE001 - a file cwltool cannot read is cwltool's error to report, not a missing file
            return
        found: list[str] = []
        refs(doc, found)
        for ref in found:
            if "://" in ref or ref.startswith("#"):
                continue  # a URL, or a process in this file's own $graph
            target = (path.parent / ref.split("#", 1)[0]).resolve()
            if target.exists():
                walk(target)
            elif ref not in missing:
                missing.append(ref)

    walk((src / wid).resolve())
    return missing


def import_cwl(src: Path, out_dir: Path) -> dict[str, Any]:
    idx, root = _meta(src)
    wid, wf, _ = _main(idx, root)
    info = _root_info(src, idx, root, wf)
    missing = _cwl_missing(src, wid) if (src / wid).exists() else []
    params = _cwl_inputs_from_crate(idx, wf) or (_cwl_inputs_from_file(src / wid) if (src / wid).exists() else [])
    tests, case = _cwl_test(src, wid)
    job: dict[str, Any] = {}
    if tests is not None and case.get("job"):
        jp = (tests.parent / str(case["job"])).resolve()
        if jp.exists():
            job = _yaml().safe_load(jp.read_text(encoding="utf-8", errors="replace")) or {}
            job["__dir__"] = jp.parent
    example: dict[str, Path] = {}
    inputs: dict[str, str] = {}
    classes: dict[str, str] = {}
    job_extra: dict[str, Any] = {}
    jdir = job.pop("__dir__", None)
    for name, v in job.items():
        if isinstance(v, dict) and v.get("class") in {"File", "Directory"}:
            loc = str(v.get("path") or v.get("location") or "").replace("file://", "")
            fp = (Path(jdir) / loc) if jdir and not Path(loc).is_absolute() else Path(loc)
            if fp.exists():
                example[fp.name] = fp
                inputs[name] = fp.name
                if v["class"] == "Directory":
                    classes[name] = "Directory"
            else:
                job_extra[name] = v
        elif isinstance(v, (str, int, float, bool)):
            for q in params:
                if q["name"] == name:
                    q["default"] = v
        else:
            job_extra[name] = v
    for q in params:
        if q["type"] in {"File", "Directory"} and q["name"] not in inputs and q["name"] not in job_extra:
            inputs[q["name"]] = q["name"]
            if q["type"] == "Directory":
                classes[q["name"]] = "Directory"
    expected_csv = None
    tolerance: dict[str, Any] = {}
    outs = case.get("output") or {}
    rows = []
    for oname, o in outs.items():
        if isinstance(o, dict) and o.get("basename") and o.get("checksum"):
            rows.append((o["basename"], str(o["checksum"]).split("$", 1)[-1], o.get("size", "")))
    work = out_dir.parent / f".{out_dir.name}-import"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    if rows and tests is not None:
        exp = work / "expected.csv"
        exp.write_text("file,sha1,size\n" + "".join(f"{f},{h},{s}\n" for f, h, s in rows), encoding="utf-8")
        expected_csv = str(exp)
        tolerance = {"value": 0, "source": "test-stated",
                     "note": f"{tests.relative_to(src)}: checksums and sizes of the published test's outputs"}
    text = "".join(p.read_text(encoding="utf-8", errors="replace") for p in src.rglob("*.cwl"))
    docker = "DockerRequirement" in text
    recipe: dict[str, Any] = {"engine": "cwl", "root": "workflow", "cwd": "work", "network": docker,
                              "needs": ["docker", "singularity", "apptainer", "udocker"] if docker else [],
                              "inputs": inputs, "results": {"kind": "files"},
                              "args": ["cwltool", "--quiet", "--outdir", "{output_dir}", "{workflow}",
                                       "{work}/recrate-job.json"]}
    if classes:
        recipe["classes"] = classes
    if job_extra:
        recipe["job_extra"] = job_extra
    if missing:  # readiness is blocked until the crate holds them (runners.readiness)
        recipe["missing"] = missing
    values = [p for p in params if p["type"] not in {"File", "Directory"}][:MAX_PARAMS]
    method = {
        "name": _slug_name(wf.get("name") or info["name"]),
        "description": (wf.get("description") or info["description"] or info["name"])
        + (f" Published test: {tests.relative_to(src)}." if tests is not None and case else
           f" {tests.relative_to(src)} tests the workflow's tools, not the workflow: no published run to reproduce."
           if tests is not None else " The crate has no cwltest file.")
        + (" Needs a container runtime (DockerRequirement)." if docker else "")
        + (f" The crate holds the workflow but not {len(missing)} file(s) its steps run: "
           f"{', '.join(missing[:6])}{', …' if len(missing) > 6 else ''}." if missing else ""),
        "workflow": {"file": str(out_dir / "workflow" / wid), "language": "Common Workflow Language", "software": _software(src, "cwl")},
        "inputs": [_param(p["name"], p["type"], None, p["description"]) for p in params
                   if p["type"] in {"File", "Directory"}]
        + [_param(p["name"], p["type"], p["default"], p["description"], role="free") for p in values],
        "outputs": [{"id": "out-results", "name": "results", "type": "File", "format": "text/csv", "kind": "files",
                     **({"abs_tolerances": {"size": tolerance}} if tolerance else {})}],
        "properties": {"recrate invocation": recipe},
    }
    try:
        return _finish(src, out_dir, info, method, "cwl", example, expected_csv, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---- Snakemake ----------------------------------------------------------------------------------------

def _snakemake_test_config(src: Path) -> Path | None:
    for c in [src / ".test" / "config" / "config.yaml", src / ".test" / "config.yaml",
              *sorted(src.glob("config/test*.y*ml"))]:
        if c.exists():
            return c
    return None


def _flatten(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def _looks_like_file(value: Any, base: Path) -> Path | None:
    if isinstance(value, str) and value and not value.startswith(("http://", "https://", "s3://", "docker://")) \
            and ("/" in value or "." in Path(value).name):
        p = base / value
        if p.exists():
            return p
    return None


def _snakemake_steps(src: Path) -> tuple[Path | None, list[dict[str, Any]]]:
    """The published test run as the authors script it: ``run_test.sh`` (else another ``run_*.sh``) calling
    ``snakemake --snakefile X --configfile Y [--workflow-profile P]`` once or several times (a QC workflow, then an
    imputation workflow). Each call is one step, in order; a step whose Snakefile is not in the crate is skipped."""
    scripts = [src / "run_test.sh", *sorted(src.glob("run_*.sh")), *sorted(src.glob("*.sh"))]
    for script in dict.fromkeys(p for p in scripts if p.is_file()):
        text = re.sub(r"\\\n\s*", " ", script.read_text(encoding="utf-8", errors="replace"))
        steps: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.split("#")[0]
            if not re.search(r"(^|\s)snakemake\s", line) or "--version" in line:
                continue
            sf = re.search(r"(?:--snakefile|-s)\s+(\S+)", line)
            if not sf or not (src / sf.group(1)).exists():
                continue
            cfgs = [c for c in re.findall(r"--configfiles?\s+(\S+)", line) if (src / c).exists()]
            prof = re.search(r"--workflow-profile\s+(\S+)", line)
            step: dict[str, Any] = {"snakefile": sf.group(1), "configfile": cfgs[0] if cfgs else None}
            if prof and (src / prof.group(1)).is_dir():
                step["workflow_profile"] = prof.group(1)
            steps.append(step)
        if steps:
            return script, steps
    return None, []


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """``over`` on top of ``base``, nested dicts merged key by key (two steps' ``params`` blocks both kept)."""
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _dvc_remote(src: Path) -> str | None:
    cfg = src / ".dvc" / "config"
    if cfg.exists():
        m = re.search(r"^\s*url\s*=\s*(\S+)", cfg.read_text(encoding="utf-8", errors="replace"), re.M)
        if m:
            return m.group(1)
    return None


def import_snakemake(src: Path, out_dir: Path) -> dict[str, Any]:
    yaml = _yaml()
    idx, root = _meta(src)
    wid, wf, _ = _main(idx, root)
    info = _root_info(src, idx, root, wf)
    cfg_path = src / "config" / "config.yaml"
    config = yaml.safe_load(cfg_path.read_text(encoding="utf-8", errors="replace")) if cfg_path.exists() else {}
    script, steps = _snakemake_steps(src)
    test_path = _snakemake_test_config(src)
    if steps:  # the authors' own test run: its first config carries the test's values
        test_path = next((src / st["configfile"] for st in steps if st.get("configfile")), test_path)
    test_cfg = yaml.safe_load(test_path.read_text(encoding="utf-8", errors="replace")) if test_path else {}
    for st in steps:  # every step's config adds its own keys (the imputation step's refs and params)
        if st.get("configfile") and src / st["configfile"] != test_path:
            more = yaml.safe_load((src / st["configfile"]).read_text(encoding="utf-8", errors="replace")) or {}
            test_cfg = _deep_merge(more, test_cfg or {})
    schema_path = src / "workflow" / "schemas" / "config.schema.yaml"
    schema = yaml.safe_load(schema_path.read_text(encoding="utf-8", errors="replace")) if schema_path.exists() else {}
    props = _flatten({k: v for k, v in (schema.get("properties") or {}).items()}) if schema else {}

    flat_cfg = _flatten(config or {})
    flat_test = _flatten(test_cfg or {})
    merged = {**flat_cfg, **flat_test} if flat_test else flat_cfg
    test_base = test_path.parent.parent if test_path and test_path.parent.name == "config" else \
        (test_path.parent if test_path else src)
    example: dict[str, Path] = {}
    inputs: dict[str, str] = {}
    files: list[dict[str, Any]] = []
    values: list[dict[str, Any]] = []
    dvc_tracked: list[str] = []  # test inputs the crate only points at (DVC): fetched with dvc pull where allowed
    for key, v in merged.items():
        if key.split(".")[0] in {"containers", "outs"}:  # container images and output folders are not parameters
            continue
        desc = str(props.get(f"{key}.description") or props.get(key.split('.')[0] + ".description") or "")
        hit = _looks_like_file(v, test_base) or _looks_like_file(v, src)
        pointer = isinstance(v, str) and bool(v) and (src / f"{v}.dvc").exists() and not (src / v).exists()
        if hit is not None or pointer or key.split(".")[-1] in {"samples", "units", "input", "input_vcf", "input_dir"}:
            files.append({"name": key, "description": desc or f"Config key {key}"})
            if hit is not None:
                example[hit.name] = hit
                inputs[key] = hit.name
            else:
                inputs[key] = Path(str(v)).name if isinstance(v, str) and v else key
                if pointer:
                    dvc_tracked.append(str(v))
        elif isinstance(v, str) and ("/" in v or re.search(r"\.[A-Za-z0-9]{1,6}$", v)) and not (src / v).exists():
            continue  # a path to something a step produces during the run: neither an input nor a parameter
        elif isinstance(v, (str, int, float, bool)):
            typ = {bool: "Boolean", int: "Integer", float: "Float"}.get(type(v), "Text")
            values.append({"name": key, "type": typ, "default": v, "description": desc or f"Config key {key}"})
    values = values[:MAX_PARAMS]
    text = "".join(p.read_text(encoding="utf-8", errors="replace")
                   for p in [src / wid, *src.rglob("*.smk")] if p.exists())
    containers = bool(re.search(r"^\s*container\s*:", text, re.M))  # commented-out directives don't count
    condas = bool(re.search(r"^\s*conda\s*:", text, re.M))
    # Snakemake runs containers through singularity/apptainer (not docker) and conda directives through conda/mamba
    needs = (["singularity", "apptainer"] if containers else []) + (["conda", "mamba"] if condas else [])
    recipe: dict[str, Any] = {"engine": "snakemake", "root": "workflow", "cwd": "work", "network": bool(needs),
                              "needs": needs, "inputs": inputs, "results": {"kind": "files"}, "collect": "new-files",
                              "config": test_cfg or config or {}}
    if steps:
        assert script is not None
        prep = src / "prep.sh"
        preflight = prep.exists() and "--what check" in prep.read_text(encoding="utf-8", errors="replace")
        recipe["steps"] = [{**st, **({"preflight": ["./prep.sh", "--what", "check", "--configfile", st["configfile"]]}
                                     if preflight and st.get("configfile") else {})} for st in steps]
        recipe["script"] = str(script.relative_to(src))
        if dvc_tracked:  # what the authors do once, where the data can be fetched: dvc pull, then unpack (prep.sh)
            recipe["prepare"] = [["dvc", "pull"]] + ([["./prep.sh"]] if prep.exists() else [])
    else:
        recipe["args"] = ["snakemake", "-s", "{workflow}", "--cores", "2", "--directory", "{work}",
                          "--configfile", "{work}/recrate-config.yaml", "{sdm}"]
    remote = _dvc_remote(src)
    how = (f" Published test run: {script.relative_to(src) if script else '?'} ({len(steps)} Snakemake step(s) in order:"
           f" {', '.join(Path(st['snakefile']).name for st in steps)})." if steps else
           f" Published test configuration: {test_path.relative_to(src)}." if test_path else
           " The crate has no test configuration.")
    deploy = (" Each rule runs in its own container (singularity or apptainer)." if containers else "") + \
        (" Rules declare conda environments (conda or mamba)." if condas else "")
    dvc_note = (f" The test's inputs are not in the crate but tracked with DVC ({', '.join(dvc_tracked)}); fetch them "
                f"with `dvc pull` in the crate's folder on a machine with access to its remote"
                f"{' (' + remote + ')' if remote else ''}, then reproduce." if dvc_tracked else "")
    method = {
        "name": _slug_name(wf.get("name") or info["name"]),
        "description": (wf.get("description") or info["description"] or info["name"]) + how + deploy + dvc_note,
        "workflow": {"file": str(out_dir / "workflow" / wid), "language": "Snakemake", "software": _software(src, "snakemake")},
        "inputs": [_param(p["name"], "File", None, p["description"]) for p in files]
        + [_param(p["name"], p["type"], p["default"], p["description"], role="free") for p in values],
        "outputs": [{"id": "out-results", "name": "results", "type": "File", "format": "text/csv", "kind": "files"}],
        "properties": {"recrate invocation": recipe},
    }
    work = out_dir.parent / f".{out_dir.name}-import"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    if dvc_tracked and not example:  # the published data is a pointer: say so where the data would be
        note = work / "DVC-DATA.md"
        note.write_text("# The published test's inputs\n\nThey are tracked with DVC, not stored in the crate:\n\n"
                        + "".join(f"- `{d}` (pointer: `{d}.dvc`)\n" for d in dvc_tracked)
                        + (f"\nDVC remote: `{remote}`\n" if remote else "")
                        + "\nOn a machine with access, run `dvc pull` (or `dvc update` for public sources) in the "
                        "crate's `workflow/` folder; the preflight (`prep.sh --what check`) then passes and the test "
                        "run reproduces as the authors run it.\n", encoding="utf-8")
        example = {note.name: note}
    try:
        return _finish(src, out_dir, info, method, "snakemake", example, None, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---- Jupyter notebooks ----------------------------------------------------------------------------------

def _nb_analysis(nb: dict[str, Any]) -> dict[str, Any]:
    """What a published notebook says about itself: the cell to inject parameters into (one tagged ``parameters``,
    else the first that assigns a path-like placeholder such as ``path2data = '...'``) and the variables it sets
    there; the files and patterns it names; the packages it imports; the printed output and the images each code
    cell stored when the authors ran it (their published results)."""
    cells = nb.get("cells", [])
    tagged = next((i for i, c in enumerate(cells) if "parameters" in (c.get("metadata", {}).get("tags") or [])), None)
    assign = re.compile(r"^([A-Za-z_]\w*)\s*=\s*(['\"])(.*?)\2\s*(?:#.*)?$", re.M)
    path_params: list[str] = []
    params_cell = tagged
    values: list[tuple[str, Any]] = []
    for i, c in enumerate(cells):
        if c.get("cell_type") != "code":
            continue
        src = "".join(c.get("source") or [])
        for m in assign.finditer(src):
            var, val = m.group(1), m.group(3)
            pathlike = val in {"...", "", "."} or val.endswith("/") or re.search(r"path|dir|folder|data", var, re.I)
            if pathlike and (tagged is None or i == tagged):
                if params_cell is None:
                    params_cell = i
                if i == params_cell and var not in path_params:
                    path_params.append(var)
        if tagged is not None and i == tagged:  # papermill's own convention: simple literal assignments
            for m in re.finditer(r"^([A-Za-z_]\w*)\s*=\s*(-?\d+(?:\.\d+)?|True|False|'[^']*'|\"[^\"]*\")\s*$", src, re.M):
                if m.group(1) not in path_params:
                    raw = m.group(2)
                    val = (raw == "True") if raw in {"True", "False"} else float(raw) if "." in raw else \
                        int(raw) if re.fullmatch(r"-?\d+", raw) else raw.strip("'\"")
                    values.append((m.group(1), val))
    code = "\n".join("".join(c.get("source") or []) for c in cells if c.get("cell_type") == "code")
    ext = r"(?:zip|csv|tsv|txt|xlsx|xls|json|npy|npz|h5|hdf5|mat|pkl|fast[aq](?:\.gz)?|vcf(?:\.gz)?|bam|tif|tiff|png|jpg)"
    written = set(re.findall(r"(?:to_csv|to_excel|to_pickle|savefig|save|to_json)\(\s*['\"]([^'\"]+)['\"]", code))
    written |= set(re.findall(r"open\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"][wa]", code))
    files = [f for f in re.findall(rf"['\"]([^'\"\n]*?\.{ext})['\"]", code)
             if f not in written and ("*" in f or Path(f).suffix)  # a name or a pattern, not a bare extension
             and not f.startswith(("tmp", "./tmp"))]  # what it reads, not what it writes
    imports = sorted({m for m in re.findall(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", code, re.M)} - STDLIB)
    found = {PYPI.get(m, m) for m in imports if PYPI.get(m, m)}
    found |= set(re.findall(r"engine\s*=\s*['\"](openpyxl|xlrd|pyarrow|fastparquet)['\"]", code))  # pandas' engines
    packages = sorted(found)
    printed: list[tuple[int, str]] = []
    images = 0
    for i, c in enumerate(cells):
        if c.get("cell_type") != "code":
            continue
        text = "".join("".join(o.get("text") or []) for o in c.get("outputs") or [] if o.get("output_type") == "stream")
        if text.strip():
            printed.append((i, text))
        images += sum(1 for o in c.get("outputs") or [] if any(k.startswith("image/") for k in (o.get("data") or {})))
    return {"params_cell": params_cell, "path_params": path_params, "values": values, "files": files,
            "packages": packages, "printed": printed, "images": images}


def _cell_rows(printed: list[tuple[int, str]]) -> list[tuple[str, str, int]]:
    """``(cell-<i>, sha1, size)`` of each code cell's printed output, whitespace at line ends ignored: what the
    notebook prints is compared as the authors' recorded output (recrate.compare, kind ``files``)."""
    import hashlib

    rows = []
    for i, text in printed:
        norm = "\n".join(ln.rstrip() for ln in text.strip().splitlines())
        rows.append((f"cell-{i}", hashlib.sha1(norm.encode("utf-8")).hexdigest(), len(norm)))
    return rows


def import_notebook(src: Path, out_dir: Path) -> dict[str, Any]:
    """A Jupyter notebook published as a workflow (WorkflowHub's Jupyter class), run as the authors ran it with
    papermill in the crate's own folder: the data folder is injected into the notebook's path variable (the cell that
    sets it is tagged ``parameters`` in the run's copy, never in the crate), every file the notebook writes is listed,
    its printed cell outputs are compared with the ones the authors' run stored, and its stored figures are the
    published figures a person compares the new ones with."""
    idx, root = _meta(src)
    wid, wf, _ = _main(idx, root)
    info = _root_info(src, idx, root, wf)
    nb = json.loads((src / wid).read_text(encoding="utf-8", errors="replace"))
    a = _nb_analysis(nb)
    name = Path(wid).name
    patterns = sorted({f for f in a["files"] if "*" in f or f.endswith("/")})
    named = sorted({Path(f).name for f in a["files"] if "*" not in f and not f.endswith("/") and "/" not in f.strip("./")
                    and not f.startswith("tmp")})
    wants = ", ".join([*(f"files matching {p}" for p in patterns), *named]) or "the files the notebook reads"
    inputs: list[dict[str, Any]] = []
    recipe_inputs: dict[str, str] = {}
    args = ["papermill", "{workflow}", f"{{output_dir}}/executed-{name}", "--cwd", "{work}"]
    if a["path_params"]:
        inputs.append(_param("input_dir", "Directory", None,
                             f"The data folder, given to the notebook as {', '.join(a['path_params'])}: {wants}"))
        for var in a["path_params"]:
            args += ["-p", var, "{input_dir}"]
    else:
        inputs.append(_param("input_dir", "Directory", None, f"The notebook's working folder must hold: {wants}"))
    for var, val in a["values"][:MAX_PARAMS]:
        typ = {bool: "Boolean", int: "Integer", float: "Float"}.get(type(val), "Text")
        inputs.append(_param(var, typ, val, "Set in the notebook's parameters cell", role="free"))
        args += ["-p", var, f"{{param:{var}}}"]
    recipe: dict[str, Any] = {"engine": "notebook", "network": False, "needs": [], "collect": "new-files",
                              "results": {"kind": "notebook-cells"}, "inputs": recipe_inputs, "args": args,
                              "notebook": {"parameters_cell": a["params_cell"], "path_params": a["path_params"]}}
    rows = _cell_rows(a["printed"])
    description = ((wf.get("description") or info["description"] or info["name"])
                   + " Runs as published, with papermill in a copy of the crate's folder"
                   + (f"; the data folder is injected as {', '.join(a['path_params'])} (cell {a['params_cell']} is "
                      "tagged as the parameters cell in the run's copy)" if a["path_params"] else "")
                   + ". Every file the notebook writes is kept."
                   + (f" Expected results: the printed output of {len(rows)} code cells as the authors' run stored it"
                      + (f", and {a['images']} stored figures" if a["images"] else "") + "."
                      if rows else " The notebook stores no printed output to compare with.")
                   + (f" It imports {', '.join(a['packages'])}." if a["packages"] else ""))
    software = [{"name": pkg, "version": ""} for pkg in a["packages"]] + [{"name": "papermill", "version": ""},
                                                                         {"name": "ipykernel", "version": ""}]
    outputs: list[dict[str, Any]] = [{"id": "out-results", "name": "results", "type": "File", "format": "text/csv",
                                      "kind": "files"}]
    if rows:
        outputs[0]["tolerances"] = {"sha1": {"value": 0.0, "source": "code-stated", "file": name,
                                              "note": "the printed output each cell stored when the authors ran it"}}
    method = {
        "name": _slug_name(wf.get("name") or info["name"]),
        "description": description,
        "workflow": {"file": str(out_dir / "workflow" / wid), "language": "Jupyter notebook", "software": software},
        "inputs": inputs,
        "outputs": outputs,
        "properties": {"recrate invocation": recipe},
    }
    work = out_dir.parent / f".{out_dir.name}-import"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    expected_csv = None
    if rows:
        exp = work / "expected.csv"
        exp.write_text("file,sha1,size\n" + "".join(f"{f},{h},{z}\n" for f, h, z in rows), encoding="utf-8")
        expected_csv = str(exp)
    try:
        desc = _finish(src, out_dir, info, method, "notebook", {}, expected_csv, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if a["images"]:  # the figures the authors' run stored: the published figures, next to the new ones
        from .notebook_figures import extract_notebook_images

        figdir = Path(out_dir) / "expected" / "figures"
        images = extract_notebook_images(src / wid, figdir)
        mpath = Path(out_dir) / "ro-crate-metadata.json"
        m = json.loads(mpath.read_text(encoding="utf-8"))
        r = next(e for e in m["@graph"] if e.get("@id") == "./")
        for img in images:
            fid = f"expected/figures/{img['path']}"
            m["@graph"].append({"@id": fid, "@type": ["File", "ImageObject"], "name": f"Published figure {img['path']}",
                                "description": f"Stored in the published notebook's cell {img['cell_index']} output."})
            r.setdefault("hasPart", []).append({"@id": fid})
        mpath.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    return desc


IMPORTERS = {"nfcore": import_nfcore, "cwl": import_cwl, "snakemake": import_snakemake, "notebook": import_notebook}


def import_crate(src: str | Path, out_dir: str | Path) -> dict[str, Any]:
    """Write a ReCRATE input crate from a published crate an adapter knows. Returns the description used."""
    src, out_dir = Path(src), Path(out_dir)
    eco = detect_ecosystem(src)
    if eco is None:
        raise ValueError(f"No adapter knows how to read the workflow in {src}.")
    return IMPORTERS[eco](src, out_dir)
