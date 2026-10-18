"""Deliver a paper agent as one zip that works somewhere else, with a USAGE.md.

Paper2Agent ships each agent as a zip and tests it after extracting it to a fresh location. ``package_agent``
does the same: it copies the agent without its runs, uploads and version-control folders, writes ``USAGE.md``
(what the agent is, its methods and their verdicts, its tools, how to connect it), zips it, extracts the zip to
a new temporary folder and runs the MCP server check there. The zip is kept only if that check passes, unless
``keep_failed`` is set.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

SKIP_DIRS = {"runs", "uploads", ".git", "__pycache__", ".ipynb_checkpoints", "node_modules", ".venv"}
MAX_FILE = 100_000_000  # larger files (raw data, model weights) are listed in USAGE.md, not packed


def usage(agent) -> str:
    c = agent.card()
    name = agent.path.name
    lines = [f"# {c.get('title') or name}", ""]
    if c.get("doi"):
        lines += [f"Research: {c['doi']}" + (f" · published {c['published']}" if c.get("published") else ""), ""]
    lines += ["A ReCRATE research object: the research's RO-Crate (`crate/`), the paper as an agent can read it "
              "(`paper/`), the software environment as of publication (`environment.json`) and its methods, "
              "ready to validate on the published data or reuse on yours.", "", "## Methods", ""]
    lines += [f"- **{m['name']}** ({m['language']}): {m['status']}" for m in c["methods"]] or \
        ["- none runnable yet; see REPORT.md" if (agent.path / "REPORT.md").exists() else "- none runnable yet"]
    lines += ["", "## Use it", "", "```bash", "pip install -e <ReCRATE repository>",
              f"recrate validate {name}         # re-run each method on the published data",
              f"recrate run {name} <your data> --who \"Your name\"",
              f"recrate serve {name}            # the agent over MCP (stdio)", "```", "",
              "In Claude Desktop, Claude Code or any MCP client (absolute paths, checked after installing):", "",
              "```bash", f"recrate install-mcp /path/to/{name} --client desktop   # or project, claude-code, print",
              "```", "",
              "Or open it in the ReCRATE app (`streamlit run app/app.py`) by attaching this zip.", "",
              "## Tools", "",
              "`about_paper`, `search_paper`, `list_tables`, `get_table`, `list_figures`, `get_figure`, "
              "`describe_method`, `check_my_data`, `reproduce`, `run_on_my_data`, `approve_run`, `methods_text`, "
              "`publish_run`, `reproducibility_record`, and one tool per method (`run_<method>`).", ""]
    if agent.environment and agent.environment.software:
        lines += ["## Software", ""] + [f"- {s.name} {s.version or '(version unknown)'} ({s.how})"
                                         for s in agent.environment.software[:30]] + [""]
    return "\n".join(lines)


def _copy(src: Path, dst: Path) -> list[str]:
    left_out = []
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts) or p.is_symlink():
            continue
        if p.is_dir():
            (dst / rel).mkdir(parents=True, exist_ok=True)
        elif p.stat().st_size > MAX_FILE:
            left_out.append(f"{rel} ({p.stat().st_size / 1e9:.1f} GB)")
        else:
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst / rel)
    return left_out


def package_agent(agent_dir: str | Path, out: str | Path | None = None, verify: bool = True,
                  keep_failed: bool = False) -> dict[str, Any]:
    from .agent import PaperAgent

    agent_dir = Path(agent_dir).resolve()
    agent = PaperAgent.load(agent_dir)
    out = Path(out) if out else agent_dir.parent / f"{agent_dir.name}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / agent_dir.name
        left_out = _copy(agent_dir, stage)
        m = stage / "agent.json"
        if m.exists():  # run crates point into runs/, which isn't packed
            manifest = json.loads(m.read_text(encoding="utf-8"))
            for meth in manifest.get("methods", []):
                meth["run_crate"] = None
            m.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        text = usage(agent)
        if left_out:
            text += "\n## Not packed (too large)\n\n" + "\n".join(f"- {f}" for f in left_out) + "\n"
        (stage / "USAGE.md").write_text(text, encoding="utf-8")
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(stage.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(stage.parent))
    report: dict[str, Any] = {"zip": str(out), "size_mb": round(out.stat().st_size / 1e6, 1), "left_out": left_out}
    if verify:
        from .server_check import check_server
        from .sources import safe_unzip

        with tempfile.TemporaryDirectory() as fresh:
            safe_unzip(out, Path(fresh))
            check = check_server(Path(fresh) / agent_dir.name, write=False)
        report["check"] = check
        report["ok"] = check["ok"]
        if not check["ok"] and not keep_failed:
            out.unlink()
            report["zip"] = None
    else:
        report["ok"] = True
    return report
