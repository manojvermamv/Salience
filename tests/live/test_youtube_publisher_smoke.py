"""Explicitly opt-in private-only YouTube smoke status."""

import pytest

from salience.publication.youtube import run_live_smoke


pytestmark = pytest.mark.live


def test_live_youtube_smoke_reports_not_run_without_explicit_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YOUTUBE_PUBLISHER_CONNECTION_REF", raising=False)
    monkeypatch.delenv("YOUTUBE_PUBLISHER_ENABLE_LIVE_SMOKE", raising=False)

    assert run_live_smoke() == "NOT RUN: YOUTUBE_PUBLISHER_CONNECTION_REF is not configured"
