import pytest
from pathlib import Path


def test_settings_reject_an_empty_control_plane_token() -> None:
    """An empty admin token must never enable the control plane."""
    try:
        from salience.config import ConfigurationError, Settings
    except ModuleNotFoundError:
        pytest.fail("configuration model is missing")

    with pytest.raises(ConfigurationError, match="CONTROL_PLANE_TOKEN"):
        Settings.from_mapping({"CONTROL_PLANE_TOKEN": ""})


def test_settings_parse_optional_intelligence_runtime_configuration() -> None:
    from salience.config import Settings

    settings = Settings.from_mapping(
        {
            "CONTROL_PLANE_TOKEN": "development-token",
            "MODEL_RUNTIME_ID": "research-model",
            "MODEL_BASE_URL": "https://models.example/v1",
            "MODEL_NAME": "structured-research-1",
            "MODEL_SECRET_REF": "env://RESEARCH_MODEL_API_KEY",
            "RESEARCH_ALLOWED_DOMAINS": "news.example, feeds.example ",
            "RESEARCH_RSS_FEED_URLS": "https://feeds.example/news.xml,https://news.example/rss.xml",
            "RESEARCH_REQUEST_TIMEOUT_SECONDS": "12",
            "RESEARCH_MAX_RESPONSE_BYTES": "250000",
            "BROWSER_ENABLED": "true",
            "BROWSER_TIMEOUT_SECONDS": "25",
            "BROWSER_STEP_LIMIT": "15",
            "MCP_ENDPOINT": "https://mcp.example/rpc",
            "MCP_AUTH_SECRET_REF": "env://MCP_API_KEY",
            "A2A_ENDPOINT": "https://agents.example/a2a",
            "A2A_AUTH_SECRET_REF": "env://A2A_API_KEY",
            "CREATIVE_MAX_VARIANTS": "4",
            "CREATIVE_MAX_STORAGE_BYTES": "9000000",
            "CREATIVE_MIN_FREE_BYTES": "2000000",
            "CREATIVE_PROVIDER_TIMEOUT_SECONDS": "75",
            "CREATIVE_TEMP_DIRECTORY": "/tmp/salience-creative-tests",
            "SYNTHESIA_API_SECRET_REF": "env://SYNTHESIA_API_KEY",
        }
    )

    assert settings.model_runtime_id == "research-model"
    assert settings.model_base_url == "https://models.example/v1"
    assert settings.model_name == "structured-research-1"
    assert settings.model_secret_ref.uri == "env://RESEARCH_MODEL_API_KEY"
    assert settings.research_allowed_domains == ("news.example", "feeds.example")
    assert settings.research_rss_feed_urls == (
        "https://feeds.example/news.xml",
        "https://news.example/rss.xml",
    )
    assert settings.research_request_timeout_seconds == 12
    assert settings.research_max_response_bytes == 250000
    assert settings.browser_enabled is True
    assert settings.browser_timeout_seconds == 25
    assert settings.browser_step_limit == 15
    assert settings.mcp_endpoint == "https://mcp.example/rpc"
    assert settings.mcp_auth_secret_ref.uri == "env://MCP_API_KEY"
    assert settings.a2a_endpoint == "https://agents.example/a2a"
    assert settings.a2a_auth_secret_ref.uri == "env://A2A_API_KEY"
    assert settings.creative_max_variants == 4
    assert settings.creative_max_storage_bytes == 9000000
    assert settings.creative_min_free_bytes == 2000000
    assert settings.creative_provider_timeout_seconds == 75
    assert settings.creative_temp_directory == Path("/tmp/salience-creative-tests")
    assert settings.creative_synthesia_secret_ref.uri == "env://SYNTHESIA_API_KEY"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("RESEARCH_REQUEST_TIMEOUT_SECONDS", "0"),
        ("RESEARCH_MAX_RESPONSE_BYTES", "0"),
        ("BROWSER_TIMEOUT_SECONDS", "0"),
        ("BROWSER_STEP_LIMIT", "0"),
        ("CREATIVE_MAX_VARIANTS", "0"),
        ("CREATIVE_MAX_STORAGE_BYTES", "0"),
        ("CREATIVE_MIN_FREE_BYTES", "0"),
        ("CREATIVE_PROVIDER_TIMEOUT_SECONDS", "0"),
    ],
)
def test_settings_reject_non_positive_intelligence_limits(field: str, value: str) -> None:
    from salience.config import ConfigurationError, Settings

    with pytest.raises(ConfigurationError, match=field):
        Settings.from_mapping({"CONTROL_PLANE_TOKEN": "development-token", field: value})
