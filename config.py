import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# ADMIN_IDS can be comma-separated strings in .env: "123456,7891011"
admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in admin_ids_raw.split(",") if x.strip().isdigit()]

# Storage channel where bot is admin
STORAGE_CHANNEL_ID = os.getenv("STORAGE_CHANNEL_ID")
if STORAGE_CHANNEL_ID:
    try:
        STORAGE_CHANNEL_ID = int(STORAGE_CHANNEL_ID)
    except ValueError:
        STORAGE_CHANNEL_ID = None
