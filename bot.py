import asyncio
import base64
import re
import sys
from datetime import datetime

import aiohttp
from aiohttp import web
from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated
from pyrogram.errors.exceptions.bad_request_400 import UserNotParticipant
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import database as db
from config import (
    API_HASH, APP_ID, BOTTOKEN, DBNAME, FILES, FORCESUB, LOGGER,
    OWNER, PORT, SHORTAPI, SHORTSITE, START_MESSAGE, FORCE_SUB_MESSAGE, WORKERS, ADMINS
)


def encode(value):
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def decode(value):
    value = value.strip("=")
    return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode()).decode()


def readable(seconds):
    d, seconds = divmod(int(seconds), 86400)
    h, seconds = divmod(seconds, 3600)
    m, s = divmod(seconds, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s or not parts: parts.append(f"{s}s")
    return " ".join(parts)


def get_message_id(client, message):
    if message.forward_from_chat:
        return message.forward_from_message_id if message.forward_from_chat.id == client.db_channel.id else 0
    if message.forward_sender_name:
        return 0
    if not message.text:
        return 0
    match = re.match(r"https://t.me/(?:c/)?(.*)/(\d+)", message.text)
    if not match:
        return 0
    channel, msg_id = match.group(1), int(match.group(2))
    if channel.isdigit():
        return msg_id if f"-100{channel}" == str(client.db_channel.id) else 0
    return msg_id if channel == client.db_channel.username else 0


async def get_messages(client, ids):
    ids = list(ids)
    result = []
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        try:
            result.extend(await client.get_messages(client.db_channel.id, chunk))
        except FloodWait as e:
            await asyncio.sleep(e.value)
            result.extend(await client.get_messages(client.db_channel.id, chunk))
    return result


class StoreBot:
    def __init__(self, token, bot_id, manager=False):
        self.bot_id = bot_id
        self.manager = manager
        self.uptime = datetime.now()
        self.cancel_broadcast = False
        self.client = Client(
            f"bot_{bot_id}",
            api_id=APP_ID,
            api_hash=API_HASH,
            bot_token=token,
            workers=WORKERS
        )
        self.log = LOGGER(f"bot_{bot_id}")
        self._register()

    def values(self):
        data = db.get_settings(self.bot_id)
        return {
            "files": int(data.get("files", FILES)),
            "forcesub": int(data.get("forcesub", FORCESUB)),
            "shortsite": data.get("shortsite", SHORTSITE),
            "shortapi": data.get("shortapi", SHORTAPI),
            "admins": data.get("admins", ADMINS)
        }

    async def subscribed(self, user_id):
        settings = self.values()
        if not settings["forcesub"] or user_id in settings["admins"]:
            return True
        try:
            member = await self.client.get_chat_member(settings["forcesub"], user_id)
            return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER)
        except UserNotParticipant:
            return False
        except Exception:
            return False

    async def shortlink(self, link):
        settings = self.values()
        site, api = settings["shortsite"], settings["shortapi"]
        if not site or not api:
            return link
        if site == "api.shareus.in":
            url = f"https://{site}/shortLink"
            params = {"token": api, "format": "json", "link": link}
        else:
            url = f"https://{site}/api"
            params = {"api": api, "url": link}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, ssl=False) as response:
                    data = await response.json(content_type=None)
                    if data.get("status") == "success":
                        return data.get("shortlink") or data.get("shortenedUrl") or link
        except Exception as e:
            self.log.warning(e)
        return link

    def _register(self):
        c = self.client

        @c.on_message(filters.private & filters.command("start"))
        async def start(_, message):
            settings = self.values()
            if not await self.subscribed(message.from_user.id):
                buttons = [[InlineKeyboardButton("Join Channel", url=c.invitelink)]] if getattr(c, "invitelink", None) else []
                if len(message.command) > 1:
                    buttons.append([InlineKeyboardButton("Try Again", url=f"https://t.me/{c.me.username}?start={message.command[1]}")])
                return await message.reply(
                    FORCE_SUB_MESSAGE.format(first=message.from_user.first_name),
                    reply_markup=InlineKeyboardMarkup(buttons) if buttons else None
                )

            db.add_user(self.bot_id, message.from_user.id)
            if len(message.command) < 2:
                return await message.reply_text(
                    START_MESSAGE.format(first=message.from_user.first_name),
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("😊 About Me", callback_data="about"),
                        InlineKeyboardButton("🔒 Close", callback_data="close")
                    ]])
                )

            try:
                args = decode(message.command[1]).split("-")
                if len(args) == 3:
                    start_id = int(int(args[1]) / abs(c.db_channel.id))
                    end_id = int(int(args[2]) / abs(c.db_channel.id))
                    ids = range(start_id, end_id + 1) if start_id <= end_id else range(start_id, end_id - 1, -1)
                elif len(args) == 2:
                    ids = [int(int(args[1]) / abs(c.db_channel.id))]
                else:
                    return
                wait = await message.reply("Please wait...")
                messages = await get_messages(c, ids)
                await wait.delete()
                for msg in messages:
                    await msg.copy(message.from_user.id)
                    await asyncio.sleep(.5)
            except Exception as e:
                self.log.warning(e)
                await message.reply("Something went wrong.")

        admin = filters.create(lambda _, __, m: bool(m.from_user and m.from_user.id in self.values()["admins"]))

        @c.on_message(filters.private & admin & filters.command("shortener"))
        async def shortener(_, message):
            s = self.values()
            if len(message.command) >= 3:
                db.set_settings(self.bot_id, {"shortsite": message.command[1], "shortapi": message.command[2]})
                return await message.reply(f"Shortener updated.\nSite: `{message.command[1]}`\nAPI: `{message.command[2]}`")
            await message.reply(f"Site: `{s['shortsite']}`\nAPI: `{s['shortapi']}`")

        @c.on_message(filters.private & admin & filters.command("genlink"))
        async def genlink(_, message):
            if not getattr(c, "db_channel", None):
                return await message.reply("Files channel is not configured.")
            try:
                m = await c.ask(message.from_user.id, "Forward the DB channel message or send its link.", timeout=60)
                msg_id = get_message_id(c, m)
                if not msg_id:
                    return await m.reply("Invalid DB channel message.")
                link = f"https://t.me/{c.me.username}?start={encode(f'get-{msg_id * abs(c.db_channel.id)}')}"
                slink = await self.shortlink(link)
                await m.reply(f"<b>Link:</b> {link}\n\n<b>Short Link:</b> {slink}")
            except Exception:
                pass

        @c.on_message(filters.private & admin & filters.command("batch"))
        async def batch(_, message):
            try:
                first = await c.ask(message.from_user.id, "Forward the first DB channel message or send its link.", timeout=60)
                first_id = get_message_id(c, first)
                if not first_id:
                    return await first.reply("Invalid DB channel message.")
                last = await c.ask(message.from_user.id, "Forward the last DB channel message or send its link.", timeout=60)
                last_id = get_message_id(c, last)
                if not last_id:
                    return await last.reply("Invalid DB channel message.")
                link = f"https://t.me/{c.me.username}?start={encode(f'get-{first_id * abs(c.db_channel.id)}-{last_id * abs(c.db_channel.id)}')}"
                slink = await self.shortlink(link)
                await last.reply(f"<b>Link:</b> {link}\n\n<b>Short Link:</b> {slink}")
            except Exception:
                pass

        @c.on_message(filters.private & admin & filters.command("broadcast"))
        async def broadcast(_, message):
            if not message.reply_to_message:
                return await message.reply("Reply to a message to broadcast it.")
            users = db.all_users(self.bot_id)
            self.cancel_broadcast = False
            status = await message.reply(f"Broadcasting...\n\nTotal: `{len(users)}`\nSent: `0`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="cancel_broadcast")]]))
            sent = failed = 0
            for user_id in users:
                if self.cancel_broadcast:
                    break
                try:
                    await message.reply_to_message.copy(user_id)
                    sent += 1
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                except UserIsBlocked:
                    db.remove_user(self.bot_id, user_id)
                except InputUserDeactivated:
                    db.remove_user(self.bot_id, user_id)
                except Exception:
                    failed += 1
            await status.edit(f"<b>{'Cancelled' if self.cancel_broadcast else 'Completed'}</b>\n\nTotal: `{len(users)}`\nSent: `{sent}`\nFailed: `{failed}`")

        @c.on_callback_query()
        async def callbacks(_, query):
            if query.data == "about":
                await query.message.edit_text("<b>○ Creator : @MrKrazyBot</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔒 Close", callback_data="close")]]))
            elif query.data == "close":
                await query.message.delete()
            elif query.data == "cancel_broadcast":
                if query.from_user.id not in self.values()["admins"]:
                    return await query.answer("Not authorized.", show_alert=True)
                self.cancel_broadcast = True
                await query.answer("Broadcast cancelled.")

        @c.on_message(filters.private & filters.command("stats") & admin)
        async def stats(_, message):
            users = db.users(self.bot_id)
            cfg = db.settings(self.bot_id)
            stats = db.db.command("dbStats")
            mine = db.db.command("collStats", users.name).get("size", 0) + db.db.command("collStats", cfg.name).get("size", 0)
            total = stats.get("dataSize", 0) + stats.get("indexSize", 0)
            limit = 512 * 1024 * 1024
            fmt = lambda n: f"{n / 1024:.2f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.2f} MB"
            await message.reply(f"<b>Stats</b>\n\nUsers: `{users.count_documents({})}`\nThis bot: `{fmt(mine)}`\nOther bots: `{fmt(max(0, total - mine))}`\nMongoDB: `{fmt(total)} / 512 MB`\nLeft: `{fmt(max(0, limit - total))}`\nUptime: `{readable((datetime.now() - self.uptime).total_seconds())}`")

    async def start(self):
        c = self.client
        await c.start()
        c.me = await c.get_me()
        self.uptime = datetime.now()
        s = self.values()
        if s["forcesub"]:
            try:
                chat = await c.get_chat(s["forcesub"])
                c.invitelink = chat.invite_link or await c.export_chat_invite_link(chat.id)
            except Exception as e:
                self.log.error(f"Force-sub error: {e}")
        if not s["files"]:
            self.log.warning(f"Bot @{c.me.username}: FILES is not configured")
        else:
            try:
                c.db_channel = await c.get_chat(s["files"])
            except Exception as e:
                self.log.error(f"Files channel error: {e}")
        c.set_parse_mode(ParseMode.HTML)
        self.log.info(f"Started @{c.me.username}")

    async def stop(self):
        if self.client.is_connected:
            await self.client.stop()


class Manager:
    def __init__(self):
        self.bot = Client("manager", api_id=APP_ID, api_hash=API_HASH, bot_token=BOTTOKEN, workers=WORKERS)
        self.clones = {}
        self._register()

    def _register(self):
        c = self.bot

        @c.on_message(filters.private & filters.user(OWNER) & filters.command("clone"))
        async def clone(_, message):
            if len(message.command) < 2:
                return await message.reply("Usage: /clone BOT_TOKEN")
            token = message.command[1]
            try:
                test = Client(f"test_{token[:8]}", api_id=APP_ID, api_hash=API_HASH, bot_token=token)
                await test.start()
                me = await test.get_me()
                await test.stop()
            except Exception as e:
                return await message.reply(f"Invalid token: `{e}`")
            if db.clones.find_one({"_id": me.id}):
                return await message.reply("This bot is already added.")
            db.clones.insert_one({"_id": me.id, "token": token, "username": me.username})
            worker = StoreBot(token, me.id)
            self.clones[me.id] = worker
            await worker.start()
            await message.reply(f"Started @{me.username}")

        @c.on_message(filters.private & filters.user(OWNER) & filters.command("mybots"))
        async def mybots(_, message):
            docs = list(db.clones.find())
            if not docs:
                return await message.reply("No clones.")
            text = "<b>Clones</b>\n\n" + "\n".join(f"{i}. @{x.get('username', 'unknown')} — {'🟢' if x['_id'] in self.clones else '🔴'}" for i, x in enumerate(docs, 1))
            await message.reply(text)

        @c.on_message(filters.private & filters.user(OWNER) & filters.command("deletebot"))
        async def deletebot(_, message):
            if len(message.command) < 2:
                return await message.reply("Usage: /deletebot BOT_ID")
            try:
                bot_id = int(message.command[1])
            except ValueError:
                return await message.reply("Invalid bot ID.")
            worker = self.clones.pop(bot_id, None)
            if worker:
                await worker.stop()
            db.clones.delete_one({"_id": bot_id})
            await message.reply("Clone deleted.")

    async def start(self):
        await self.bot.start()
        self.bot.me = await self.bot.get_me()
        for doc in db.clones.find():
            try:
                worker = StoreBot(doc["token"], doc["_id"])
                await worker.start()
                self.clones[doc["_id"]] = worker
            except Exception as e:
                LOGGER("manager").error(f"Failed to start clone {doc.get('_id')}: {e}")
        app = web.Application()
        app.router.add_get("/", lambda request: web.json_response("OK"))
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()

    async def stop(self):
        for worker in self.clones.values():
            await worker.stop()
        if self.bot.is_connected:
            await self.bot.stop()
