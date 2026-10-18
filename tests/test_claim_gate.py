"""The non-model gate: nothing a model wrote about the paper reaches a verdict unchecked."""

from __future__ import annotations

import pandas as pd

from recrate.claim_gate import gate
from recrate.claims import Claim

PAPER = "Results\nSixty-eight (68) papers reported that data were available."
CI_PAPER = "The share was 21% (8%, 48%) of all papers."


def _c(**kw) -> Claim:
    base = dict(id="c1", kind="number", claim="A claim", section="Results",
                anchor="data were available", paper_says="68")
    base.update(kw)
    return Claim(**base)


def test_a_claim_whose_anchor_is_in_the_paper_passes():
    out = gate([_c(anchor="data were available", paper_says="68")], PAPER)
    assert out[0].result == "Not checked" and out[0].why_not == ""


def test_a_hallucinated_anchor_is_not_checkable():        # Review Focus 2
    out = gate([_c(anchor="ninety-nine papers", paper_says="68")], PAPER)
    assert out[0].result == "Not checkable"
    assert "anchor" in out[0].why_not


def test_a_value_not_printed_in_the_paper_is_not_checkable():
    out = gate([_c(anchor="data were available", paper_says="71")], PAPER)
    assert out[0].result == "Not checkable"
    assert out[0].why_not == "the reported value is not printed in the paper"


def test_anchor_matching_ignores_whitespace_and_case():
    out = gate([_c(anchor="DATA   were\navailable", paper_says="68")], PAPER)
    assert out[0].result == "Not checked"


def test_a_missing_data_file_is_not_checkable(tmp_path):
    c = _c(anchor="data were available", paper_says="68",
           data_source={"file": "missing.xlsx", "columns": ["X"]})
    out = gate([c], PAPER, tmp_path)
    assert out[0].result == "Not checkable"
    assert out[0].why_not == "data file not found: missing.xlsx"


def test_the_gate_never_promotes_a_claim():
    c = _c(anchor="data were available", paper_says="68")
    assert gate([c], PAPER)[0].result != "Reproduced"


def test_a_claim_already_not_checkable_stays_so():
    c = _c(result="Not checkable", why_not="needs a figure")
    out = gate([c], PAPER)
    assert out[0].result == "Not checkable"


def test_the_first_failing_check_wins():
    out = gate([_c(anchor="ninety-nine papers", paper_says="71")], PAPER)
    assert out[0].why_not == "anchor not found in the paper's text"


def test_the_gate_does_not_mutate_the_callers_claims():
    c = _c(anchor="ninety-nine papers")
    out = gate([c], PAPER)
    assert c.result == "Not checked" and c.why_not == ""
    assert out[0] is not c


def test_a_non_numeric_value_is_matched_as_printed():
    ok = gate([_c(anchor="the share was", paper_says="21% (8%, 48%)")], CI_PAPER)
    bad = gate([_c(anchor="the share was", paper_says="21% (8%, 49%)")], CI_PAPER)
    assert ok[0].result == "Not checked"
    assert bad[0].result == "Not checkable"


def test_a_numeric_value_tolerates_sign_variants():
    paper = "The effect was −0.50 in the treated group."
    out = gate([_c(anchor="the effect was", paper_says="-0.50")], paper)
    assert out[0].result == "Not checked"


def test_without_a_data_root_the_data_check_is_skipped():
    c = _c(data_source={"file": "missing.xlsx", "columns": ["X"]})
    assert gate([c], PAPER)[0].result == "Not checked"


def test_columns_present_in_a_csv_pass_and_absent_ones_fail(tmp_path):
    pd.DataFrame({"A": [1], "B": [2]}).to_csv(tmp_path / "d.csv", index=False)
    ok = _c(data_source={"file": "d.csv", "columns": ["A", "B"]})
    bad = _c(data_source={"file": "d.csv", "columns": ["A", "Z"]})
    out = gate([ok, bad], PAPER, tmp_path)
    assert out[0].result == "Not checked"
    assert out[1].result == "Not checkable"
    assert out[1].why_not == "column not in the data: Z"


def test_an_xlsx_sheet_is_honoured(tmp_path):
    with pd.ExcelWriter(tmp_path / "d.xlsx") as w:
        pd.DataFrame({"A": [1]}).to_excel(w, sheet_name="one", index=False)
        pd.DataFrame({"Only2": [1]}).to_excel(w, sheet_name="two", index=False)
    ok = _c(data_source={"file": "d.xlsx", "sheet": "two", "columns": ["Only2"]})
    bad = _c(data_source={"file": "d.xlsx", "sheet": "one", "columns": ["Only2"]})
    out = gate([ok, bad], PAPER, tmp_path)
    assert out[0].result == "Not checked"
    assert out[1].why_not == "column not in the data: Only2"


def test_an_unopenable_file_type_is_not_checkable_not_a_crash(tmp_path):
    (tmp_path / "d.parquet").write_bytes(b"not really")
    c = _c(data_source={"file": "d.parquet", "columns": ["A"]})
    out = gate([c], PAPER, tmp_path)
    assert out[0].result == "Not checkable" and out[0].why_not


def test_a_corrupt_file_is_not_checkable_not_a_crash(tmp_path):
    (tmp_path / "d.xlsx").write_bytes(b"this is not a zip")
    c = _c(data_source={"file": "d.xlsx", "columns": ["A"]})
    out = gate([c], PAPER, tmp_path)
    assert out[0].result == "Not checkable" and out[0].why_not


def test_a_missing_sheet_is_not_checkable_not_a_crash(tmp_path):
    pd.DataFrame({"A": [1]}).to_excel(tmp_path / "d.xlsx", index=False)
    c = _c(data_source={"file": "d.xlsx", "sheet": "nope", "columns": ["A"]})
    out = gate([c], PAPER, tmp_path)
    assert out[0].result == "Not checkable" and out[0].why_not
