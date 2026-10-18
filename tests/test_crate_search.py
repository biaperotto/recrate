"""Before recreating a paper's crate, ReCRATE looks for one already published."""

from __future__ import annotations

from recrate.crate_search import search_crates
from recrate.paper import Paper
from recrate.agent import own_doi

DOI = "10.1093/nargab/lqad104"


def fake(responses):
    def fetch(url, accept=None):
        for key, value in responses.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        return {"data": [], "hits": {"hits": []}}
    return fetch


def test_workflowhub_publication_links_the_workflow():
    s = search_crates(DOI, fetch_json=fake({
        "publications?filter": {"data": [{"id": "62"}]},
        "publications/62": {"data": {"attributes": {"doi": DOI}, "relationships": {"workflows": {"data": [{"id": "2010"}]}}}},
    }))
    assert s.best.where == "WorkflowHub" and s.best.ref.id == "2010"


def test_zenodo_counts_only_deposits_that_are_crates():
    s = search_crates(DOI, fetch_json=fake({"zenodo.org/api/records": {"hits": {"hits": [
        {"id": 1, "metadata": {"title": "videos", "keywords": []}, "files": [{"key": "a.mp4"}]},
        {"id": 2, "metadata": {"title": "workflow", "keywords": ["RO-Crate"]}, "files": [{"key": "wf.zip"}], "doi": "10.5281/zenodo.2"},
    ]}}}))
    assert [c.ref.id for c in s.candidates] == ["2"]


def test_a_link_in_the_paper_wins():
    s = search_crates(DOI, text="Code: https://workflowhub.eu/workflows/771?version=2", fetch_json=fake({
        "publications?filter": {"data": [{"id": "62"}]},
        "publications/62": {"data": {"attributes": {"doi": DOI}, "relationships": {"workflows": {"data": [{"id": "2010"}]}}}},
    }))
    assert s.best.where == "paper link" and s.best.ref.id == "771"


def test_nothing_found_says_where_it_looked_and_survives_a_source_being_down():
    s = search_crates(DOI, fetch_json=fake({"zenodo.org": OSError("down")}))
    assert s.best is None and "WorkflowHub" in s.summary() and any("unavailable" in line for line in s.log)


def test_the_papers_own_doi_not_a_reference():
    text = "Title\\nhttps://doi.org/10.1371/journal.pone.0090081\\n" + "x " * 2000 + "References 10.1000/other.1"
    p = Paper("T", text, "paper.pdf", dois=["10.1000/other.1", "10.1371/journal.pone.0090081"])
    assert own_doi(p) == "10.1371/journal.pone.0090081"
    assert own_doi(Paper("T", "body", "arxiv-2507.22030.pdf")) == "10.48550/arXiv.2507.22030"
