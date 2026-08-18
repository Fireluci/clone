import os
import logging

BOTTOKEN = os.getenv("BOTTOKEN", "")
MONGODB = os.getenv("MONGODB", "")
DBNAME = os.getenv("DBNAME", "manager")
APP_ID = int(os.getenv("APP_ID", "1736204"))
API_HASH = os.getenv("API_HASH", "")
WORKERS = int(os.getenv("WORKERS", "4"))
OWNER = int(os.getenv("OWNER", "1058015838"))
FILES = int(os.getenv("FILES", "-1001205507869"))
FORCESUB = int(os.getenv("FORCESUB", "0"))
SHORTSITE = os.getenv("SHORTSITE", "nowshort.com")
SHORTAPI = os.getenv("SHORTAPI", "")
ADMINS = [int(x) for x in os.getenv("ADMINS", "5051689666 1058015838 6012123382").split() if x.lstrip("-").isdigit()]
PORT = int(os.getenv("PORT", "8080"))

START_MESSAGE = "<b>🌟 Hello {first},\n\nI'm A File Store Bot 🤖 designed to provide files 📁 and links 🔗</b>"
FORCE_SUB_MESSAGE = "<b>🌟 Hello {first},\n\nI'm A File Store Bot 🤖 designed to provide files 📁 and links 🔗</b>\n\n<b>You need to join my channel to use this bot.</b>"

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logging.getLogger("pyrogram").setLevel(logging.WARNING)
LOGGER = logging.getLogger(__name__)
