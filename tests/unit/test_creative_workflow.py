import pytest

from salience.workflows.creative import CreativeProductionRequest, CreativeProductionResult


def test_creative_request_requires_exact_brief_identity_and_idempotency_key() -> None:
    with pytest.raises(ValueError, match="brief"):
        CreativeProductionRequest(
            workspace_id="workspace-1",
            content_program_id="program-1",
            brief_id="",
            idempotency_key="creative-1",
        )


def test_non_dry_creative_request_requires_an_explicit_budget_identity() -> None:
    with pytest.raises(ValueError, match="budget"):
        CreativeProductionRequest(
            workspace_id="workspace-1",
            content_program_id="program-1",
            brief_id="brief-1",
            idempotency_key="creative-1",
            dry_run=False,
        )


def test_creative_result_is_a_publish_free_ready_package_reference() -> None:
    result = CreativeProductionResult(
        state="completed",
        job_id="job-1",
        trace_id="trace-1",
        ready_package_id="ready-1",
        provider_submit_count=1,
    )

    assert result.ready_package_id == "ready-1"
    assert not hasattr(result, "published_url")
