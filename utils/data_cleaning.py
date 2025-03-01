from datetime import datetime
from datetime import time

import pandas as pd

from utils.exceptions_handler import error_exit


def load_csv(file_path, skip_rows, headers):
    try:
        return pd.read_csv(file_path, skiprows=skip_rows, names=headers)

    except Exception as e:
        error_exit(f"Error in load_csv()", e)


def transform_to_time(value):
    try:
        # Handle the special case where value is 24.0
        if value == 24.0:
            value = 0.0

        # Convert the float value to an integer representing hours
        hours = int(value)

        # Extract the decimal part of the value to represent minutes
        minutes_decimal = (value - hours) * 60

        # Calculate minutes and seconds
        minutes = int(minutes_decimal)
        seconds_decimal = (minutes_decimal - minutes) * 60
        seconds = int(seconds_decimal)

        return time(hours, minutes, seconds)  # return time object

    except Exception as e:
        error_exit(f"Error in transform_to_time()", e)


def transform_date(date_str):
    try:
        date_str = date_str.strip()
        date_obj = datetime.strptime(date_str, '%d/%m/%Y')

        return date_obj.strftime('%Y-%m-%d')

    except Exception as e:
        error_exit(f"Error in transform_date()", e)


def rows_to_skip(file):
    try:
        num_rows = len(open(file).readlines())

        return list(range(5, num_rows))  # return a list of indexes to be skipped

    except Exception as e:
        error_exit(f"Error in rows_to_skip()", e)
