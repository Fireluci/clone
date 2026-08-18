import os
import logging

BOTTOKEN = os.getenv("BOTTOKEN", "")
MONGODB = os.getenv("MONGODB", "")
DBNAME = os.getenv("DBNAME", "jisoo")
APP_ID = int(os.getenv("APP_ID", "1736204"))
API_HASH = os.getenv("API_HASH", "")
OWNER = int(os.getenv("OWNER", "0"))
FILES = int(os.getenv("FILES", "0"))
FORCESUB = int(os.getenv("FORCESUB", "0"))
SHORTSITE = os.getenv("SHORTSITE", "nowshort.com")
SHORTAPI = os.getenv("SHORTAPI", "")
ADMINS = [int(x) for x in os.getenv("ADMINS", str(OWNER)).split() if x.strip().lstrip("-").isdigit()]
WORKERS = int(os.getenv("WORKERS", "4"))
PORT = int(os.getenv("PORT", "8080"))

START_MESSAGE = "Hello {first}\n\nI can store private files and give you access links."
FORCE_SUB_MESSAGE = "Hello {first}\n\n<b>You need to join my channel to use this bot.</b>\n\nPlease join the channel and try again."

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s"
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)

LOGGER = logging.getLogger
