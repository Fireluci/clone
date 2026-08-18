from datetime import datetime
from pyromod import listen
from pyrogram import Client
from pyrogram.enums import ParseMode
from config import APP_ID, API_HASH, WORKERS, FILES, FORCESUB, ADMINS
from database import get_setting


class StoreBot:
    def __init__(self, name, token, bot_id):
        self.client = Client(name=name, api_id=APP_ID, api_hash=API_HASH, bot_token=token, workers=WORKERS)
        self.token = token
        self.bot_id = bot_id
        self.username = None
        self.db_channel = None
        self.invitelink = None
        self.files = 0
        self.forcesub = 0
        self.admins = []
        self.uptime = None

    def value(self, key, default):
        return get_setting(self.bot_id, key, default)

    async def start(self):
        await self.client.start()
        me = await self.client.get_me()
        self.username = me.username or me.first_name
        self.uptime = datetime.now()
        self.client.username = self.username
        self.client.uptime = self.uptime
        self.client.bot_id = self.bot_id
        self.client.store = self
        self.client.set_parse_mode(ParseMode.HTML)

        self.files = int(self.value("files", 0))
        self.forcesub = int(self.value("forcesub", 0))
        self.admins = [int(x) for x in self.value("admins", [])]

        if not self.files:
            await self.stop()
            raise RuntimeError("FILES is not configured")

        try:
            self.db_channel = await self.client.get_chat(self.files)
            test = await self.client.send_message(self.db_channel.id, "test")
            await test.delete()
        except Exception as e:
            await self.stop()
            raise RuntimeError(f"Cannot access FILES channel: {e}")

        self.client.db_channel = self.db_channel
        self.client.files = self.files
        self.client.forcesub = self.forcesub
        self.client.admins = self.admins

        if self.forcesub:
            try:
                chat = await self.client.get_chat(self.forcesub)
                self.invitelink = chat.invite_link or await self.client.export_chat_invite_link(self.forcesub)
            except Exception as e:
                await self.stop()
                raise RuntimeError(f"Cannot access FORCESUB channel: {e}")
        self.client.invitelink = self.invitelink

        print(f"Started @{self.username}")

    async def stop(self):
        try:
            await self.client.stop()
        except Exception:
            pass
