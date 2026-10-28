"""Shared by every view: paths, the examples, labels, the session state and small helpers."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import streamlit as st

from recrate.chat import LOCAL_URL

ROOT = Path(__file__).resolve().parent.parent.parent  # app/ui/common.py -> the repository
IMG = ROOT / "docs" / "img"
WH = ROOT / "examples" / "workflowhub-nuclei-segmentation"
SD = ROOT / "examples" / "stardist-nuclei"
EXAMPLES = {
    "workflowhub": {"title": "Nuclei segmentation · WorkflowHub #771",
                    "about": "The simple one: a published Galaxy workflow (Otsu threshold, count) reproduced exactly on "
                             "its own test image, then reused on 6 open BBBC039 images with their ground truth.",
                    "tags": ["RO-Crate", "Galaxy", "microscopy", "ground truth"],
                    "agent": WH / "paper_agent", "build": {"crate": WH / "crate"},
                    "data": WH / "reuse-bbbc039",
                    "data_note": "6 images from BBBC039 (U2OS nuclei, Hoechst; Broad Institute, CC0), another lab and "
                                 "microscope, with their ground truth in ground-truth/: your counts are shown next to "
                                 "the true ones."},
    "stardist": {"title": "Cell Detection with Star-convex Polygons · StarDist, arXiv 2018",
                 "about": "A segmentation preprint with public code, model and data. Reproduce its Table 1, then reuse "
                          "the same model on a second public nuclei dataset (BBBC039) and compare the segmentations.",
                 "tags": ["preprint", "Python · TensorFlow", "real data"],
                 "agent": SD / "paper_agent",
                 "fetch_paper": "arXiv:1806.03535",  # fetched when opened: the arXiv licence doesn't let us ship it
                 "build": {"crate": SD / "crate", "paper_doi": "10.48550/arXiv.1806.03535"},
                 "env": True, "data": SD / "reuse-sample",
                 "data_note": "6 of BBBC039's 200 images, with their ground truth (CC0): enough to see how the "
                              "model does on another lab's nuclei. For all 200, use *From a link*.",
                 "reuse_links": {"label": "BBBC039 (Broad Institute, CC0): 200 U2OS nuclei images, Hoechst, with "
                                          "ground truth",
                                 "refs": ["https://data.broadinstitute.org/bbbc/BBBC039/images.zip",
                                          "https://data.broadinstitute.org/bbbc/BBBC039/masks.zip"]}},
}
BADGE = {"validated": ":green-badge[reproduced]", "differs": ":orange-badge[differences found]",
         "failed": ":red-badge[failed]", "not validated": ":gray-badge[not run yet]"}
ICON = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
SERIES = ["#2a78d6", "#eb6834"]  # reference categorical slots 1-2 (validated palette)
TOOL_LABEL = {
    "about_paper": "Reading the research object", "search_paper": "Searching the paper",
    "list_tables": "Listing the paper's tables", "get_table": "Reading a table",
    "list_figures": "Listing the paper's figures", "show_figure": "Showing a figure",
    "describe_method": "Reading the method", "list_my_files": "Looking at the data",
    "check_my_data": "Checking the new data", "reproduce": "Reproducing the published result",
    "run_on_my_data": "Running on new data", "methods_text": "Writing the methods paragraph",
    "approve_run": "Recording your review", "create_output_crate": "Creating the output RO-Crate",
    "reproducibility_record": "Reading the record", "set_pixel_size": "Setting the pixel size",
    "compare_paper_parameters": "Running with the published values unchanged",
    "followup_checks": "Listing follow-up checks", "run_followup_check": "Running a follow-up check",
    "make_figure": "Plotting from the run", "recreated_figures": "Showing the recreated figures",
    "review_figure": "Recording your figure review",
}
def chip(name: str, args: dict | None) -> str:
    """A tool call as the chat shows it: what it does, and the call itself, e.g. reproduce(size_dimorphism_index)."""
    arg = next((str(v) for k, v in (args or {}).items() if k in ("method", "query", "check", "table", "figure")), "")
    return f"↳ {TOOL_LABEL.get(name, name)} · `{name}({arg})`"
SECTIONS = {"validate": "Reproduce", "reuse": "Reuse", "record": "Record"}
ss = st.session_state  # the session, shared by every view
def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)
def short(text: str, limit: int = 240) -> str:
    """The first sentences of a description, leaving out sentences that are only there to give a link."""
    out = ""
    for sent in re.split(r"(?<=[.!?])\s+", " ".join(text.split())):
        if "http" in sent:
            continue
        if out and len(out) + len(sent) > limit:
            break
        out = f"{out} {sent}".strip()
    return out
def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "research-object"
def default_name() -> str:
    """Who runs are recorded as: git's user.name, else the computer's user name."""
    import getpass
    import subprocess

    try:
        name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:  # noqa: BLE001
        name = ""
    return name or getpass.getuser()
IMAGE_EXT = {".png", ".jpg", ".jpeg"}


def init_state() -> None:
    """Session defaults, set once per session (called on every run of app.py)."""
    ss.setdefault("who", default_name())
    ss.setdefault("history", [])
    ss.setdefault("ro", {})
    ss.setdefault("figures", {})
    ss.setdefault("context_notes", [])
    ss.setdefault("uploads", Path(tempfile.mkdtemp(prefix="recrate-")))
    ss.setdefault("local_url", LOCAL_URL)
    ss.setdefault("section", "validate")
