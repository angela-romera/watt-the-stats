import unittest
from unittest.mock import Mock, PropertyMock, patch

from selenium.webdriver.common.by import By

from src.scraper import WebScraper
from tests.test_import_files import make_settings


class WebScraperTests(unittest.TestCase):
    def test_period_filename_normalizes_date_range(self) -> None:
        result = WebScraper._period_filename("18/02/2026 - 17/03/2026")

        self.assertEqual(result, "2026-02-18_2026-03-17")

    def test_period_filename_ignores_unrecognized_labels(self) -> None:
        self.assertEqual(WebScraper._period_filename("Select a period"), "")


def test_login_uses_selected_credential_pair(tmp_path):
    scraper = WebScraper(make_settings(tmp_path), "second-user", "second-password")
    scraper.driver = Mock()
    username_field, password_field, button = Mock(), Mock(), Mock()
    scraper.driver.find_element.side_effect = [password_field, button]
    with patch.object(WebScraper, "wait", new_callable=PropertyMock) as wait:
        wait.return_value.until.side_effect = [username_field, Mock()]
        scraper._login()
    username_field.send_keys.assert_called_once_with("second-user")
    password_field.send_keys.assert_called_once_with("second-password")
    scraper.driver.find_element.assert_any_call(By.ID, "password")
    button.click.assert_called_once_with()
