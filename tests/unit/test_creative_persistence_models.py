"""Contracts for the typed Phase 7-8 persistence boundary."""

from salience.db.base import Base
from salience.db import models  # noqa: F401


def test_creative_models_register_the_canonical_phase_7_8_tables() -> None:
    expected_tables = {
        "script_versions",
        "creative_briefs",
        "storyboards",
        "shot_plans",
        "creative_jobs",
        "provider_jobs",
        "assets",
        "asset_variants",
        "asset_relationships",
        "caption_tracks",
        "compositions",
        "asset_licenses",
        "consent_records",
        "likeness_identities",
        "voice_identities",
        "usage_restrictions",
        "asset_provenance",
        "platform_profiles",
        "distribution_packages",
        "distribution_package_variants",
        "title_thumbnail_candidates",
        "localizations",
        "originality_evaluations",
        "synthetic_media_disclosures",
        "ready_to_publish_packages",
    }

    assert expected_tables <= set(Base.metadata.tables)
