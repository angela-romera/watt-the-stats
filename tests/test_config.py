import unittest

from src.config import load_settings


class SettingsTests(unittest.TestCase):
    def test_project_settings_are_valid(self) -> None:
        settings = load_settings()

        self.assertTrue(settings.url.startswith("https://"))
        self.assertIn(settings.browser, {"chrome", "edge"})
        self.assertTrue(settings.download_dir.is_dir())
        self.assertGreater(settings.download_timeout_seconds, 0)
        self.assertGreater(settings.periods_to_download, 0)


if __name__ == "__main__":
    unittest.main()
