import pytest

from app.settings import (
    CANNED_TYPICAL_DURATION_SECONDS,
    LIVE_TYPICAL_DURATION_SECONDS,
    ConfigurationError,
    load_settings,
)


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


def test_canned_delay_defaults_to_zero():
    settings = load_settings({"A2A_API_KEY": "k"})
    assert settings.canned_delay_seconds == 0.0


def test_canned_delay_is_read_from_the_environment():
    settings = load_settings({"A2A_API_KEY": "k", "CANNED_DELAY_SECONDS": "12.5"})
    assert settings.canned_delay_seconds == 12.5


def test_bad_canned_delay_is_a_configuration_error():
    with pytest.raises(ConfigurationError):
        load_settings({"A2A_API_KEY": "k", "CANNED_DELAY_SECONDS": "soon"})


def test_negative_canned_delay_is_a_configuration_error():
    with pytest.raises(ConfigurationError):
        load_settings({"A2A_API_KEY": "k", "CANNED_DELAY_SECONDS": "-1"})


def test_live_search_declares_the_measured_live_duration():
    # A declared duration is only worth having if it is true of the deployment
    # that publishes it, so it follows the provider that will actually run.
    settings = load_settings({"A2A_API_KEY": "k", "OPENAI_API_KEY": "sk-test"})
    assert settings.typical_duration_seconds == LIVE_TYPICAL_DURATION_SECONDS


def test_the_canned_configuration_declares_itself_fast():
    settings = load_settings({"A2A_API_KEY": "k"})
    assert settings.typical_duration_seconds == CANNED_TYPICAL_DURATION_SECONDS


def test_a_slowed_canned_run_declares_the_slower_number():
    # CANNED_DELAY_SECONDS exists to make this agent behave like a minute-scale
    # one; the card has to say so, or a client races a window it cannot win.
    settings = load_settings({"A2A_API_KEY": "k", "CANNED_DELAY_SECONDS": "20"})
    assert settings.typical_duration_seconds == 41.0


def test_the_declared_duration_can_be_overridden():
    settings = load_settings(
        {"A2A_API_KEY": "k", "OPENAI_API_KEY": "sk-test", "TYPICAL_DURATION_SECONDS": "45"}
    )
    assert settings.typical_duration_seconds == 45.0


def test_a_nonsense_declared_duration_is_a_configuration_error():
    for value in ("soon", "0", "-5"):
        with pytest.raises(ConfigurationError):
            load_settings({"A2A_API_KEY": "k", "TYPICAL_DURATION_SECONDS": value})
