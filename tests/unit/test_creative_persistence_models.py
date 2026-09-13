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
        "distribution_package_assets",
        "title_thumbnail_candidates",
        "localizations",
        "originality_evaluations",
        "synthetic_media_disclosures",
        "ready_to_publish_packages",
        "creative_job_effects",
        "creative_provider_webhook_receipts",
        "creative_job_rights",
        "asset_rights_links",
    }

    assert expected_tables <= set(Base.metadata.tables)


def test_creative_release_gate_models_expose_lifecycle_cost_and_receipt_fields() -> None:
    assert {
        "terminal_at",
        "next_poll_after",
        "retry_after",
        "actual_cost_status",
        "cancel_requested_at",
    } <= set(Base.metadata.tables["provider_jobs"].columns.keys())
    assert {
        "external_effect_id",
        "budget_reservation_id",
        "provider_job_id",
        "state",
        "trace_id",
        "span_id",
    } <= set(Base.metadata.tables["creative_job_effects"].columns.keys())
    assert {
        "provider_id",
        "delivery_identity",
        "safe_payload_hash",
        "signature_verified",
        "state",
    } <= set(Base.metadata.tables["creative_provider_webhook_receipts"].columns.keys())
