import unittest

from src.scraper import WebScraper


class WebScraperTests(unittest.TestCase):
    def test_period_filename_normalizes_date_range(self) -> None:
        result = WebScraper._period_filename("18/02/2026 - 17/03/2026")

        self.assertEqual(result, "2026-02-18_2026-03-17")

    def test_period_filename_ignores_unrecognized_labels(self) -> None:
        self.assertEqual(WebScraper._period_filename("Select a period"), "")
