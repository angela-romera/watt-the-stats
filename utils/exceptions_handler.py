import sys

from loguru import logger


def error_exit(error_message, e):
    logger.error(f"{error_message}. Exception: {e}")
    logger.info("Process finished")
    sys.exit()
