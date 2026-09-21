import unittest
from unittest.mock import patch

import pytest

from src.config import load_settings


class SettingsTests(unittest.TestCase):
    def test_project_settings_are_valid(self) -> None:
        with patch("src.config.load_dotenv"), patch.dict("os.environ", {}, clear=True):
            settings = load_settings()

        self.assertTrue(settings.url.startswith("https://"))
        self.assertIn(settings.browser, {"chrome", "edge"})
        self.assertTrue(settings.download_dir.is_dir())
        self.assertGreater(settings.download_timeout_seconds, 0)
        self.assertGreater(settings.periods_to_download, 0)


if __name__ == "__main__":
    unittest.main()


@pytest.fixture
def credential_env(monkeypatch):
    monkeypatch.setattr("src.config.load_dotenv", lambda *_args: None)
    monkeypatch.delenv("USER_ENERGIAXXI_LIST", raising=False)
    monkeypatch.delenv("PWD_ENERGIAXXI_LIST", raising=False)
    return monkeypatch


def test_load_credential_lists_preserves_passwords(credential_env):
    import json

    passwords = [' space,quote"slash\\ ', "second-secret"]
    credential_env.setenv("USER_ENERGIAXXI_LIST", '["first", "second"]')
    credential_env.setenv("PWD_ENERGIAXXI_LIST", json.dumps(passwords))
    settings = load_settings()
    assert settings.usernames == ["first", "second"]
    assert settings.passwords == passwords
    assert "second-secret" not in repr(settings)


@pytest.mark.parametrize(
    "value", ["plain-secret", '"scalar"', "{}", "null", "[1]", '[""]', '[" "]']
)
@pytest.mark.parametrize("name", ["USER_ENERGIAXXI_LIST", "PWD_ENERGIAXXI_LIST"])
def test_reject_invalid_credential_lists(credential_env, name, value):
    credential_env.setenv(name, value)
    with pytest.raises(ValueError, match=f"{name} must be a JSON list") as error:
        load_settings()
    assert "plain-secret" not in str(error.value)


def test_reject_mismatched_credentials(credential_env):
    credential_env.setenv("USER_ENERGIAXXI_LIST", '["first", "second"]')
    credential_env.setenv("PWD_ENERGIAXXI_LIST", '["secret"]')
    with pytest.raises(ValueError, match="same length"):
        load_settings()


def test_missing_credentials_allow_processing(credential_env):
    settings = load_settings()
    assert settings.usernames == settings.passwords == []
