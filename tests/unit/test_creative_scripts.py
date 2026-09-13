from salience.creative.scripts import ScriptVerifier


def _brief() -> dict[str, object]:
    return {
        "brief_id": "brief-1",
        "claim_ids": ["claim-1"],
        "evidence_ids": ["evidence-1"],
        "content": {"audience": "urban gardeners", "cta_allowed": True},
    }


def _script() -> dict[str, object]:
    return {
        "brief_id": "brief-1",
        "claim_ids": ["claim-1"],
        "evidence_ids": ["evidence-1"],
        "target_duration_seconds": 30,
        "sections": [
            {"kind": "hook", "text": "A practical garden check", "duration_seconds": 8},
            {"kind": "body", "text": "Check soil moisture before watering.", "duration_seconds": 14},
            {"kind": "cta", "text": "Save this checklist for later.", "duration_seconds": 8},
        ],
    }


def test_script_rejects_claim_absent_from_the_immutable_brief() -> None:
    script = _script() | {"claim_ids": ["claim-1", "unsupported-claim"]}

    result = ScriptVerifier().evaluate(script, _brief())

    assert result.allowed is False
    assert "unsupported_claim" in result.blocker_codes


def test_script_rejects_repeated_sections_and_misleading_framing() -> None:
    script = _script() | {
        "sections": [
            {"kind": "hook", "text": "Guaranteed secret garden cure", "duration_seconds": 10},
            {"kind": "body", "text": "Guaranteed secret garden cure", "duration_seconds": 10},
            {"kind": "cta", "text": "Guaranteed secret garden cure", "duration_seconds": 10},
        ]
    }

    result = ScriptVerifier().evaluate(script, _brief())

    assert {"repeated_section", "misleading_framing"} <= set(result.blocker_codes)


def test_script_accepts_a_claim_preserving_duration_bounded_draft() -> None:
    result = ScriptVerifier().evaluate(_script(), _brief())

    assert result.allowed is True
    assert result.blocker_codes == ()
