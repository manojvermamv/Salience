import pytest


def test_settings_reject_an_empty_control_plane_token() -> None:
    """An empty admin token must never enable the control plane."""
    try:
        from salience.config import ConfigurationError, Settings
    except ModuleNotFoundError:
        pytest.fail("configuration model is missing")

    with pytest.raises(ConfigurationError, match="CONTROL_PLANE_TOKEN"):
        Settings.from_mapping({"CONTROL_PLANE_TOKEN": ""})
