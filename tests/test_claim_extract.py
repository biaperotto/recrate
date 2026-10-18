from __future__ import annotations

from pathlib import Path

import pytest

from recrate.claim_extract import extract_claims

FIXTURES = Path(__file__).parent / "fixtures"


def _replay(_prompt: str) -> str:
    return (FIXTURES / "claims_response.json").read_text()


def test_extracts_claims_with_anchors_from_a_recorded_response():
    text = (FIXTURES / "paper_text.txt").read_text()
    claims = extract_claims(text, respond=_replay)
    assert len(claims) >= 3
    c = next(c for c in claims if c.paper_says == "68")
    assert c.anchor in text and c.section and c.kind == "number"
    assert c.result == "Not checked"          # extraction never sets a verdict


def test_every_recorded_anchor_and_value_is_in_the_text_and_ids_are_ordered():
    text = (FIXTURES / "paper_text.txt").read_text()
    claims = extract_claims(text, respond=_replay)
    assert [c.id for c in claims] == [f"c{i}" for i in range(1, len(claims) + 1)]
    for c in claims:
        assert c.anchor in text and c.paper_says in text
        assert c.result == "Not checked" and c.kind == "number"


def test_prompt_carries_the_paper_text():
    seen = []
    extract_claims("UNIQUE-PAPER-TEXT", respond=lambda p: seen.append(p) or '{"claims": []}')
    assert "UNIQUE-PAPER-TEXT" in seen[0]


def test_malformed_model_output_raises(monkeypatch):      # Review Focus 5
    with pytest.raises(ValueError):
        extract_claims("some paper", respond=lambda _p: "not json at all")


def test_invalid_json_inside_braces_raises_value_error():
    with pytest.raises(ValueError):
        extract_claims("some paper", respond=lambda _p: "{not: valid}")


def test_payload_without_a_claims_list_raises():
    with pytest.raises(ValueError):
        extract_claims("some paper", respond=lambda _p: '{"claims": "none"}')


def test_claim_missing_a_required_field_raises():         # Review Focus 5
    with pytest.raises(ValueError):
        extract_claims("some paper", respond=lambda _p: '{"claims":[{"claim":"x"}]}')


def test_unknown_kind_is_rejected():
    entry = ('{"claims":[{"claim":"x","section":"s","anchor":"a","paper_says":"1","kind":"table"}]}')
    with pytest.raises(ValueError):
        extract_claims("some paper", respond=lambda _p: entry)


def test_requires_an_api_key_only_when_no_respond_given(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        extract_claims("some paper")
