"""Tests for Gemini backend config."""
from cli_bridge.config.schema import DriverConfig, GeminiBackendConfig


def test_gemini_backend_config_defaults():
    cfg = GeminiBackendConfig()
    assert cfg.gemini_path == "gemini"
    assert cfg.model == "gemini-2.5-pro"
    assert cfg.yolo is True


def test_driver_config_gemini_backend_populates_gemini_config():
    cfg = DriverConfig(backend="gemini")
    assert cfg.gemini is not None
    assert isinstance(cfg.gemini, GeminiBackendConfig)


def test_driver_config_gemini_backend_accepts_explicit_config():
    cfg = DriverConfig(backend="gemini", gemini=GeminiBackendConfig(model="gemini-2.0-flash"))
    assert cfg.gemini.model == "gemini-2.0-flash"
