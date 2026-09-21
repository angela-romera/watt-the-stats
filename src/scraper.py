import re
import time
from datetime import datetime
from pathlib import Path
from types import TracebackType

from loguru import logger
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from src.config import Settings


class ScrapingError(RuntimeError):
    """Raised when the portal cannot complete a scraping step."""


class WebScraper:
    """Log in to Energia XXI and download every available consumption CSV."""

    def __init__(self, settings: Settings, username: str, password: str) -> None:
        self.settings = settings
        self.username = username
        self.password = password
        self.driver: WebDriver | None = None

    def __enter__(self) -> "WebScraper":
        self.driver = self._create_driver()
        self.driver.set_page_load_timeout(self.settings.wait_timeout_seconds)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.driver is not None:
            self.driver.quit()

    @property
    def wait(self) -> WebDriverWait:
        return WebDriverWait(self._require_driver(), self.settings.wait_timeout_seconds)

    def download(self) -> list[Path]:
        """Download configured periods for every contract in the account."""
        self._login()
        contract_count = self._option_count("contrato")
        if contract_count == 0:
            raise ScrapingError("No contracts were found in the Energia XXI account")

        downloaded_files: list[Path] = []
        logger.info("Found {} contract(s)", contract_count)

        for contract_index in range(contract_count):
            self._ensure_contract_selected(contract_index)
            available_periods = self._option_count("periodo_consumo") - 1
            period_count = min(self.settings.periods_to_download, available_periods)
            logger.info(
                "Downloading {} period(s) for contract {} of {}",
                period_count,
                contract_index + 1,
                contract_count,
            )

            for period_index in range(1, period_count + 1):
                self._ensure_contract_selected(contract_index)
                period_label = self._select_index("periodo_consumo", period_index)
                self._wait_for_consumption_data()
                downloaded_file = self._download_csv(
                    contract_index=contract_index,
                    period_index=period_index,
                    period_label=period_label,
                )
                downloaded_files.append(downloaded_file)
                logger.info("Saved {}", downloaded_file.name)
                self._refresh_consumption_page()

        return downloaded_files

    def _create_driver(self) -> WebDriver:
        preferences = {
            "download.default_directory": str(self.settings.download_dir),
            "download.directory_upgrade": True,
            "download.prompt_for_download": False,
            "safebrowsing.enabled": True,
        }

        if self.settings.browser == "edge":
            options = EdgeOptions()
            options.add_experimental_option("prefs", preferences)
            self._add_browser_arguments(options)
            return webdriver.Edge(options=options)

        options = ChromeOptions()
        options.add_experimental_option("prefs", preferences)
        self._add_browser_arguments(options)
        return webdriver.Chrome(options=options)

    def _add_browser_arguments(self, options: ChromeOptions | EdgeOptions) -> None:
        options.add_argument("--window-size=1920,1080")
        if self.settings.headless:
            options.add_argument("--headless=new")

    def _login(self) -> None:
        if not self.username or not self.password:
            raise ScrapingError("An Energia XXI username and password are required")

        driver = self._require_driver()
        driver.get(self.settings.url)

        try:
            username_field = self.wait.until(EC.visibility_of_element_located((By.ID, "alias")))
            password_field = driver.find_element(By.ID, "password")
            username_field.clear()
            username_field.send_keys(self.username)
            password_field.clear()
            password_field.send_keys(self.password)
            driver.find_element(By.ID, "loginButton").click()
            self.wait.until(EC.presence_of_element_located((By.ID, "contrato")))
        except TimeoutException as exc:
            message = self._visible_login_error()
            detail = f": {message}" if message else ""
            raise ScrapingError(f"Energia XXI login did not complete{detail}") from exc

        logger.info("Logged in to Energia XXI")

    def _visible_login_error(self) -> str:
        driver = self._require_driver()
        for selector in ("[role='alert']", ".alert", "[class*='error']"):
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                if element.is_displayed() and element.text.strip():
                    return " ".join(element.text.split())[:300]
        return ""

    def _wait_for_periods(self) -> None:
        try:
            self.wait.until(lambda _: self._option_count("periodo_consumo") > 1)
        except TimeoutException as exc:
            raise ScrapingError("Consumption periods did not load") from exc
        self._wait_for_consumption_data()

    def _ensure_contract_selected(self, contract_index: int) -> None:
        driver = self._require_driver()
        selected_index = int(
            driver.execute_script(
                """
                const select = document.getElementById('contrato');
                return select ? select.selectedIndex : -1;
                """
            )
        )
        if selected_index != contract_index:
            self._select_index("contrato", contract_index)
        self._wait_for_periods()

    def _refresh_consumption_page(self) -> None:
        driver = self._require_driver()
        driver.refresh()
        try:
            self.wait.until(EC.presence_of_element_located((By.ID, "contrato")))
        except TimeoutException as exc:
            raise ScrapingError("Consumption page did not reload after a download") from exc

    def _wait_for_consumption_data(self) -> None:
        driver = self._require_driver()
        time.sleep(0.5)
        try:
            self.wait.until(
                lambda _: (
                    not driver.execute_script(
                        """
                    const loader = document.querySelector('.bannerLoadingGrafConsum');
                    return loader && Boolean(loader.offsetWidth || loader.offsetHeight);
                    """
                    )
                )
            )
            self.wait.until(EC.presence_of_element_located((By.ID, "downloadSheet")))
        except TimeoutException as exc:
            raise ScrapingError("Consumption data did not finish loading") from exc

    def _select_index(self, element_id: str, index: int) -> str:
        driver = self._require_driver()
        result = driver.execute_script(
            """
            const select = document.getElementById(arguments[0]);
            const index = arguments[1];
            if (!select || index < 0 || index >= select.options.length) {
                return null;
            }
            select.selectedIndex = index;
            select.dispatchEvent(new Event('change', {bubbles: true}));
            return select.options[index].text;
            """,
            element_id,
            index,
        )
        if result is None:
            raise ScrapingError(f"Could not select index {index} from {element_id}")
        return str(result).strip()

    def _option_count(self, element_id: str) -> int:
        driver = self._require_driver()
        return int(
            driver.execute_script(
                """
                const select = document.getElementById(arguments[0]);
                return select ? select.options.length : 0;
                """,
                element_id,
            )
        )

    def _download_csv(
        self,
        contract_index: int,
        period_index: int,
        period_label: str,
    ) -> Path:
        driver = self._require_driver()
        before = self._download_snapshot()

        if not driver.execute_script(
            """
            const button = document.getElementById('downloadSheet');
            if (!button) return false;
            button.click();
            return true;
            """
        ):
            raise ScrapingError("The spreadsheet download control was not found")

        try:
            self.wait.until(
                lambda _: driver.execute_script(
                    """
                    const button = document.getElementById('csv');
                    return button && Boolean(button.offsetWidth || button.offsetHeight);
                    """
                )
            )
        except TimeoutException as exc:
            raise ScrapingError("The CSV download option did not open") from exc

        driver.execute_script("document.getElementById('csv').click();")
        source = self._wait_for_download(before)
        target = self._unique_download_path(
            contract_index=contract_index,
            period_index=period_index,
            period_label=period_label,
        )
        source.replace(target)
        return target

    def _download_snapshot(self) -> dict[Path, tuple[int, int]]:
        return {
            path: (path.stat().st_size, path.stat().st_mtime_ns)
            for path in self.settings.download_dir.iterdir()
            if path.is_file()
        }

    def _wait_for_download(self, before: dict[Path, tuple[int, int]]) -> Path:
        deadline = time.monotonic() + self.settings.download_timeout_seconds
        last_candidate: Path | None = None
        stable_checks = 0

        while time.monotonic() < deadline:
            files = [path for path in self.settings.download_dir.iterdir() if path.is_file()]
            partial_files = [
                path for path in files if path.suffix.lower() in {".crdownload", ".part", ".tmp"}
            ]
            changed_files = [
                path
                for path in files
                if path.suffix.lower() == ".csv"
                and (
                    path not in before
                    or (path.stat().st_size, path.stat().st_mtime_ns) != before[path]
                )
            ]

            if changed_files and not partial_files:
                candidate = max(changed_files, key=lambda path: path.stat().st_mtime_ns)
                if candidate == last_candidate:
                    stable_checks += 1
                else:
                    last_candidate = candidate
                    stable_checks = 1
                if stable_checks >= 2:
                    return candidate

            time.sleep(0.5)

        raise ScrapingError(
            f"CSV download did not finish within {self.settings.download_timeout_seconds} seconds"
        )

    def _unique_download_path(
        self,
        contract_index: int,
        period_index: int,
        period_label: str,
    ) -> Path:
        period_name = self._period_filename(period_label) or f"period_{period_index:02d}"
        base_name = f"consumption_contract_{contract_index + 1:02d}_{period_name}"
        candidate = self.settings.download_dir / f"{base_name}.csv"
        counter = 2
        while candidate.exists():
            candidate = self.settings.download_dir / f"{base_name}_{counter}.csv"
            counter += 1
        return candidate

    @staticmethod
    def _period_filename(label: str) -> str:
        dates = re.findall(r"\d{1,2}[/-]\d{1,2}[/-]\d{4}", label)
        normalized_dates: list[str] = []
        for value in dates:
            normalized = value.replace("-", "/")
            try:
                normalized_dates.append(
                    datetime.strptime(normalized, "%d/%m/%Y").strftime("%Y-%m-%d")
                )
            except ValueError:
                continue
        return "_".join(normalized_dates)

    def _require_driver(self) -> WebDriver:
        if self.driver is None:
            raise RuntimeError("WebScraper must be used as a context manager")
        return self.driver
