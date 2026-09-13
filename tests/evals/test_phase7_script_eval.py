from salience.creative.scripts import ScriptVerifier


def test_phase_seven_eval_rejects_contradicted_or_clickbait_script_content() -> None:
    result = ScriptVerifier().evaluate(
        {
            "brief_id": "brief-1",
            "claim_ids": ["claim-1"],
            "evidence_ids": ["evidence-1"],
            "target_duration_seconds": 15,
            "sections": [
                {"kind": "hook", "text": "You will never believe this guaranteed result", "duration_seconds": 15}
            ],
            "contradiction_markers": ["claim-1"],
        },
        {"brief_id": "brief-1", "claim_ids": ["claim-1"], "evidence_ids": ["evidence-1"]},
    )

    assert {"contradicted_claim", "misleading_framing"} <= set(result.blocker_codes)
