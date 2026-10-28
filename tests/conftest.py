from __future__ import annotations

import os
import socket
from functools import lru_cache

import pytest

os.environ.setdefault("RECRATE_OFFLINE", "1")  # no registry or DOI lookups in tests
os.environ.setdefault("RECRATE_NO_VALIDATE", "1")  # crate validation on creation is tested on its own (test_export)

from recrate.agent import agentify
from recrate.demo import make_demo


@lru_cache(maxsize=1)
def network_available() -> bool:
    """Whether tests marked ``network`` can run: the RO-Crate validator fetches JSON-LD contexts and profile shapes
    (w3id.org). RECRATE_TEST_NETWORK=1 forces them on, =0 off."""
    forced = os.environ.get("RECRATE_TEST_NETWORK")
    if forced is not None:
        return forced == "1"
    try:
        socket.create_connection(("w3id.org", 443), timeout=3).close()
        return True
    except OSError:
        return False


def pytest_collection_modifyitems(config, items):
    if network_available():
        return
    skip = pytest.mark.skip(reason="needs network (the RO-Crate validator fetches profiles from w3id.org); "
                                   "set RECRATE_TEST_NETWORK=1 to force")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def demo(tmp_path_factory):
    """Demo paper, its crate (two methods), and 'your images' at half resolution."""
    return make_demo(tmp_path_factory.mktemp("demo"))


@pytest.fixture()
def agent(demo, tmp_path):
    """A fresh paper agent built from the demo paper + crate."""
    return agentify(tmp_path / "agent", paper=demo["paper"], crate=demo["crate"])
