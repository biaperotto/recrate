from __future__ import annotations

import os

import pytest

os.environ.setdefault("RECRATE_OFFLINE", "1")  # no registry or DOI lookups in tests
os.environ.setdefault("RECRATE_NO_VALIDATE", "1")  # crate validation on creation is tested on its own (test_export)

from recrate.agent import agentify
from recrate.demo import make_demo


@pytest.fixture(scope="session")
def demo(tmp_path_factory):
    """Demo paper, its crate (two methods), and 'your images' at half resolution."""
    return make_demo(tmp_path_factory.mktemp("demo"))


@pytest.fixture()
def agent(demo, tmp_path):
    """A fresh paper agent built from the demo paper + crate."""
    return agentify(tmp_path / "agent", paper=demo["paper"], crate=demo["crate"])
