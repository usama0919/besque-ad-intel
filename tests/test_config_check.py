import pytest
from src import config_check


def test_validate_passes_when_all_present(monkeypatch):
    for k in config_check.REQUIRED:
        monkeypatch.setenv(k, "x")
    assert config_check.validate_config() is True


def test_validate_fails_with_missing(monkeypatch):
    for k in config_check.REQUIRED:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(EnvironmentError) as e:
        config_check.validate_config()
    assert "Missing required" in str(e.value)
