import sys
import time

from loguru import logger


def click_element(driver, by, selector, sleep_time, error_element):
    try:
        driver.find_element(by, selector).click()
        time.sleep(sleep_time)

    except Exception as e:
        logger.error(f"Error when clicking {error_element}. Exception: {e}")
        logger.info("Process finished")
        sys.exit()


def click_element_from_list(driver, by, selector, sleep_time, element_list_position, error_element):
    try:
        button = driver.find_elements(by, selector)
        button[element_list_position].click()
        time.sleep(sleep_time)

    except Exception as e:
        logger.error(f"Error when clicking {error_element}. Exception: {e}")
        logger.info("Process finished")
        sys.exit()
