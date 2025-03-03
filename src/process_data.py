import pandas as pd

from utils.data_cleaning import load_csv, transform_date, rows_to_skip, transform_to_time
from utils.exceptions_handler import error_exit


def get_invoice_data(file):
    # Load first part of consumption csv to have invoice info
    invoice_df = load_csv(file, rows_to_skip(file), ['Parameter', 'Value'])

    # Transpose and reorganize invoice_df
    try:
        invoice_df = invoice_df.pivot(columns='Parameter', values='Value').ffill().iloc[-1].to_frame().T
        invoice_df.columns = ['cups', 'initial_date', 'final_date', 'extraction_datetime', 'tariff']
        invoice_df = invoice_df[['initial_date', 'final_date', 'tariff', 'cups']]
        invoice_df.reset_index(drop=True, inplace=True)

    except Exception as e:
        error_exit("Error when transposing and reorganizing the invoice_df", e)

    # Transform date cols to correct format
    for col in ['initial_date', 'final_date']:
        invoice_df[col] = invoice_df[col].apply(transform_date)

    # Remove blank spaces
    try:
        invoice_df['cups'] = invoice_df['cups'].str.strip()

    except Exception as e:
        error_exit("Error when removing blank spaces", e)

    # # Add id_client col to invoice_df
    # invoice_df = add_col_from_df(DB_ELEC, invoice_df, 'cups', 'client', 'id', 'id_client')

    return invoice_df


def get_detail_data(file):
    # Load second part of consumption csv to have detail info
    detail_df = load_csv(file, 7, ['date', 'hour', 'consumption', 'price', 'cost_per_hour'])

    # Check if value of last col in last row is nan and drop it
    if pd.isna(detail_df.iloc[-1, -1]):
        detail_df.dropna(axis=0, subset=[detail_df.columns[-1]], inplace=True)

    # Get hour col in correct format
    detail_df['hour'] = detail_df['hour'].apply(transform_to_time)

    return detail_df
