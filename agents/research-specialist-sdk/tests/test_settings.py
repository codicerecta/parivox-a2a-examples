import pytest

from app.settings import ConfigurationError, load_settings


def test_refuses_to_start_without_key_or_explicit_anonymous():
    # The whole point of failing closed: an omitted key must not silently produce
    # an open endpoint.
    with pytest.raises(ConfigurationError):
        load_settings({})


def test_api_key_enables_auth():
    settings = load_settings({"A2A_API_KEY": "secret"})
    assert settings.auth_enabled is True
    assert settings.api_key == "secret"


def test_explicit_anonymous_disables_auth():
    settings = load_settings({"A2A_ALLOW_ANONYMOUS": "1"})
    assert settings.auth_enabled is False
    assert settings.api_key is None


def test_blank_api_key_is_not_a_key():
    with pytest.raises(ConfigurationError):
        load_settings({"A2A_API_KEY": "   "})


def test_defaults():
    settings = load_settings({"A2A_ALLOW_ANONYMOUS": "yes"})
    assert settings.port == 8080
    assert settings.model == "gpt-4.1"
    assert settings.can_search is False


def test_public_url_is_advertised_verbatim():
    settings = load_settings(
        {"A2A_API_KEY": "k", "PUBLIC_URL": "http://research.a2a.internal:8080/"}
    )
    assert settings.public_url == "http://research.a2a.internal:8080/"


def test_bad_port_is_a_configuration_error():
    with pytest.raises(ConfigurationError):
        load_settings({"A2A_API_KEY": "k", "PORT": "not-a-port"})


def test_openai_key_enables_live_search():
    settings = load_settings({"A2A_API_KEY": "k", "OPENAI_API_KEY": "sk-test"})
    assert settings.can_search is True
