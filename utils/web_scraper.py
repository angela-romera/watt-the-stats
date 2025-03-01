import time

from utils.exceptions_handler import error_exit


def click_element(driver, by, selector, sleep_time, error_element):
    try:
        driver.find_element(by, selector).click()
        time.sleep(sleep_time)

    except Exception as e:
        error_exit(f"Error when clicking {error_element}", e)


def click_element_from_list(driver, by, selector, sleep_time, element_list_position, error_element):
    try:
        button = driver.find_elements(by, selector)
        button[element_list_position].click()
        time.sleep(sleep_time)

    except Exception as e:
        error_exit(f"Error when clicking {error_element}", e)
