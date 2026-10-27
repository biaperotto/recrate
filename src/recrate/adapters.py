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
  test run: local files become example data, remote ones are listed); runs ``nextflow run <wf> -profile test,<runtime>``,
  which needs a container runtime (docker, singularity, apptainer or conda) and the network.
* **CWL**: the crate's ``FormalParameter``s (WorkflowHub writes them from the workflow), else the workflow's ``inputs``;
  a cwltest ``tests.yml`` gives the job (example data) and the expected outputs' checksums and sizes.
* **Snakemake**: ``config/config.yaml`` with ``workflow/schemas/config.schema.yaml``; ``.test/config/config.yaml`` or
  ``config/test*.yaml`` as the example run.

Expected results from a test are recorded with the tolerance source ``test-stated`` (the test file is the evidence).
No test, no reproduction: the method is reusable only, and the gaps say so.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

LABELS = {"nfcore": "Nextflow", "cwl": "CWL", "snakemake": "Snakemake"}
RUNTIMES = ["docker", "singularity", "apptainer", "conda"]
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
    cit = root.get("citation")
    cit = cit.get("@id") if isinstance(cit, dict) else cit
    doi = hub.get("doi")
    if doi and "://" not in str(doi):
        doi = f"https://doi.org/{doi}"
    return {
        "name": re.sub(r"^Research Object Crate for\s+", "", str(root.get("name") or wf.get("name") or src.name)),
        "description": root.get("description") or wf.get("description") or "",
        "license": lic_id,
        "authors": people,
        "citation": cit or article or doi,
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

    runtime_note = " It needs a container runtime (docker, singularity, apptainer or conda) and the network."
    description = (wf.get("description") or root.get("description") or info["name"]) + (
        f" Published test run: {test.name}." if test.exists() else " The crate has no test profile.") + (
        f" The test downloads: {'; '.join(remote)}." if remote else "") + (
        f" {len(hidden)} further parameters stay at the workflow's defaults." if hidden else "") + runtime_note
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
        return _finish(src, out_dir, info, method, "nfcore", example, None, work)
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


def import_cwl(src: Path, out_dir: Path) -> dict[str, Any]:
    idx, root = _meta(src)
    wid, wf, _ = _main(idx, root)
    info = _root_info(src, idx, root, wf)
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
    values = [p for p in params if p["type"] not in {"File", "Directory"}][:MAX_PARAMS]
    method = {
        "name": _slug_name(wf.get("name") or info["name"]),
        "description": (wf.get("description") or info["description"] or info["name"])
        + (f" Published test: {tests.relative_to(src)}." if tests is not None and case else
           f" {tests.relative_to(src)} tests the workflow's tools, not the workflow: no published run to reproduce."
           if tests is not None else " The crate has no cwltest file.")
        + (" Needs a container runtime (DockerRequirement)." if docker else ""),
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


def import_snakemake(src: Path, out_dir: Path) -> dict[str, Any]:
    yaml = _yaml()
    idx, root = _meta(src)
    wid, wf, _ = _main(idx, root)
    info = _root_info(src, idx, root, wf)
    cfg_path = src / "config" / "config.yaml"
    config = yaml.safe_load(cfg_path.read_text(encoding="utf-8", errors="replace")) if cfg_path.exists() else {}
    test_path = _snakemake_test_config(src)
    test_cfg = yaml.safe_load(test_path.read_text(encoding="utf-8", errors="replace")) if test_path else {}
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
    for key, v in merged.items():
        desc = str(props.get(f"{key}.description") or props.get(key.split('.')[0] + ".description") or "")
        hit = _looks_like_file(v, test_base) or _looks_like_file(v, src)
        if hit is not None or key.split(".")[-1] in {"samples", "units", "input", "input_vcf", "input_dir"}:
            files.append({"name": key, "description": desc or f"Config key {key}"})
            if hit is not None:
                example[hit.name] = hit
                inputs[key] = hit.name
            else:
                inputs[key] = Path(str(v)).name if isinstance(v, str) and v else key
        elif isinstance(v, (str, int, float, bool)):
            typ = {bool: "Boolean", int: "Integer", float: "Float"}.get(type(v), "Text")
            values.append({"name": key, "type": typ, "default": v, "description": desc or f"Config key {key}"})
    values = values[:MAX_PARAMS]
    text = "".join(p.read_text(encoding="utf-8", errors="replace")
                   for p in [src / wid, *src.rglob("*.smk")] if p.exists())
    envs = bool(re.search(r"^\s*(container|conda)\s*:", text, re.M))
    recipe = {"engine": "snakemake", "root": "workflow", "cwd": "work", "network": envs,
              "needs": ["docker", "singularity", "apptainer", "conda", "mamba"] if envs else [],
              "inputs": inputs, "results": {"kind": "files"}, "collect": "new-files",
              "config": test_cfg or config or {},
              "args": ["snakemake", "-s", "{workflow}", "--cores", "2", "--directory", "{work}",
                       "--configfile", "{work}/recrate-config.yaml"]}
    method = {
        "name": _slug_name(wf.get("name") or info["name"]),
        "description": (wf.get("description") or info["description"] or info["name"])
        + (f" Published test configuration: {test_path.relative_to(src)}." if test_path else
           " The crate has no test configuration.")
        + (" Needs conda or a container runtime (the rules declare environments)." if envs else ""),
        "workflow": {"file": str(out_dir / "workflow" / wid), "language": "Snakemake", "software": _software(src, "snakemake")},
        "inputs": [_param(p["name"], "File", None, p["description"]) for p in files]
        + [_param(p["name"], p["type"], p["default"], p["description"], role="free") for p in values],
        "outputs": [{"id": "out-results", "name": "results", "type": "File", "format": "text/csv", "kind": "files"}],
        "properties": {"recrate invocation": recipe},
    }
    work = out_dir.parent / f".{out_dir.name}-import"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        return _finish(src, out_dir, info, method, "snakemake", example, None, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


IMPORTERS = {"nfcore": import_nfcore, "cwl": import_cwl, "snakemake": import_snakemake}


def import_crate(src: str | Path, out_dir: str | Path) -> dict[str, Any]:
    """Write a ReCRATE input crate from a published crate an adapter knows. Returns the description used."""
    src, out_dir = Path(src), Path(out_dir)
    eco = detect_ecosystem(src)
    if eco is None:
        raise ValueError(f"No adapter knows how to read the workflow in {src}.")
    return IMPORTERS[eco](src, out_dir)
