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
