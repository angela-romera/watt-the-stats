import time

from selenium.webdriver.common.by import By

from utils.web_scraper import click_element, click_element_from_list


# Download data
def download_csv(driver):
    for idx in range(1, 7):
        # Select period
        time.sleep(15)
        click_element_from_list(driver, By.XPATH, "//span[@class='chosen-single']", 5, 1, "Period dropdown")
        click_element(driver, By.XPATH, f"li.active-result[data-option-array-index='{idx}]'", 15, "Select period")

        # Download sheet button
        click_element(driver, By.XPATH, "//a[@id='downloadSheet']", 3, "Download sheet button")

        # Save as csv
        click_element(driver, By.ID, "csv", 15, "Save as csv")

        # Refresh the screen
        driver.refresh()
        time.sleep(15)
