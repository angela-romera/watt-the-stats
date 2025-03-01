import time

from selenium.webdriver.common.by import By


# Download data
def download_csv(driver):
    for idx in range(1, 7):
        # Select period
        time.sleep(15)
        dropdown_button = driver.find_elements(By.XPATH, '//span[@class="chosen-single"]')
        dropdown_button[1].click()
        time.sleep(5)
        try:
            test_click = driver.find_element(By.CSS_SELECTOR, f'li.active-result[data-option-array-index="{idx}"]')
            test_click.click()
            time.sleep(15)
        except:
            driver.find_element(By.CSS_SELECTOR, f'li.active-result[data-option-array-index="{idx}"]').click()
            time.sleep(15)

        # Download sheet button
        driver.find_element(By.XPATH, '//a[@id="downloadSheet"]').click()
        time.sleep(3)

        # Save as csv
        driver.find_element(By.ID, 'csv').click()
        time.sleep(15)

        # Refresh the screen
        driver.refresh()
        time.sleep(15)
