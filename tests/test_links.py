"""What a paper links to: its authors' own code and data told apart from software they cite, links cleaned of the
text PDFs glue onto them, and the code read without running it (languages, interpreters, R packages)."""

from __future__ import annotations

from pathlib import Path

from recrate.environment import detect_r_packages
from recrate.links import classify, survey_code
from recrate.paper import Paper, _clean_link

TEXT = """An Empirical Assessment of Inferential Reproducibility
Lee Jones1,2,3*, Adrian Barnett2

Abstract
We reanalyse papers. Results for each paper are available on GitHub [20].

References
18. PLOS. Data availability; 2019. Available from:
https://journals.plos.org/plosone/s/data-availability.
19. Chamberlain S, Boettiger C, Ram K. rplos: Interface to PLoS Journals search
API; 2014. Available from: https://github.com/ropensci/rplos.
20. Jones L. Reproducibility; 2026. Available from:
https://github.com/Lee-V-Jones/Reproducibility.
30. Jones L. Lee-V-Jones/statistical-quality: Protocol; 2024. Available from:
https://doi.org/10.5281/zenodo.10620146.
"""


def test_links_are_cleaned_of_what_pdf_text_glues_on():
    assert _clean_link("10.64898/2026.04.07.26350296doi:") == "10.64898/2026.04.07.26350296"
    assert _clean_link("10.1177/2515245919858428.August") == "10.1177/2515245919858428"
    assert _clean_link("https://github.com/ropensci/rplos.20") == "https://github.com/ropensci/rplos"
    assert _clean_link("10.1371/journal.pone.0090081") == "10.1371/journal.pone.0090081"  # a real DOI is kept


def test_the_authors_own_code_and_data_are_told_from_cited_software():
    p = Paper(title="x", text=TEXT, source="x", dois=["10.5281/zenodo.10620146"],
              repos=["https://github.com/Lee-V-Jones/Reproducibility", "https://github.com/ropensci/rplos"])
    got = {l["url"]: (l["kind"], l["own"]) for l in classify(p)}
    assert got["https://github.com/Lee-V-Jones/Reproducibility"] == ("code", True)  # owner is an author
    assert got["https://github.com/ropensci/rplos"] == ("code", False)  # a tool they used
    assert got["https://doi.org/10.5281/zenodo.10620146"] == ("data", True)  # its reference is by an author


def test_the_code_is_surveyed_without_running_it(tmp_path):
    (tmp_path / "Papers").mkdir()
    (tmp_path / "README.md").write_text("# Reproducibility")
    (tmp_path / "Papers" / "paper01.qmd").write_text("```{r}\nlibrary(tidyverse)\nlibrary(car)\nlmtest::bptest(m)\n```")
    (tmp_path / "Papers" / "paper02.qmd").write_text("```{r}\nlibrary(tidyverse)\nlibrary(stats)\n```")
    s = survey_code(tmp_path)
    assert s["languages"] == {"Quarto": 2} and s["readme"] == "README.md" and set(s["needs"]) == {"Rscript", "quarto"}
    pkgs = [x.name for x in detect_r_packages(tmp_path)]
    assert pkgs[0] == "tidyverse" and set(pkgs) == {"tidyverse", "car", "lmtest"}  # base R (stats) left out


def test_the_licence_and_authors_come_from_the_doi():
    from recrate.agent import _paper_metadata
    from recrate.paper_crate import licence_entity

    csl = {"license": [{"content-version": "vor", "URL": "http://creativecommons.org/licenses/by-nc/4.0/"}],
           "author": [{"given": "Lee", "family": "Jones", "ORCID": "http://orcid.org/0000-0002-5686-1155"},
                      {"given": "Dimitrios", "family": "Vagenas"}]}
    meta = _paper_metadata("10.64898/2026.04.07.26350296", lambda url, accept: csl)
    assert meta["authors"][0] == {"name": "Lee Jones", "orcid": "https://orcid.org/0000-0002-5686-1155"}
    assert licence_entity(meta["license"], "x")["name"] == "CC BY-NC 4.0"
    assert licence_entity(None, "")["name"] == "Licence not stated"  # said plainly, never left out
