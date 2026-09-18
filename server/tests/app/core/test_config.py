"""pydantic-settings 配置测试。"""

from server.app.core.config import Settings


def test_settings_defaults() -> None:
    """未提供环境变量时取默认值。"""
    settings = Settings(_env_file=None)
    assert settings.provider == "openai_compat"
    assert settings.provider_base_url == "https://api.openai.com/v1"
    assert settings.provider_api_key == ""
    assert settings.provider_model == "gpt-image-1"
    assert settings.provider_timeout_seconds == 120.0


def test_settings_read_from_env(monkeypatch) -> None:
    """环境变量可覆盖默认值（.env 与环境变量同机制）。"""
    monkeypatch.setenv("PROVIDER", "comfyui")
    monkeypatch.setenv("PROVIDER_BASE_URL", "http://127.0.0.1:8188")
    monkeypatch.setenv("PROVIDER_API_KEY", "sk-test")
    monkeypatch.setenv("PROVIDER_MODEL", "sdxl")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "30")

    settings = Settings(_env_file=None)
    assert settings.provider == "comfyui"
    assert settings.provider_base_url == "http://127.0.0.1:8188"
    assert settings.provider_api_key == "sk-test"
    assert settings.provider_model == "sdxl"
    assert settings.provider_timeout_seconds == 30.0


def test_settings_rejects_invalid_provider(monkeypatch) -> None:
    """非法 provider 值直接校验失败，而不是静默落到错误后端。"""
    monkeypatch.setenv("PROVIDER", "midjourney")
    try:
        Settings(_env_file=None)
    except Exception as exc:  # noqa: BLE001 - pydantic ValidationError 断言即可
        assert "provider" in str(exc)
    else:
        raise AssertionError("非法 provider 值应触发校验错误")
