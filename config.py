import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in .env file")
if not CHANNEL_ID:
    raise ValueError("CHANNEL_ID is not set in .env file")
if ADMIN_USER_ID == 0:
    raise ValueError("ADMIN_USER_ID is not set in .env file")
