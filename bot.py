import logging
import asyncio
from datetime import datetime
from pyrogram import Client
from pyrogram.enums import ParseMode
from config import APP_ID, API_HASH, WORKERS, FILES, FORCESUB, ADMINS, OWNER
from database import get_setting

LOGGER = logging.getLogger(__name__)


class StoreBot:
    def __init__(self, name, token, bot_id, register=None, in_memory=False):
        self._client_args = dict(
            name=name,
            api_id=APP_ID,
            api_hash=API_HASH,
            bot_token=token,
            workers=WORKERS,
            in_memory=in_memory,
        )
        self.client = Client(**self._client_args)
        self.token = token
        self.bot_id = bot_id
        self.register = register
        self.username = None
        self.db_channel = None
        self.invitelink = None
        self.files = FILES
        self.forcesub = FORCESUB
        self.admins = ADMINS[:]
        self.uptime = None
        self.alerted = False

    async def value(self, key, default):
        return await asyncio.to_thread(get_setting, self.bot_id, key, default)

    async def alert(self, text):
        """Message the owner from this bot itself. Never raises.

        Telegram only lets a bot message users who have pressed Start on it,
        so this returns False until the owner has started this bot once.
        """
        try:
            await self.client.send_message(OWNER, text)
            return True
        except Exception as e:
            LOGGER.warning("Bot %s could not alert the owner: %s", self.bot_id, e)
            return False

    async def start(self, event="Started"):
        if self.client.is_connected:
            return

        self.alerted = False
        await self.client.start()

        # Pyrogram clears the dispatcher's handlers when a client is stopped,
        # so a restarted client would stay connected but never answer anyone.
        if self.register and not self.client.dispatcher.groups:
            self.register(self.client)

        try:
            me = await self.client.get_me()
            self.username = me.username or me.first_name
            self.uptime = datetime.now()

            self.client.username = self.username
            self.client.uptime = self.uptime
            self.client.bot_id = self.bot_id
            self.client.store = self
            self.client.set_parse_mode(ParseMode.HTML)

            self.files = int(await self.value("files", FILES))
            self.forcesub = int(await self.value("forcesub", FORCESUB))
            self.admins = [int(x) for x in await self.value("admins", ADMINS)]

            if not self.files:
                raise RuntimeError("FILES is not configured")

            self.db_channel = await self.client.get_chat(self.files)
            self.client.db_channel = self.db_channel
            self.client.files = self.files
            self.client.forcesub = self.forcesub
            self.client.admins = self.admins

            if self.forcesub:
                chat = await self.client.get_chat(self.forcesub)
                self.invitelink = chat.invite_link or await self.client.export_chat_invite_link(self.forcesub)
            else:
                self.invitelink = None

            self.client.invitelink = self.invitelink
            LOGGER.info("Started @%s (%s)", self.username, self.bot_id)

            self.alerted = await self.alert(
                f"🟢 <b>{event}</b>\n\nBot: @{self.username}\nID: <code>{self.bot_id}</code>"
            )
        except Exception:
            await self.stop()
            raise

    async def stop(self):
        session = getattr(self.client, "http_session", None)
        if session is not None and not session.closed:
            try:
                await session.close()
            except Exception:
                LOGGER.exception("Failed to close HTTP session for bot %s", self.bot_id)
        try:
            if self.client.is_connected:
                await self.client.stop()
        except Exception:
            LOGGER.exception("Failed to stop bot %s", self.bot_id)

    async def restart(self):
        await self.stop()
        # Re-using a stopped Pyrogram client is unreliable (it can come back
        # connected but silent), so every restart builds a brand-new client.
        self.client = Client(**self._client_args)
        await self.start("Restarted")
