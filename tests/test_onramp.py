from recrate.sources import identify


def test_code_only_link_is_not_a_paper_or_crate():
    refs = identify("https://github.com/someone/repo")
    # a repo resolves to code, which add_and_build must NOT treat as paper/crate
    assert all(r.kind != "paper" and r.kind != "crate" for r in refs) or refs == []
