import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    API_ID = os.getenv("API_ID")
    API_HASH = os.getenv("API_HASH")
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    GOFILE_TOKEN = os.getenv("GOFILE_TOKEN")
    GOFILE_ACCOUNT_ID = os.getenv("GOFILE_ACCOUNT_ID")
    OWNER_ID = int(os.getenv("OWNER_ID"))
    UPLOAD_INTERVAL = int(os.getenv("UPLOAD_INTERVAL"))
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")