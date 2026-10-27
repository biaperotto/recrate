"""A paper without a structured full text still gets labelled figures: the PDF's images paired with the captions
printed on the same page."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from recrate.reading import _captions, figures, figures_from_pdf

STARDIST = Path(__file__).resolve().parent.parent / "examples" / "stardist-nuclei" / "paper-download" / "arxiv-1806.03535.pdf"


def test_captions_are_found_in_page_text():
    text = ("Results\nFigure 2. Nuclei per field for\nthree images.\nFig. 3: The same at half resolution.\n"
            "See Figure 4 for details (not a caption).\n")
    assert _captions(text) == [(2, "Nuclei per field for three images."), (3, "The same at half resolution.")]


def _pdf_with_figures(path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["pdf.fonttype"] = 42  # TrueType: the text layer stays extractable
    from matplotlib.backends.backend_pdf import PdfPages

    rng = np.random.default_rng(0)
    with PdfPages(path) as pdf:
        fig, ax = plt.subplots(figsize=(5, 5))
        ax.imshow(rng.random((80, 80)), cmap="gray")
        ax.set_axis_off()
        fig.text(0.1, 0.03, "Figure 2. Nuclei per field for three images.", fontsize=9)
        pdf.savefig(fig)
        plt.close(fig)
        fig, ax = plt.subplots(figsize=(5, 5))
        ax.imshow(rng.random((80, 80)), cmap="gray")
        ax.set_axis_off()
        fig.text(0.1, 0.03, "Some page text without a caption.", fontsize=9)
        pdf.savefig(fig)
        plt.close(fig)


def test_pdf_images_are_paired_with_their_captions(tmp_path):
    pytest.importorskip("matplotlib")
    pdf = tmp_path / "paper.pdf"
    _pdf_with_figures(pdf)
    paper_dir = tmp_path / "paper"
    out = figures_from_pdf(pdf, paper_dir / "figures", min_bytes=100)
    names = sorted(p.name for p in out)
    assert any(n.startswith("figure-2") for n in names), names  # the captioned page's image, named after it
    assert any(n.startswith("page-2-") for n in names), names  # the other keeps its page name
    listed = figures(paper_dir)
    assert listed[0]["label"] == "Figure 2" and listed[0]["caption"].startswith("Nuclei per field")
    meta = json.loads((paper_dir / "package.json").read_text())
    assert meta["tables"] == [] and meta["figures"][0]["page"] == 1


@pytest.mark.skipif(not STARDIST.exists(), reason="arXiv PDF not downloaded")
def test_an_arxiv_paper_gets_labelled_figures(tmp_path):
    figures_from_pdf(STARDIST, tmp_path / "paper" / "figures")
    labels = [f["label"] for f in figures(tmp_path / "paper")]
    assert any(label.startswith("Figure ") for label in labels), labels
