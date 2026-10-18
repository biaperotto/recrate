import dataclasses

import pytest

from recrate.claims import Claim, load_claims, save_claims


def test_claim_round_trips_through_json(tmp_path):
    c = Claim(id="c1", kind="number", claim="Sixty-eight papers said data were available.",
              section="Results", anchor="data were available", paper_says="68")
    save_claims([c], tmp_path / "claims.json")
    assert load_claims(tmp_path / "claims.json") == [c]

def test_result_defaults_to_not_checked_and_is_never_blank():
    assert Claim(id="c1", kind="number", claim="x", section="Results",
                 anchor="a", paper_says="68").result == "Not checked"

def test_not_checkable_carries_its_reason():
    c = Claim(id="c1", kind="number", claim="x", section="Results",
              anchor="a", paper_says="68").not_checkable("data not shared")
    assert (c.result, c.why_not) == ("Not checkable", "data not shared")

def test_save_rejects_an_unknown_result(tmp_path):
    c = Claim(id="c1", kind="number", claim="x", section="R", anchor="a",
              paper_says="68", result="Probably")
    with pytest.raises(ValueError):
        save_claims([c], tmp_path / "claims.json")

def test_not_checkable_does_not_mutate_the_original():
    c = Claim(id="c1", kind="number", claim="x", section="Results",
              anchor="a", paper_says="68")
    c2 = c.not_checkable("reason")
    assert (c.result, c.why_not) == ("Not checked", "")
    assert c2 is not c

def test_rejected_save_names_the_bad_id_and_writes_nothing(tmp_path):
    good = Claim(id="good", kind="number", claim="x", section="R", anchor="a", paper_says="68")
    bad = Claim(id="bad", kind="number", claim="x", section="R", anchor="a",
                paper_says="68", result="Probably")
    p = tmp_path / "claims.json"
    with pytest.raises(ValueError, match="'bad'"):
        save_claims([good, bad], p)
    assert not p.exists()

def test_round_trip_is_lossless_with_everything_populated(tmp_path):
    c = Claim(id="c9", kind="figure", claim="Accuracy ≥ 0.9 on the test split (café set).",
              section="Results/Figure 2", anchor="≥ 0.9", paper_says="0.92",
              data_gives="0.917", result="Not checkable", why_not="data not shared",
              data_source={"file": "data/é.csv", "rows": [1, 2]},
              computation={"fn": "accuracy", "args": {"split": "test"}},
              tolerance={"abs": 0.01, "note": "≤ 1 pp"})
    save_claims([c], tmp_path / "claims.json")
    assert load_claims(tmp_path / "claims.json") == [c]

def test_to_json_emits_every_field_including_none_values():
    c = Claim(id="c1", kind="number", claim="x", section="R", anchor="a", paper_says="68")
    assert c.data_gives is None and c.tolerance is None
    assert set(c.to_json()) == {f.name for f in dataclasses.fields(Claim)}
