import sys
import time

import requests
from loguru import logger
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from selenium.common.exceptions import NoSuchElementException
from config.constants import URL, DOWNLOADS_PATH, USERNAME_VAL, PASSWORD_VAL
from src.download_data import download_csv
from utils.web_scraper import click_element


def main():
    # Initialize logs
    logger.add(".log", rotation="30 days", retention=12)
    logger.remove()
    logger.add(sys.stdout, level="INFO",
               format="{time:YYYY-MM-DD > HH:mm:ss} | {level} | {module} | {function} | {line} | {message}")
    logger.add(".log", level="INFO",
               format="{time:YYYY-MM-DD > HH:mm:ss} | {level} | {module} | {function} | {line} | {message}")

    logger.info("Process started")

    # webdriver options
    options = EdgeOptions()
    options.use_chromium = True
    options.add_experimental_option("prefs", {"download.default_directory": DOWNLOADS_PATH})

    # Get webdriver
    driver = webdriver.Chrome(options=options)

    # Send a GET request to the URL
    response = requests.get(URL)

    if response.status_code == 200:
        # Navigate to the website
        driver.get(URL)

        # Wait for the cookies popup to be present
        cookies_popup = WebDriverWait(driver, 30).until(ec.presence_of_element_located((By.ID, "truste-consent-content")))
        # Find the button to reject or close the cookies popup
        try:
            reject_button = cookies_popup.find_element(By.XPATH, "//button[text()='Rechazar']")
            reject_button.click()
        except NoSuchElementException:
            pass

        # Find the username and password input fields
        username_input = driver.find_element(By.ID, "alias")
        password_input = driver.find_element(By.ID, "password")
        # Enter credentials
        username_input.send_keys(USERNAME_VAL)
        password_input.send_keys(PASSWORD_VAL)

        # Find the button to access the website
        access_button = driver.find_element(By.ID, "loginButton")
        if access_button:
            access_button.click()
            time.sleep(15)

            # Download last six months of data
            download_csv(driver)

            # Change CUPS
            click_element(driver, By.XPATH, '//div[@class="chosen-container chosen-container-single chosen-container-single-nosearch"]', 15, "CUPS dropdown")
            click_element(driver, By.CSS_SELECTOR, "li.active-result[data-option-array-index='1']", 15, "Select CUPS")
            driver.refresh()
            time.sleep(15)

            # Download last six months of data of the other CUPS
            download_csv(driver)

    logger.info("Process finished")


if __name__ == "__main__":
    main()
