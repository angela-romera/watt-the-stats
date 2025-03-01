import json
import os

from dotenv import load_dotenv

# Load environment constants
load_dotenv()

# Load configuration
with open('settings.json', 'r') as f:
    config = json.load(f)

# Path for downloading
DOWNLOADS_PATH = os.path.realpath(config["download_folder"])

# Scraperbot constants
URL = config["url"]
USERNAME_VAL = os.getenv('USER_ENERGIAXXI_1')
PASSWORD_VAL = os.getenv('PWD_ENERGIAXXI_1')

# DB constants
SERVER_WTS = os.getenv('SERVER_WTS')
DB_WTS_ELEC = os.getenv('DB_WTS_ELEC')
USER_DB_WTS = os.getenv('USER_DB_WTS')
PWD_DB_WTS = os.getenv('PWD_DB_WTS')