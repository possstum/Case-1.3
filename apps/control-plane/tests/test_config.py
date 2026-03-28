from app.config import Settings


def test_settings_accept_comma_separated_env_values() -> None:
    settings = Settings(
        api_keys="key-a, key-b",
        available_models="model-a, model-b",
    )

    assert settings.api_keys == ["key-a", "key-b"]
    assert settings.available_models == ["model-a", "model-b"]
