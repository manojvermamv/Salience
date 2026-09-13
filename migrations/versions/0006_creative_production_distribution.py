"""Add canonical Phase 7-8 creative production and distribution records."""

from alembic import op


revision = "0006_creative_production"
down_revision = "0005_strategy_idempotency"
branch_labels = None
depends_on = None


SCHEMA_SQL = """
CREATE TABLE script_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE,
    content_brief_id UUID NOT NULL REFERENCES content_brief_versions(id) ON DELETE RESTRICT,
    parent_script_id UUID REFERENCES script_versions(id) ON DELETE RESTRICT,
    script_key VARCHAR(255) NOT NULL, version INTEGER NOT NULL, status VARCHAR(32) NOT NULL,
    target_format VARCHAR(128) NOT NULL, target_duration_seconds INTEGER NOT NULL,
    script JSONB NOT NULL, claim_ids JSONB NOT NULL DEFAULT '[]'::jsonb, evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb, trace_id VARCHAR(64), span_id VARCHAR(32),
    CONSTRAINT uq_script_versions_program_key_version UNIQUE (content_program_id, script_key, version)
);
CREATE TABLE creative_briefs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE,
    content_brief_id UUID NOT NULL REFERENCES content_brief_versions(id) ON DELETE RESTRICT,
    script_version_id UUID NOT NULL REFERENCES script_versions(id) ON DELETE RESTRICT,
    creative_key VARCHAR(255) NOT NULL, version INTEGER NOT NULL, status VARCHAR(32) NOT NULL,
    creative_plan JSONB NOT NULL, provenance JSONB NOT NULL DEFAULT '{}'::jsonb, trace_id VARCHAR(64), span_id VARCHAR(32),
    UNIQUE (content_program_id, creative_key, version)
);
CREATE TABLE storyboards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    creative_brief_id UUID NOT NULL REFERENCES creative_briefs(id) ON DELETE CASCADE,
    version INTEGER NOT NULL, status VARCHAR(32) NOT NULL, content JSONB NOT NULL,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb, trace_id VARCHAR(64), span_id VARCHAR(32), UNIQUE (creative_brief_id, version)
);
CREATE TABLE shot_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    storyboard_id UUID NOT NULL REFERENCES storyboards(id) ON DELETE CASCADE,
    sequence_no INTEGER NOT NULL, duration_seconds NUMERIC(10,3) NOT NULL, shot JSONB NOT NULL,
    capability_requirements JSONB NOT NULL DEFAULT '[]'::jsonb, UNIQUE (storyboard_id, sequence_no)
);
CREATE TABLE creative_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE,
    job_id UUID REFERENCES jobs(id) ON DELETE SET NULL, content_brief_id UUID NOT NULL REFERENCES content_brief_versions(id) ON DELETE RESTRICT,
    script_version_id UUID REFERENCES script_versions(id) ON DELETE RESTRICT, creative_brief_id UUID REFERENCES creative_briefs(id) ON DELETE RESTRICT,
    requested_capability VARCHAR(128) NOT NULL, request_fingerprint VARCHAR(128) NOT NULL, idempotency_key VARCHAR(255) NOT NULL,
    state VARCHAR(32) NOT NULL, request JSONB NOT NULL, budget_reservation_id UUID REFERENCES budget_reservations(id) ON DELETE RESTRICT,
    timeout_seconds INTEGER NOT NULL, trace_id VARCHAR(64), span_id VARCHAR(32), provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_creative_jobs_program_request_fingerprint UNIQUE (content_program_id, request_fingerprint),
    UNIQUE (content_program_id, idempotency_key)
);
CREATE TABLE provider_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    creative_job_id UUID NOT NULL REFERENCES creative_jobs(id) ON DELETE CASCADE,
    plugin_version_id UUID REFERENCES plugin_versions(id) ON DELETE RESTRICT,
    provider_id VARCHAR(128) NOT NULL, provider_version VARCHAR(64), model_id VARCHAR(255), external_job_id VARCHAR(255),
    state VARCHAR(32) NOT NULL, reconciliation_state JSONB NOT NULL DEFAULT '{}'::jsonb, normalized_request JSONB NOT NULL,
    provider_extension JSONB NOT NULL DEFAULT '{}'::jsonb, estimated_cost_micros BIGINT NOT NULL DEFAULT 0, actual_cost_micros BIGINT,
    failure_class VARCHAR(128), submitted_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, trace_id VARCHAR(64), span_id VARCHAR(32),
    CONSTRAINT uq_provider_jobs_provider_external UNIQUE (provider_id, external_job_id)
);
CREATE TABLE assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE,
    artifact_id UUID REFERENCES artifacts(id) ON DELETE SET NULL, creative_job_id UUID REFERENCES creative_jobs(id) ON DELETE SET NULL,
    storage_key VARCHAR(1024) NOT NULL, content_hash VARCHAR(128) NOT NULL, media_type VARCHAR(255) NOT NULL, byte_size BIGINT NOT NULL,
    origin_type VARCHAR(64) NOT NULL, technical_properties JSONB NOT NULL DEFAULT '{}'::jsonb, creation_parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    rights_status VARCHAR(32) NOT NULL DEFAULT 'unknown', provenance_status VARCHAR(32) NOT NULL DEFAULT 'not_configured',
    trace_id VARCHAR(64), span_id VARCHAR(32), CONSTRAINT uq_assets_program_hash_media_type UNIQUE (content_program_id, content_hash, media_type)
);
CREATE TABLE asset_variants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE, provider_job_id UUID REFERENCES provider_jobs(id) ON DELETE SET NULL,
    variant_key VARCHAR(255) NOT NULL, selection_state VARCHAR(32) NOT NULL, selection_reason TEXT, verifier_results JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (asset_id, variant_key)
);
CREATE TABLE asset_relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    parent_asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE RESTRICT, child_asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
    relationship_type VARCHAR(64) NOT NULL, transformation JSONB NOT NULL DEFAULT '{}'::jsonb, UNIQUE (parent_asset_id, child_asset_id, relationship_type)
);
CREATE TABLE caption_tracks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE, language_code VARCHAR(32) NOT NULL, format VARCHAR(32) NOT NULL,
    content JSONB NOT NULL, artifact_id UUID REFERENCES artifacts(id) ON DELETE SET NULL, validation JSONB NOT NULL DEFAULT '{}'::jsonb, UNIQUE (asset_id, language_code, format)
);
CREATE TABLE compositions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE, output_asset_id UUID REFERENCES assets(id) ON DELETE SET NULL,
    composition_key VARCHAR(255) NOT NULL, version INTEGER NOT NULL, specification JSONB NOT NULL, status VARCHAR(32) NOT NULL,
    UNIQUE (content_program_id, composition_key, version)
);
CREATE TABLE asset_licenses (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE, license_type VARCHAR(64) NOT NULL, attribution JSONB NOT NULL DEFAULT '{}'::jsonb, commercial_use BOOLEAN NOT NULL DEFAULT false, terms JSONB NOT NULL DEFAULT '{}'::jsonb, expires_at TIMESTAMPTZ, status VARCHAR(32) NOT NULL);
CREATE TABLE consent_records (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT, subject_type VARCHAR(64) NOT NULL, subject_id VARCHAR(255) NOT NULL, status VARCHAR(32) NOT NULL, permitted_channels JSONB NOT NULL DEFAULT '[]'::jsonb, commercial_use BOOLEAN NOT NULL DEFAULT false, territories JSONB NOT NULL DEFAULT '[]'::jsonb, expires_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ, evidence JSONB NOT NULL DEFAULT '{}'::jsonb, UNIQUE (workspace_id, subject_type, subject_id));
CREATE TABLE likeness_identities (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT, identity_key VARCHAR(255) NOT NULL, consent_record_id UUID REFERENCES consent_records(id) ON DELETE RESTRICT, status VARCHAR(32) NOT NULL, attributes JSONB NOT NULL DEFAULT '{}'::jsonb, UNIQUE (workspace_id, identity_key));
CREATE TABLE voice_identities (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT, identity_key VARCHAR(255) NOT NULL, consent_record_id UUID REFERENCES consent_records(id) ON DELETE RESTRICT, status VARCHAR(32) NOT NULL, attributes JSONB NOT NULL DEFAULT '{}'::jsonb, UNIQUE (workspace_id, identity_key));
CREATE TABLE usage_restrictions (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, asset_id UUID REFERENCES assets(id) ON DELETE CASCADE, restriction_type VARCHAR(64) NOT NULL, document JSONB NOT NULL, status VARCHAR(32) NOT NULL);
CREATE TABLE asset_provenance (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE, provenance_record_id UUID REFERENCES provenance_records(id) ON DELETE SET NULL, origin_type VARCHAR(64) NOT NULL, ingredients JSONB NOT NULL DEFAULT '[]'::jsonb, transformations JSONB NOT NULL DEFAULT '[]'::jsonb, c2pa_manifest_reference TEXT, validation_status VARCHAR(32) NOT NULL, signer_metadata JSONB NOT NULL DEFAULT '{}'::jsonb, UNIQUE (asset_id));
CREATE TABLE platform_profiles (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT, content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE, profile_key VARCHAR(255) NOT NULL, version INTEGER NOT NULL, target_platform VARCHAR(128) NOT NULL, rules JSONB NOT NULL, status VARCHAR(32) NOT NULL, CONSTRAINT uq_platform_profiles_program_key_version UNIQUE (content_program_id, profile_key, version));
CREATE TABLE distribution_packages (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT, content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE, content_brief_id UUID NOT NULL REFERENCES content_brief_versions(id) ON DELETE RESTRICT, script_version_id UUID NOT NULL REFERENCES script_versions(id) ON DELETE RESTRICT, platform_profile_id UUID NOT NULL REFERENCES platform_profiles(id) ON DELETE RESTRICT, package_key VARCHAR(255) NOT NULL, version INTEGER NOT NULL, locale VARCHAR(32) NOT NULL, metadata JSONB NOT NULL, status VARCHAR(32) NOT NULL, verifier_results JSONB NOT NULL DEFAULT '{}'::jsonb, CONSTRAINT uq_distribution_packages_program_key_version UNIQUE (content_program_id, package_key, version));
CREATE TABLE distribution_package_variants (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, distribution_package_id UUID NOT NULL REFERENCES distribution_packages(id) ON DELETE CASCADE, variant_key VARCHAR(255) NOT NULL, metadata JSONB NOT NULL, status VARCHAR(32) NOT NULL, UNIQUE (distribution_package_id, variant_key));
CREATE TABLE title_thumbnail_candidates (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, distribution_package_id UUID NOT NULL REFERENCES distribution_packages(id) ON DELETE CASCADE, candidate_key VARCHAR(255) NOT NULL, title TEXT NOT NULL, thumbnail_asset_id UUID REFERENCES assets(id) ON DELETE SET NULL, score NUMERIC(7,3), selection_state VARCHAR(32) NOT NULL, reason TEXT, UNIQUE (distribution_package_id, candidate_key));
CREATE TABLE localizations (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, distribution_package_id UUID NOT NULL REFERENCES distribution_packages(id) ON DELETE CASCADE, source_locale VARCHAR(32) NOT NULL, target_locale VARCHAR(32) NOT NULL, content JSONB NOT NULL, claim_ids JSONB NOT NULL DEFAULT '[]'::jsonb, status VARCHAR(32) NOT NULL, UNIQUE (distribution_package_id, target_locale));
CREATE TABLE originality_evaluations (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, distribution_package_id UUID NOT NULL REFERENCES distribution_packages(id) ON DELETE CASCADE, evaluator_version VARCHAR(64) NOT NULL, metrics JSONB NOT NULL, status VARCHAR(32) NOT NULL, reason TEXT NOT NULL, UNIQUE (distribution_package_id, evaluator_version));
CREATE TABLE synthetic_media_disclosures (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, distribution_package_id UUID NOT NULL REFERENCES distribution_packages(id) ON DELETE CASCADE, decision JSONB NOT NULL, status VARCHAR(32) NOT NULL, policy_version_id UUID REFERENCES policy_versions(id) ON DELETE RESTRICT, UNIQUE (distribution_package_id));
CREATE TABLE ready_to_publish_packages (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT, content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE, content_brief_id UUID NOT NULL REFERENCES content_brief_versions(id) ON DELETE RESTRICT, script_version_id UUID NOT NULL REFERENCES script_versions(id) ON DELETE RESTRICT, distribution_package_id UUID NOT NULL REFERENCES distribution_packages(id) ON DELETE RESTRICT, platform_profile_id UUID NOT NULL REFERENCES platform_profiles(id) ON DELETE RESTRICT, disclosure_id UUID NOT NULL REFERENCES synthetic_media_disclosures(id) ON DELETE RESTRICT, ready_package_key VARCHAR(255) NOT NULL, version INTEGER NOT NULL, approval_request_id UUID REFERENCES approval_requests(id) ON DELETE RESTRICT, approval_state VARCHAR(32) NOT NULL, verifier_results JSONB NOT NULL, policy_versions JSONB NOT NULL DEFAULT '[]'::jsonb, lineage JSONB NOT NULL, CONSTRAINT uq_ready_package_program_key_version UNIQUE (content_program_id, ready_package_key, version));
"""


def upgrade() -> None:
    for statement in SCHEMA_SQL.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    for table_name in (
        "ready_to_publish_packages", "synthetic_media_disclosures", "originality_evaluations", "localizations", "title_thumbnail_candidates", "distribution_package_variants", "distribution_packages", "platform_profiles", "asset_provenance", "usage_restrictions", "voice_identities", "likeness_identities", "consent_records", "asset_licenses", "compositions", "caption_tracks", "asset_relationships", "asset_variants", "assets", "provider_jobs", "creative_jobs", "shot_plans", "storyboards", "creative_briefs", "script_versions",
    ):
        op.drop_table(table_name)
