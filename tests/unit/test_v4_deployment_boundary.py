import pytest

from salience.api.app import create_configured_app
from salience.workflows.worker import run_worker


def test_unqualified_production_and_multitenancy_fail_startup(monkeypatch):
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "production")
    with pytest.raises(ValueError, match="not qualified"):
        create_configured_app()
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "p0")
    monkeypatch.setenv("SALIENCE_TENANCY", "shared")
    with pytest.raises(ValueError, match="single-workspace"):
        create_configured_app()
    monkeypatch.setenv("SALIENCE_TENANCY", "single-workspace")
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "true")
    with pytest.raises(ValueError, match="disabled effects"):
        create_configured_app()


@pytest.mark.asyncio
async def test_p0_cannot_start_an_effect_worker(monkeypatch):
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "p0")
    with pytest.raises(ValueError, match="does not authorize"):
        await run_worker()
