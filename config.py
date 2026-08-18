import os
import logging

BOTTOKEN = os.getenv("BOTTOKEN", "8191103559:AAHL8HM-MgOPT-AakHL_37mtLaor0XEElDk")
MONGODB = os.getenv("MONGODB", "mongodb+srv://herostore:herostore@herostore.ywtvule.mongodb.net/?appName=herostore")

APP_ID = int(os.getenv("APP_ID", "1736204"))
API_HASH = os.getenv("API_HASH", "890d40e0f91a4de32dec2965444b2cbe")

OWNER = int(os.getenv("OWNER", "1058015838"))
FILES = int(os.getenv("FILES", "-1001205507869"))
FORCESUB = int(os.getenv("FORCESUB", "0"))

SHORTSITE = os.getenv("SHORTSITE", "nowshort.com")
SHORTAPI = os.getenv("SHORTAPI", "c576b9cdd34ceb572a8df1f57eabf7c11efbda3a")

ADMINS = [5051689666, 1058015838, 6012123382]
PORT = int(os.getenv("PORT", "8080"))

START_MESSAGE = "Hello {first}\n\nI can store private files and give you access links."
FORCE_SUB_MESSAGE = "Hello {first}\n\n<b>You need to join my channel to use this bot.</b>\n\nPlease join the channel and try again."

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logging.getLogger("pyrogram").setLevel(logging.WARNING)
LOGGER = logging.getLogger(__name__)
