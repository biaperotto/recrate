from __future__ import annotations

import pytest

from recrate.demo import make_demo


@pytest.fixture(scope="session")
def demo(tmp_path_factory):
    """A demo paper crate and a folder of 'your images' at half resolution."""
    return make_demo(tmp_path_factory.mktemp("demo"))
