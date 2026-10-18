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
    out = gate([_c(anchor="ninety-nine papers were reported", paper_says="71")], PAPER)
    assert out[0].why_not == "anchor not found in the paper's text"


def test_a_long_hallucinated_anchor_is_not_found():
    out = gate([_c(anchor="ninety-nine papers were reported", paper_says="68")], PAPER)
    assert out[0].why_not == "anchor not found in the paper's text"


def test_the_gate_does_not_mutate_the_callers_claims():
    c = _c(anchor="ninety-nine papers were reported")
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


# Spurious presence: what the gate must refuse although a substring test would accept it.

def test_a_very_short_anchor_proves_nothing():
    for anchor in ("a", "cat", "rate", "ninety-nine papers"):
        out = gate([_c(anchor=anchor, paper_says="68")], "Allocated Rate, J. (2019): 68 papers.")
        assert out[0].result == "Not checkable", anchor
        assert out[0].why_not == "anchor too short to verify", anchor


def test_an_anchor_inside_a_longer_word_is_not_found():
    for anchor in ("data were availab", "ata were available"):
        out = gate([_c(anchor=anchor, paper_says="68")], PAPER)
        assert out[0].result == "Not checkable", anchor
        assert out[0].why_not == "anchor not found in the paper's text", anchor


def test_a_percent_value_is_not_matched_inside_a_longer_number():
    paper = "The share was 48% of all papers."
    out = gate([_c(anchor="the share was", paper_says="8%")], paper)
    assert out[0].result == "Not checkable"
    assert out[0].why_not == "the reported value is not printed in the paper"
    assert gate([_c(anchor="the share was", paper_says="21%")], "The share was 121% of papers.")[0].result == "Not checkable"


def test_a_value_ending_a_sentence_still_matches():
    paper = "The share was 48% of all papers, which is 5 of 10."
    assert gate([_c(anchor="the share was", paper_says="5 of 10")], paper)[0].result == "Not checked"


def test_a_numeric_value_that_drops_a_decimal_is_not_printed():
    paper = "Sixty-eight (68.5) papers reported that data were available."
    out = gate([_c(anchor="data were available", paper_says="68")], paper)
    assert out[0].result == "Not checkable"
    assert out[0].why_not == "the reported value is not printed in the paper"


def test_a_value_far_from_its_anchor_is_not_checkable():
    paper = "Data were available in the methods section. " + "filler " * 400 + "Table 2 reports 68 papers."
    out = gate([_c(anchor="data were available", paper_says="68")], paper)
    assert out[0].result == "Not checkable"
    assert out[0].why_not == "the reported value is not printed near the anchor"


def test_a_value_a_sentence_from_its_anchor_passes():
    paper = "Data were available for most papers. Overall, 68 papers reported it."
    assert gate([_c(anchor="data were available", paper_says="68")], paper)[0].result == "Not checked"


def test_a_path_outside_the_data_root_has_its_own_reason(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    (tmp_path / "secret.csv").write_text("A\n1\n")
    out = gate([_c(data_source={"file": "../secret.csv", "columns": ["A"]})], PAPER, root)
    assert out[0].result == "Not checkable"
    assert out[0].why_not == "data file path is outside the data root: ../secret.csv"


def test_a_list_of_sheets_is_not_checkable_not_a_crash(tmp_path):
    with pd.ExcelWriter(tmp_path / "d.xlsx") as w:
        pd.DataFrame({"A": [1]}).to_excel(w, sheet_name="one", index=False)
        pd.DataFrame({"A": [1]}).to_excel(w, sheet_name="two", index=False)
    c = _c(data_source={"file": "d.xlsx", "sheet": ["one", "two"], "columns": ["A"]})
    out = gate([c], PAPER, tmp_path)
    assert out[0].result == "Not checkable" and out[0].why_not


def test_a_malformed_data_source_is_not_checkable_not_a_crash(tmp_path):
    for bad in (["d.csv"], "d.csv", {"file": "d.csv", "columns": "A"}):
        out = gate([_c(data_source=bad)], PAPER, tmp_path)
        assert out[0].result == "Not checkable" and out[0].why_not, bad


# Thousands separators: a fragment of a comma-grouped number is not the number.

def test_a_fragment_of_a_comma_grouped_number_is_not_printed():
    for value, printed in (("500", "1,500"), ("068", "1,068"), ("500", "2,500,000")):
        paper = f"In total we found {printed} papers in the sample."
        out = gate([_c(anchor="in total we found", paper_says=value)], paper)
        assert out[0].result == "Not checkable", (value, printed)
        assert out[0].why_not == "the reported value is not printed in the paper", (value, printed)


def test_a_fragment_of_a_comma_grouped_value_with_a_unit_is_not_printed():
    paper = "In total we found 1,500 (95% CI) papers in the sample."
    out = gate([_c(anchor="in total we found", paper_says="500 (95% CI)")], paper)
    assert out[0].result == "Not checkable"


def test_a_value_that_itself_contains_a_separator_still_matches():
    paper = "In total we found 1,500 papers in the sample."
    assert gate([_c(anchor="in total we found", paper_says="1,500")], paper)[0].result == "Not checked"
    paper = "In total we found 1,500 (95% CI) papers in the sample."
    assert gate([_c(anchor="in total we found", paper_says="1,500 (95% CI)")], paper)[0].result == "Not checked"


def test_a_value_after_a_comma_in_prose_still_matches():
    paper = "In total we found 68, 72 and 90 papers in the sample."
    for value in ("68", "72", "90"):
        assert gate([_c(anchor="in total we found", paper_says=value)], paper)[0].result == "Not checked", value
