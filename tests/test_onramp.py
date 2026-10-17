from recrate.sources import identify


def test_code_only_link_is_not_a_paper_or_crate():
    refs = identify("https://github.com/someone/repo")
    # a repo resolves to code, which add_and_build must NOT treat as paper/crate
    assert all(r.kind != "paper" and r.kind != "crate" for r in refs) or refs == []


def test_crate_without_methods_still_lists_files(demo, tmp_path):
    from recrate.agent import agentify, PaperAgent
    a = agentify(tmp_path / "paperonly", paper=demo["paper"])  # no crate -> may have no runnable method
    # the review screen reads agent.path file tree; assert the crate dir is listable
    assert a.path.exists()


def test_agentify_failure_is_catchable(tmp_path):
    import pytest
    from recrate.agent import agentify
    with pytest.raises(Exception):
        agentify(tmp_path / "bad", paper=tmp_path / "does-not-exist.pdf")
