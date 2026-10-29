import pytest

import app.providers.genesys  # noqa: F401  (registers provider metadata)
from app.config_validator import validate_config

VL = {"AZURE_VOICE_LIVE_ENDPOINT": "https://x/", "AZURE_VOICE_LIVE_API_KEY": "k"}


def test_voice_agent_requires_voice_live_endpoint():
    with pytest.raises(SystemExit):
        validate_config({"CALL_MODE": "voice_agent"}, None)


def test_invalid_call_mode_exits():
    with pytest.raises(SystemExit):
        validate_config({**VL, "CALL_MODE": "nope"}, None)


def test_transcription_with_fake_backend_needs_no_azure():
    assert validate_config({"CALL_MODE": "transcription", "TRANSCRIPTION_BACKEND": "fake"}, None) is False


def test_transcription_unknown_backend_exits():
    with pytest.raises(SystemExit):
        validate_config({**VL, "CALL_MODE": "transcription", "TRANSCRIPTION_BACKEND": "x"}, None)


def test_transcription_azure_speech_requires_endpoint():
    with pytest.raises(SystemExit):
        validate_config({"CALL_MODE": "transcription", "TRANSCRIPTION_BACKEND": "azure_speech"}, None)


def test_transcription_accepts_speech_endpoint_without_credentials():
    cfg = {"CALL_MODE": "transcription", "AZURE_SPEECH_ENDPOINT": "https://s/"}
    assert validate_config(cfg, None) is False  # no provider; web harness only


def test_transcription_with_genesys_provider_is_ready(monkeypatch):
    monkeypatch.setenv("GENESYS_API_KEY", "g")
    cfg = {"CALL_MODE": "transcription", "TRANSCRIPTION_BACKEND": "fake"}
    assert validate_config(cfg, "genesys") is True


def test_transcription_with_non_genesys_provider_is_not_registered(monkeypatch):
    monkeypatch.setenv("ACS_CONNECTION_STRING", "x")
    cfg = {"CALL_MODE": "transcription", "TRANSCRIPTION_BACKEND": "fake"}
    assert validate_config(cfg, "acs") is False
