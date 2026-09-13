"""Persist creative release-gate lifecycle, rights, and immutability invariants."""

from alembic import op


revision = "0008_creative_release_gate"
down_revision = "0007_creative_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE provider_jobs
        ADD COLUMN terminal_at TIMESTAMPTZ,
        ADD COLUMN next_poll_after TIMESTAMPTZ,
        ADD COLUMN retry_after TIMESTAMPTZ,
        ADD COLUMN actual_cost_status VARCHAR(32) NOT NULL DEFAULT 'pending',
        ADD COLUMN cancel_requested_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        CREATE TABLE creative_job_effects (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            creative_job_id UUID NOT NULL REFERENCES creative_jobs(id) ON DELETE CASCADE,
            external_effect_id UUID NOT NULL REFERENCES external_effects(id) ON DELETE RESTRICT,
            budget_reservation_id UUID REFERENCES budget_reservations(id) ON DELETE RESTRICT,
            provider_job_id UUID REFERENCES provider_jobs(id) ON DELETE RESTRICT,
            request_key VARCHAR(255) NOT NULL,
            state VARCHAR(32) NOT NULL DEFAULT 'planned',
            trace_id VARCHAR(64),
            span_id VARCHAR(32),
            provenance_record_id UUID REFERENCES provenance_records(id) ON DELETE SET NULL,
            CONSTRAINT uq_creative_job_effects_creative_job UNIQUE (creative_job_id),
            CONSTRAINT uq_creative_job_effects_external_effect UNIQUE (external_effect_id),
            CONSTRAINT uq_creative_job_effects_reservation UNIQUE (budget_reservation_id),
            CONSTRAINT uq_creative_job_effects_provider_job UNIQUE (provider_job_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE creative_provider_webhook_receipts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            provider_job_id UUID NOT NULL REFERENCES provider_jobs(id) ON DELETE CASCADE,
            provider_id VARCHAR(128) NOT NULL,
            delivery_identity VARCHAR(255) NOT NULL,
            safe_payload_hash VARCHAR(64) NOT NULL,
            signature_verified BOOLEAN NOT NULL,
            state VARCHAR(32) NOT NULL,
            received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            trace_id VARCHAR(64),
            span_id VARCHAR(32),
            provenance_record_id UUID REFERENCES provenance_records(id) ON DELETE SET NULL,
            CONSTRAINT uq_creative_webhook_provider_delivery UNIQUE (provider_id, delivery_identity),
            CONSTRAINT uq_creative_webhook_job_hash UNIQUE (provider_job_id, safe_payload_hash)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE creative_job_rights (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            creative_job_id UUID NOT NULL REFERENCES creative_jobs(id) ON DELETE CASCADE,
            link_key VARCHAR(255) NOT NULL,
            asset_license_id UUID REFERENCES asset_licenses(id) ON DELETE RESTRICT,
            consent_record_id UUID REFERENCES consent_records(id) ON DELETE RESTRICT,
            likeness_identity_id UUID REFERENCES likeness_identities(id) ON DELETE RESTRICT,
            voice_identity_id UUID REFERENCES voice_identities(id) ON DELETE RESTRICT,
            usage_restriction_id UUID REFERENCES usage_restrictions(id) ON DELETE RESTRICT,
            reference_asset_id UUID REFERENCES assets(id) ON DELETE RESTRICT,
            territory VARCHAR(64),
            channel VARCHAR(128),
            commercial_use BOOLEAN,
            CONSTRAINT uq_creative_job_rights_link UNIQUE (creative_job_id, link_key),
            CONSTRAINT chk_creative_job_rights_reference CHECK (
                num_nonnulls(
                    asset_license_id,
                    consent_record_id,
                    likeness_identity_id,
                    voice_identity_id,
                    usage_restriction_id,
                    reference_asset_id
                ) = 1
            )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE asset_rights_links (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
            link_key VARCHAR(255) NOT NULL,
            asset_license_id UUID REFERENCES asset_licenses(id) ON DELETE RESTRICT,
            consent_record_id UUID REFERENCES consent_records(id) ON DELETE RESTRICT,
            likeness_identity_id UUID REFERENCES likeness_identities(id) ON DELETE RESTRICT,
            voice_identity_id UUID REFERENCES voice_identities(id) ON DELETE RESTRICT,
            usage_restriction_id UUID REFERENCES usage_restrictions(id) ON DELETE RESTRICT,
            reference_asset_id UUID REFERENCES assets(id) ON DELETE RESTRICT,
            CONSTRAINT uq_asset_rights_links_link UNIQUE (asset_id, link_key),
            CONSTRAINT chk_asset_rights_links_reference CHECK (
                num_nonnulls(
                    asset_license_id,
                    consent_record_id,
                    likeness_identity_id,
                    voice_identity_id,
                    usage_restriction_id,
                    reference_asset_id
                ) = 1
            )
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_approved_distribution_decision_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_TABLE_NAME = 'ready_to_publish_packages' THEN
                IF OLD.approval_state = 'approved' THEN
                    RAISE EXCEPTION 'immutable approved distribution decision';
                END IF;
            ELSIF TG_TABLE_NAME = 'synthetic_media_disclosures' THEN
                IF EXISTS (
                    SELECT 1 FROM ready_to_publish_packages ready
                    WHERE ready.disclosure_id = OLD.id AND ready.approval_state = 'approved'
                ) THEN
                    RAISE EXCEPTION 'immutable approved distribution decision';
                END IF;
            ELSIF TG_TABLE_NAME = 'platform_profiles' THEN
                IF EXISTS (
                    SELECT 1 FROM ready_to_publish_packages ready
                    WHERE ready.platform_profile_id = OLD.id AND ready.approval_state = 'approved'
                ) THEN
                    RAISE EXCEPTION 'immutable approved distribution decision';
                END IF;
            ELSIF TG_TABLE_NAME = 'approval_requests' THEN
                IF EXISTS (
                    SELECT 1 FROM ready_to_publish_packages ready
                    WHERE ready.approval_request_id = OLD.id AND ready.approval_state = 'approved'
                ) THEN
                    RAISE EXCEPTION 'immutable approved distribution decision';
                END IF;
            ELSIF TG_TABLE_NAME = 'distribution_packages' THEN
                IF EXISTS (
                    SELECT 1 FROM ready_to_publish_packages ready
                    WHERE ready.distribution_package_id = OLD.id AND ready.approval_state = 'approved'
                ) THEN
                    RAISE EXCEPTION 'immutable approved distribution decision';
                END IF;
            ELSIF EXISTS (
                SELECT 1
                FROM ready_to_publish_packages ready
                WHERE ready.distribution_package_id = OLD.distribution_package_id
                  AND ready.approval_state = 'approved'
            ) THEN
                RAISE EXCEPTION 'immutable approved distribution decision';
            END IF;

            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in (
        "distribution_packages",
        "distribution_package_variants",
        "distribution_package_assets",
        "title_thumbnail_candidates",
        "localizations",
        "originality_evaluations",
        "synthetic_media_disclosures",
        "platform_profiles",
        "approval_requests",
        "ready_to_publish_packages",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable_after_approval
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reject_approved_distribution_decision_mutation()
            """
        )


def downgrade() -> None:
    for table_name in (
        "ready_to_publish_packages",
        "approval_requests",
        "platform_profiles",
        "synthetic_media_disclosures",
        "originality_evaluations",
        "localizations",
        "title_thumbnail_candidates",
        "distribution_package_assets",
        "distribution_package_variants",
        "distribution_packages",
    ):
        op.execute(f"DROP TRIGGER trg_{table_name}_immutable_after_approval ON {table_name}")
    op.execute("DROP FUNCTION reject_approved_distribution_decision_mutation()")
    op.drop_table("asset_rights_links")
    op.drop_table("creative_job_rights")
    op.drop_table("creative_provider_webhook_receipts")
    op.drop_table("creative_job_effects")
    op.execute(
        """
        ALTER TABLE provider_jobs
        DROP COLUMN cancel_requested_at,
        DROP COLUMN actual_cost_status,
        DROP COLUMN retry_after,
        DROP COLUMN next_poll_after,
        DROP COLUMN terminal_at
        """
    )
