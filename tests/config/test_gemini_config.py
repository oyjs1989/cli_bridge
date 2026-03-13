"""Tests for Gemini backend config."""
from cli_bridge.config.schema import DriverConfig, GeminiBackendConfig


def test_gemini_backend_config_defaults():
    cfg = GeminiBackendConfig()
    assert cfg.gemini_path == "gemini"
    assert cfg.model == "gemini-2.5-pro"
    assert cfg.yolo is True
    assert cfg.api_key == ""
    assert cfg.google_api_key == ""
    assert cfg.sandbox is False


def test_driver_config_gemini_backend_populates_gemini_config():
    cfg = DriverConfig(backend="gemini")
    assert cfg.gemini is not None
    assert isinstance(cfg.gemini, GeminiBackendConfig)


def test_driver_config_gemini_backend_accepts_explicit_config():
    cfg = DriverConfig(backend="gemini", gemini=GeminiBackendConfig(model="gemini-2.0-flash"))
    assert cfg.gemini.model == "gemini-2.0-flash"


def test_config_get_model_for_gemini_backend():
    from cli_bridge.config.schema import Config, DriverConfig, GeminiBackendConfig
    cfg = Config(driver=DriverConfig(backend="gemini", gemini=GeminiBackendConfig(model="gemini-2.0-flash")))
    assert cfg.get_model() == "gemini-2.0-flash"


def test_gemini_backend_config_extra_fields_ignored():
    cfg = GeminiBackendConfig(**{"gemini_path": "gemini", "unknown_field": "value"})
    assert not hasattr(cfg, "unknown_field")
