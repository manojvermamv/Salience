from datetime import UTC, datetime, timedelta

from salience.creative.governance import (
    DisclosurePolicy,
    OriginalityValidator,
    PlatformValidator,
    RightsPolicy,
)


def test_rights_policy_fails_closed_for_missing_likeness_consent() -> None:
    decision = RightsPolicy().authorize(
        uses_real_likeness=True,
        consent=None,
        channel="short_video",
        territory="US",
        commercial_use=True,
    )

    assert decision.allowed is False
    assert decision.reason == "missing_consent"


def test_rights_policy_rejects_revoked_or_expired_voice_consent() -> None:
    decision = RightsPolicy().authorize(
        uses_voice_clone=True,
        consent={
            "status": "active",
            "revoked_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "permitted_channels": ["short_video"],
            "territories": ["US"],
            "commercial_use": True,
        },
        channel="short_video",
        territory="US",
        commercial_use=True,
    )

    assert decision.allowed is False
    assert decision.reason == "consent_revoked"


def test_platform_profile_blocks_caption_and_aspect_ratio_violation() -> None:
    result = PlatformValidator().validate(
        {"aspect_ratio": "16:9", "caption": "x" * 101, "duration_seconds": 45},
        {"aspect_ratio": "9:16", "caption_limit": 100, "max_duration_seconds": 60},
    )

    assert {"caption_limit", "aspect_ratio"} <= set(result.blocker_codes)


def test_disclosure_and_originality_are_deterministic() -> None:
    disclosure = DisclosurePolicy().decide(
        {
            "generated": True,
            "realistic": True,
            "uses_real_likeness": True,
            "c2pa_status": "not_configured",
            "profile_requires_disclosure": True,
        }
    )
    originality = OriginalityValidator().evaluate(
        {
            "title": "Garden care checklist",
            "narrative_fingerprint": "garden-checklist",
            "thumbnail_fingerprint": "thumb-1",
            "template_key": "short-v1",
        },
        [
            {
                "title": "Garden care checklist",
                "narrative_fingerprint": "garden-checklist",
                "thumbnail_fingerprint": "thumb-1",
                "template_key": "short-v1",
            }
        ],
    )

    assert disclosure.required is True
    assert "synthetic_media" in disclosure.labels
    assert originality.allowed is False
    assert "duplicate_narrative" in originality.blocker_codes
