"""Each field gets its own vocabulary and tools: a paper analysing tables is never asked about pixels."""

from __future__ import annotations

from pathlib import Path

import pytest

from recrate.agent import agentify
from recrate.chat import system_prompt, tools_for
from recrate.profiles import get_profile

ROOT = Path(__file__).resolve().parent.parent
PENGUINS, NUCLEI = ROOT / "examples" / "penguins-dimorphism", ROOT / "examples" / "workflowhub-nuclei-segmentation"


def test_profiles_describe_their_field():
    assert get_profile("generic").field.scale is None and not get_profile("generic").field.visual
    assert get_profile("bioimaging").field.scale == "pixel size" and get_profile("bioimaging").field.visual
    assert get_profile("timeseries").field.scale == "sampling rate"


@pytest.mark.skipif(not (PENGUINS / "crate").exists() or not (NUCLEI / "crate").exists(), reason="examples missing")
def test_tools_and_prompt_follow_the_field(tmp_path):
    tables = agentify(tmp_path / "penguins", paper=PENGUINS / "paper.pdf", crate=PENGUINS / "crate", validate="none")
    images = agentify(tmp_path / "nuclei", crate=NUCLEI / "crate", validate="none")

    def run_props(a):
        return next(t for t in tools_for(a) if t["name"] == "run_on_my_data")["input_schema"]["properties"]

    assert "set_pixel_size" not in [t["name"] for t in tools_for(tables)]
    assert "scale_ratio" not in run_props(tables) and "channel" not in run_props(tables)
    assert "pixel" not in system_prompt(tables).lower()
    assert "set_pixel_size" in [t["name"] for t in tools_for(images)] and "scale_ratio" in run_props(images)
    assert "bring their own images" in system_prompt(images)
