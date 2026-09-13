from salience.creative.governance import DisclosurePolicy, OriginalityValidator, PlatformValidator


def test_phase_eight_eval_blocks_duplicate_localization_and_missing_disclosure() -> None:
    platform = PlatformValidator().validate(
        {"aspect_ratio": "9:16", "caption": "ok", "locale": "fr", "disclosure": None},
        {
            "aspect_ratio": "9:16",
            "caption_limit": 100,
            "allowed_locales": ["en"],
            "requires_disclosure": True,
        },
    )
    disclosure = DisclosurePolicy().decide({"generated": True, "profile_requires_disclosure": True})
    originality = OriginalityValidator().evaluate(
        {"title": "Same title", "narrative_fingerprint": "new", "thumbnail_fingerprint": "new"},
        [{"title": "Same title", "narrative_fingerprint": "old", "thumbnail_fingerprint": "old"}],
    )

    assert {"locale_not_allowed", "missing_disclosure"} <= set(platform.blocker_codes)
    assert disclosure.required is True
    assert originality.allowed is False
    assert "duplicate_title" in originality.blocker_codes
