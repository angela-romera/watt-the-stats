import sys

from loguru import logger


def log_exit(error_message, error_type, e):
    if error_type.upper()=="ERROR":
        logger.error(f"{error_message}. Exception: {e}")
    elif error_type.upper()=="INFO":
        logger.info(f"{error_message}. Exception: {e}")
    elif error_type.upper() == "WARNING":
        logger.warning(f"{error_message}. Exception: {e}")
    else:
        logger.info(f"Error type {error_type} is not contemplated")

    logger.info("Process finished")
    sys.exit()
