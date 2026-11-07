import asyncio
import base64
import logging
import os
import re
import time
from html import escape
from urllib.parse import quote

import aiohttp
from aiohttp import web
from pymongo import MongoClient
from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.errors import FloodWait, InputUserDeactivated, UserIsBlocked, UserNotParticipant
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb

# ════════════════════════════ config ════════════════════════════
env = os.getenv
BOTTOKEN = env("BOTTOKEN", "8191103559:AAG-6navNOYM0pSAZqDYzzwsLBSXS-EsRHI")
MONGODB = env("MONGODB", "mongodb+srv://herostore:herostore@herostore.ywtvule.mongodb.net/?appName=herostore")
DBNAME = env("DBNAME", "manager")
APP_ID = int(env("APP_ID", "1736204"))
API_HASH = env("API_HASH", "890d40e0f91a4de32dec2965444b2cbe")
OWNER = int(env("OWNER", "1058015838"))
FILES = int(env("FILES", "-1001205507869"))
FORCESUB = int(env("FORCESUB", "0"))
SHORTSITE = env("SHORTSITE", "nowshort.com")
SHORTAPI = env("SHORTAPI", "c576b9cdd34ceb572a8df1f57eabf7c11efbda3a")
ADMINS = [int(x) for x in env("ADMINS", "5051689666 1058015838 6012123382").replace(",", " ").split()]
WORKERS = int(env("WORKERS", "4"))
PORT = int(env("PORT", "8082"))
MONGO_LIMIT_MB = int(env("MONGO_LIMIT_MB", "512"))
ADMIN_USERNAME = "@MrKrazyBot"

START_MESSAGE = """<b>🌟 Hello {first},

I'm A File Store Bot 🤖 designed to provide files 📁 and links 🔗</b>"""
FORCE_SUB_MESSAGE = """<b>🌟 Hello {first},

You need to join our channel to use this bot.</b>"""

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logging.getLogger("pyrogram").setLevel(logging.WARNING)
LOGGER = logging.getLogger("clone")

run = asyncio.to_thread
DEFAULTS = {"files": FILES, "forcesub": FORCESUB, "shortsite": SHORTSITE, "shortapi": SHORTAPI, "admins": ADMINS}
TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")


# ═════════════════════════ database ═════════════════════════
# Manager data lives in DBNAME.clones. Every clone gets its own database named after
# the bot username, holding its `users` and `config` (settings + info) collections.
mongo = MongoClient(MONGODB, serverSelectionTimeoutMS=10000)
clones = mongo[DBNAME]["clones"]


def cid(username):
    return str(username).lstrip("@").strip().lower()


def db(bot_id):
    item = clones.find_one({"_id": bot_id}, {"database": 1}) or {}
    return mongo[item.get("database") or bot_id]


def settings(bot_id):
    cfg = {**DEFAULTS, **(db(bot_id)["config"].find_one({"_id": "settings"}) or {})}
    cfg["files"], cfg["forcesub"] = int(cfg["files"]), int(cfg["forcesub"])
    cfg["admins"] = [int(x) for x in cfg["admins"]]
    return cfg


def put(bot_id, **values):
    db(bot_id)["config"].update_one({"_id": "settings"}, {"$set": values}, upsert=True)


def check_db(name):
    """Make sure an existing database can be adopted by a clone. Returns its user count."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,38}", name):
        raise ValueError("Invalid database name.")
    if name in (DBNAME, "admin", "local", "config"):
        raise ValueError("That database cannot be used.")
    count = mongo[name]["users"].estimated_document_count()
    if not count:
        raise ValueError(f"Database '{name}' has no users - check the name.")
    return count


def save_clone(bot_id, token, username, name, database=None):
    old = clones.find_one({"_id": bot_id}) or {}
    database = database or old.get("database") or (bot_id if bot_id != DBNAME else f"clone_{bot_id}")
    cfg = mongo[database]["config"]
    cur = cfg.find_one({"_id": "settings"}) or {}
    cur.pop("_id", None)
    legacy = cfg.find_one({"_id": "shortener_config"}) or {}  # old sarah-jisoo databases
    if legacy.get("shortener") and legacy.get("shortener_api"):
        cur.setdefault("shortsite", legacy["shortener"])
        cur.setdefault("shortapi", legacy["shortener_api"])
    for key, value in DEFAULTS.items():
        cur.setdefault(key, value)
    cfg.replace_one({"_id": "settings"}, cur, upsert=True)
    clones.replace_one({"_id": bot_id}, {"_id": bot_id, "token": token, "username": username,
                                         "display_name": name, "database": database}, upsert=True)
    set_info(bot_id, username, name)


def set_info(bot_id, username, name):
    """Keep the bot's name visible in Atlas and in the manager."""
    clones.update_one({"_id": bot_id}, {"$set": {"username": username, "display_name": name}})
    db(bot_id)["config"].update_one({"_id": "info"}, {"$set": {"username": username, "name": name}}, upsert=True)


def set_database(bot_id, name):
    item = clones.find_one({"_id": bot_id})
    save_clone(bot_id, item["token"], item["username"], item["display_name"], name)


def get_clone(bot_id):
    return clones.find_one({"_id": bot_id})


def get_clones():
    return list(clones.find())


def delete_clone(bot_id):
    item = clones.find_one({"_id": bot_id}, {"database": 1})
    clones.delete_one({"_id": bot_id})
    mongo.drop_database((item or {}).get("database") or bot_id)


def add_user(bot_id, uid):
    db(bot_id)["users"].update_one({"_id": uid}, {"$setOnInsert": {"_id": uid}}, upsert=True)


def del_user(bot_id, uid):
    db(bot_id)["users"].delete_one({"_id": uid})


def all_users(bot_id):
    return [u["_id"] for u in db(bot_id)["users"].find({}, {"_id": 1}, batch_size=500)]


def user_count(bot_id):
    return db(bot_id)["users"].count_documents({})


def storage(database):
    total = 0
    for name in database.list_collection_names():
        try:
            s = database.command("collStats", name)
            total += s.get("storageSize", 0) + s.get("totalIndexSize", 0)
        except Exception:
            LOGGER.exception("collStats failed for %s", name)
    return total


def bot_storage(bot_id):
    return storage(db(bot_id))


def cluster_storage():
    names = [mongo[DBNAME]] + [mongo[c["database"]] for c in clones.find({}, {"database": 1}) if c.get("database")]
    return sum(storage(d) for d in names)


# ═════════════════════════ bot wrapper ═════════════════════════
class Bot:
    """A Pyrogram client plus start/stop/restart. `store` bots also load their settings."""

    def __init__(self, session, token, bot_id, register=None, store=True):
        self.args = dict(name=session, api_id=APP_ID, api_hash=API_HASH, bot_token=token, workers=WORKERS)
        self.client = Client(**self.args)
        self.bot_id, self.register, self.store = bot_id, register, store
        self.username = None
        self.name = self.title = bot_id

    async def load(self):
        """(Re)read this bot's settings and resolve its channels."""
        c = self.client
        c.cfg = cfg = await run(settings, self.bot_id)
        if not cfg["files"]:
            raise RuntimeError("FILES is not configured")
        c.db_channel = await c.get_chat(cfg["files"])
        c.invitelink = None
        if cfg["forcesub"]:
            chat = await c.get_chat(cfg["forcesub"])
            c.invitelink = chat.invite_link or await c.export_chat_invite_link(cfg["forcesub"])

    async def start(self):
        c = self.client
        if c.is_connected:
            return
        await c.start()
        try:
            me = await c.get_me()
            self.username = me.username or me.first_name
            self.name = " ".join(filter(None, [me.first_name, me.last_name])) or self.username
            self.title = f"{escape(self.name)} (@{me.username})" if me.username else escape(self.name)
            c.username, c.bot_id = self.username, self.bot_id
            c.set_parse_mode(ParseMode.HTML)
            if self.store:
                await self.load()
            # Pyrogram drops handlers on stop(), so they are re-added after every (re)start.
            if self.register and not c.dispatcher.groups:
                self.register(c)
            LOGGER.info("Started @%s (%s)", self.username, self.bot_id)
        except Exception:
            await self.stop()
            raise

    async def stop(self):
        try:
            session = getattr(self.client, "http_session", None)
            if session and not session.closed:
                await session.close()
            if self.client.is_connected:
                await self.client.stop()
        except Exception:
            LOGGER.exception("Failed to stop bot %s", self.bot_id)

    async def restart(self):
        await self.stop()
        self.client = Client(**self.args)  # reusing a stopped client can come back connected but silent
        await self.start()


# ═════════════════════════ clone bot: helpers ═════════════════════════
TASKS, DELIVERING, BROADCASTS, STATES = set(), set(), {}, {}
CANCEL = Kb([[Btn("❌ Cancel", callback_data="cancel_broadcast")]])


def encode(value):
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def decode(value):
    value = value.strip()
    return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode()).decode()


def is_admin(client, uid):
    return uid in client.cfg["admins"]


def spawn(coro):
    """Run long work (deliveries, broadcasts) in the background so the bot stays responsive."""
    task = asyncio.create_task(coro)
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)


def share_url(link):
    return f"https://telegram.me/share/url?url={quote(link, safe='')}"


def link_id(raw, channel):
    n = int(raw)
    if n <= 0 or n % channel:
        raise ValueError("link was not generated for this files channel")
    return n // channel


async def copy_to(msg, chat, **kw):
    try:
        return await msg.copy(chat, **kw)
    except FloodWait as e:
        await asyncio.sleep(e.value + 1)
        return await msg.copy(chat, **kw)


async def get_messages(client, ids):
    try:
        return await client.get_messages(client.db_channel.id, list(ids))
    except FloodWait as e:
        await asyncio.sleep(e.value + 1)
        return await client.get_messages(client.db_channel.id, list(ids))


def get_message_id(client, message):
    """Message ID in the files channel from a forward or a t.me link, else 0."""
    chat = message.forward_from_chat
    if chat:
        return message.forward_from_message_id if chat.id == client.db_channel.id else 0
    text = (message.text or "").strip()
    match = None if message.forward_sender_name else re.fullmatch(r"https?://(?:t\.me|telegram\.me)/(?:c/)?([^/]+)/(\d+)", text)
    if not match:
        return 0
    channel, mid = match.groups()
    if channel.isdigit():
        return int(mid) if int(f"-100{channel}") == client.db_channel.id else 0
    return int(mid) if channel.lower() == (client.db_channel.username or "").lower() else 0


async def subscribed(client, uid):
    forcesub = client.cfg["forcesub"]
    if not forcesub or is_admin(client, uid):
        return True
    try:
        member = await client.get_chat_member(forcesub, uid)
        return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER)
    except UserNotParticipant:
        return False
    except Exception:
        LOGGER.exception("ForceSub check failed for user %s on bot %s", uid, client.bot_id)
        return False


async def get_shortlink(client, link):
    site = re.sub(r"^https?://", "", str(client.cfg["shortsite"] or "").strip()).rstrip("/")
    api = str(client.cfg["shortapi"] or "").strip()
    link = link.replace("http://", "https://", 1)
    if not site or not api:
        return link
    shareus = site == "api.shareus.in"
    url = f"https://{site}/shortLink" if shareus else f"https://{site}/api"
    params = {"token": api, "format": "json", "link": link} if shareus else {"api": api, "url": link}
    try:
        session = getattr(client, "http_session", None)
        if session is None or session.closed:
            session = client.http_session = aiohttp.ClientSession()
        async with session.get(url, params=params, raise_for_status=True) as response:
            data = await response.json(content_type=None)
        if data.get("status") == "success":
            return data.get("shortlink" if shareus else "shortenedUrl", link)
    except Exception:
        LOGGER.exception("Shortener request failed for bot %s", client.bot_id)
    return link


# ═════════════════════════ clone bot: handlers ═════════════════════════
async def start(client, message):
    user = message.from_user
    if not user:
        return
    if not await subscribed(client, user.id):
        buttons = [[Btn("Join Channel", url=client.invitelink)]]
        if len(message.command) > 1:
            buttons.append([Btn("Try Again", url=f"https://t.me/{client.username}?start={message.command[1]}")])
        return await message.reply(FORCE_SUB_MESSAGE.format(first=user.first_name or "User"), reply_markup=Kb(buttons))

    try:  # a database problem must never stop people from getting their files
        await run(add_user, client.bot_id, user.id)
    except Exception:
        LOGGER.exception("Could not save user %s on bot %s", user.id, client.bot_id)

    if len(message.command) < 2:
        admin = Btn("👨‍💻 Admin", url=f"https://t.me/{ADMIN_USERNAME.lstrip('@')}")
        return await message.reply_text(START_MESSAGE.format(first=user.first_name or "User"), reply_markup=Kb([[admin]]))

    try:
        parts = decode(message.command[1]).split("-")
        if len(parts) not in (2, 3):
            raise ValueError("unexpected link format")
        channel = abs(client.db_channel.id)
        a, b = link_id(parts[1], channel), link_id(parts[-1], channel)
        ids = range(a, b + 1) if a <= b else range(a, b - 1, -1)
        if len(ids) > 100:
            return await message.reply("This link contains too many files.")
    except Exception as e:
        LOGGER.warning("Invalid start link for bot %s: %s", client.bot_id, e)
        return await message.reply("Invalid link.")

    key = (client.bot_id, user.id)
    if key in DELIVERING:
        return await message.reply("Your previous files are still being sent. Please wait a moment.")
    try:
        messages = await get_messages(client, ids)
    except Exception:
        LOGGER.exception("Could not fetch files for bot %s", client.bot_id)
        return await message.reply("Could not fetch the files right now. Please try again later.")
    DELIVERING.add(key)
    spawn(send_files(client.bot_id, user.id, messages))


async def send_files(bot_id, uid, messages):
    try:
        for msg in messages:
            if not msg or msg.empty:
                continue
            try:
                await copy_to(msg, uid, caption=msg.caption.html if msg.caption else None)
            except (UserIsBlocked, InputUserDeactivated):
                return
            except Exception:
                LOGGER.exception("File send error for bot %s", bot_id)
            await asyncio.sleep(0.5)
    finally:
        DELIVERING.discard((bot_id, uid))


async def broadcast(client, message):
    if not message.from_user or not is_admin(client, message.from_user.id):
        return
    source = message.reply_to_message
    if not source:
        return await message.reply("Reply to a message.")
    if client.bot_id in BROADCASTS:
        return await message.reply("A broadcast is already running.")
    state = BROADCASTS[client.bot_id] = {"cancel": False}  # claim the lock first
    try:
        users = await run(all_users, client.bot_id)
        status = await message.reply(
            f"<b>Broadcasting...</b>\n\nTotal: <code>{len(users)}</code>\nSent: <code>0</code>", reply_markup=CANCEL)
    except Exception:
        BROADCASTS.pop(client.bot_id, None)
        raise
    spawn(run_broadcast(client.bot_id, source, status, users, state))


async def run_broadcast(bot_id, source, status, users, state):
    n = dict.fromkeys(("sent", "blocked", "deleted", "failed"), 0)

    def report(title):
        left = len(users) - sum(n.values())
        return (f"<b>{title}</b>\n\nTotal: <code>{len(users)}</code>\nSent: <code>{n['sent']}</code>\n"
                f"Blocked: <code>{n['blocked']}</code>\nDeleted: <code>{n['deleted']}</code>\n"
                f"Failed: <code>{n['failed']}</code>\nRemaining: <code>{left}</code>")

    try:
        for i, uid in enumerate(users, 1):
            if state["cancel"]:
                break
            try:
                await copy_to(source, uid)
                n["sent"] += 1
            except UserIsBlocked:
                await run(del_user, bot_id, uid)
                n["blocked"] += 1
            except InputUserDeactivated:
                await run(del_user, bot_id, uid)
                n["deleted"] += 1
            except Exception:
                LOGGER.exception("Broadcast failed for user %s on bot %s", uid, bot_id)
                n["failed"] += 1
            if i % 25 == 0:
                try:
                    await status.edit(report("Broadcasting..."), reply_markup=CANCEL)
                except Exception:
                    pass
        await status.edit(report("Broadcast Cancelled" if state["cancel"] else "Broadcast Completed"))
    except Exception:
        LOGGER.exception("Broadcast crashed on bot %s", bot_id)
    finally:
        BROADCASTS.pop(bot_id, None)


async def stats(client, message):
    if not message.from_user or not is_admin(client, message.from_user.id):
        return
    limit = MONGO_LIMIT_MB * 1024 * 1024
    mine, total, users = await asyncio.gather(
        run(bot_storage, client.bot_id), run(cluster_storage), run(user_count, client.bot_id))

    def fmt(n):
        return f"{n / 1048576:.2f} MB" if n >= 1048576 else f"{n / 1024:.2f} KB"

    await message.reply(
        f"<b>Bot Stats</b>\n\nUsers: <code>{users}</code>\n\n"
        f"This bot: <code>{fmt(mine)}</code>\nOther bots: <code>{fmt(max(0, total - mine))}</code>\n"
        f"MongoDB: <code>{fmt(total)} / {MONGO_LIMIT_MB} MB</code>\nLeft: <code>{fmt(max(0, limit - total))}</code>")


async def shortener_show(client, message):
    if not message.from_user or not is_admin(client, message.from_user.id):
        return
    cfg = client.cfg
    await message.reply(
        f"<b>Current Shortener</b>\n\nSite: <code>{cfg['shortsite'] or 'Not set'}</code>\n"
        f"API: <code>{cfg['shortapi'] or 'Not set'}</code>\n\nChange it with <code>/shortlink site.com API</code>")


async def shortlink(client, message):
    if not message.from_user or not is_admin(client, message.from_user.id):
        return
    args = message.text.split()
    if len(args) == 2 and args[1].lower() == "off":
        site = api = ""
    elif len(args) == 3:
        site, api = re.sub(r"^https?://", "", args[1]).rstrip("/"), args[2]
    else:
        return await message.reply("Usage: <code>/shortlink site.com API</code>\nTo disable: <code>/shortlink off</code>")
    await run(put, client.bot_id, shortsite=site, shortapi=api)
    client.cfg.update(shortsite=site, shortapi=api)
    await message.reply(f"<b>Shortener Updated</b>\n\nSite: <code>{site or 'Off'}</code>\nAPI: <code>{api or 'Off'}</code>")


async def link_cmd(client, message):
    """/genlink (one file) and /batch (first + last file)."""
    if not message.from_user or not is_admin(client, message.from_user.id):
        return
    batch = "batch" in message.command[0].lower()
    STATES[(client.bot_id, message.from_user.id)] = {"batch": batch, "first": None}
    await message.reply("Forward first DB message or send its link." if batch else "Forward DB message or send its link.")


async def link_input(client, message, state):
    key = (client.bot_id, message.from_user.id)
    mid = get_message_id(client, message)
    if not mid:
        return await message.reply("Invalid DB message. Forward a message from the FILES channel or send its link.")
    if state["batch"] and state["first"] is None:
        state["first"] = mid
        return await message.reply("Forward last DB message or send its link.")
    STATES.pop(key, None)
    ids = sorted((state["first"], mid)) if state["batch"] else [mid]
    channel = abs(client.db_channel.id)
    raw = "get-" + "-".join(str(i * channel) for i in ids)
    link = f"https://telegram.me/{client.username}?start={encode(raw)}"
    slink = await get_shortlink(client, link)
    await message.reply(
        f"<b>Link:</b> {link}\n\n<b>Slink:</b> {slink}",
        reply_markup=Kb([[Btn("Share Link", url=share_url(link)), Btn("Share Slink", url=share_url(slink))]]))


async def text_in(client, message):
    """Admin text: continue a /genlink or /batch flow, otherwise shorten the first link in it."""
    if not message.from_user or not is_admin(client, message.from_user.id):
        return
    state = STATES.get((client.bot_id, message.from_user.id))
    if state:
        return await link_input(client, message, state)
    original = message.text or message.caption or ""
    match = re.search(r"https?://\S+", original)
    if not match:
        return
    link = match.group(0).rstrip(".,!?)]}>\"'")
    link = re.sub(r"^https?://(?:t|telegram)\.me/", "https://telegram.me/", link).replace("telegram.me/+", "telegram.me/%2B")
    slink = await get_shortlink(client, link)
    await message.reply_text(
        f"<b>Original:-</b> {original}\n\n<b>Short Link:-</b> {slink}", quote=True,
        reply_markup=Kb([[Btn("Share Short Link", url=share_url(slink))]]))


async def cancel_cb(client, query):
    if not is_admin(client, query.from_user.id):
        return await query.answer("Not authorized.", show_alert=True)
    if client.bot_id in BROADCASTS:
        BROADCASTS[client.bot_id]["cancel"] = True
    await query.answer("Broadcast cancelled.")


def register(client):
    private = filters.private
    for fn, names in ((start, "start"), (broadcast, "broadcast"), (stats, "stats"), (shortener_show, "shortener"),
                      (shortlink, "shortlink"), (link_cmd, ["batch", "genlink"])):
        client.add_handler(MessageHandler(fn, filters.command(names) & private))
    client.add_handler(MessageHandler(text_in, (filters.text | filters.caption | filters.forwarded) & private))
    client.add_handler(CallbackQueryHandler(cancel_cb, filters.regex("^cancel_broadcast$")))


# ═════════════════════════ manager bot ═════════════════════════
MANAGER = None
workers = {}   # bot_id -> Bot
pending = {}   # owner id -> (bot_id, field) while waiting for a new setting value

PROMPTS = {
    "files": ("Files Channel", "Send the new <b>files channel ID</b> (like <code>-100123456789</code>). The bot must be an admin there."),
    "forcesub": ("ForceSub", "Send the <b>force-sub channel ID</b>, or <code>0</code> to turn it off. The bot must be an admin there."),
    "shortener": ("Shortener", "Send <code>site.com API</code>, or <code>off</code> to disable the shortener."),
    "admins": ("Admins", "Send the <b>admin user IDs</b> separated by spaces. They replace the current list."),
    "database": ("Database", "Send the <b>name of an existing database</b> to move this clone to (it must already contain a users collection)."),
}
KEYS = {"files": ("files",), "forcesub": ("forcesub",), "admins": ("admins",), "shortener": ("shortsite", "shortapi")}
DELETE = {
    "del": ("<b>First confirmation</b>\n\nDelete this clone and permanently erase its MongoDB database?", "⚠️ Yes, Continue", "del2"),
    "del2": ("<b>FINAL CONFIRMATION</b>\n\nThis cannot be undone. Permanently delete the clone, all users, and its MongoDB database?",
             "🛑 YES, DELETE PERMANENTLY", "del3"),
}


def label(item):
    name, user = item.get("display_name"), item.get("username")
    return f"{name} (@{user})" if name and user else name or (f"@{user}" if user else item["_id"])


def running(bot_id):
    w = workers.get(bot_id)
    return bool(w and w.client.is_connected)


def num(text):
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"'{text}' is not a number.") from None


async def notify(text):
    try:
        await MANAGER.client.send_message(OWNER, text)
    except Exception:
        LOGGER.exception("Failed to notify owner")


async def get_me(token):
    async with aiohttp.ClientSession() as s, s.get(f"https://api.telegram.org/bot{token}/getMe") as r:
        data = await r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Invalid bot token."))
    return data["result"]


async def clone_list():
    docs = await run(get_clones)
    if not docs:
        return "<b>No clones yet.</b>\n\nAdd one with <code>/clone BOT_TOKEN</code>", None
    rows = [[Btn(f"{'🟢' if running(d['_id']) else '🔴'} {label(d)}", callback_data=f"clone:{d['_id']}")] for d in docs]
    return "<b>Bot Clones</b>", Kb(rows)


async def panel(item):
    bot_id = item["_id"]
    cfg, users = await asyncio.gather(run(settings, bot_id), run(user_count, bot_id))
    text = (
        f"<b>Clone Settings</b>\n\n"
        f"Bot: <b>{escape(label(item))}</b>\n"
        f"Status: {'🟢 Running' if running(bot_id) else '🔴 Stopped'}\n"
        f"Users: <code>{users}</code>\n"
        f"Database: <code>{item.get('database', bot_id)}</code>\n"
        f"Token: <code>{item.get('token', '')}</code>\n\n"
        f"Files Channel: <code>{cfg['files']}</code>\n"
        f"ForceSub: <code>{cfg['forcesub'] or 'Off'}</code>\n"
        f"Shortener: <code>{cfg['shortsite'] or 'Off'}</code> / <code>{cfg['shortapi'] or 'Not set'}</code>\n"
        f"Admins: <code>{' '.join(map(str, cfg['admins'])) or 'None'}</code>")
    edits = [Btn(f"✏️ {title}", callback_data=f"edit:{key}:{bot_id}") for key, (title, _) in PROMPTS.items()]
    rows = [edits[i:i + 2] for i in range(0, len(edits), 2)]
    rows += [[Btn("🔄 Restart", callback_data=f"restart:{bot_id}"), Btn("🗑 Delete", callback_data=f"del:{bot_id}")],
             [Btn("⬅️ Back", callback_data="clones")]]
    return text, Kb(rows)


def apply(bot_id, field, text):
    """Validate and save one setting. Raises ValueError with a readable message on bad input."""
    text = text.strip()
    if field in ("files", "forcesub"):
        value = num(text)
        if field == "files" and not value:
            raise ValueError("The files channel cannot be 0.")
        put(bot_id, **{field: value})
    elif field == "admins":
        ids = [num(x) for x in text.replace(",", " ").split()]
        if not ids:
            raise ValueError("Send at least one user ID.")
        put(bot_id, admins=ids)
    elif field == "shortener":
        parts = ["", ""] if text.lower() == "off" else text.split()
        if len(parts) != 2:
            raise ValueError("Send: site.com API (or off)")
        put(bot_id, shortsite=re.sub(r"^https?://", "", parts[0]).rstrip("/"), shortapi=parts[1])
    else:
        check_db(text)
        set_database(bot_id, text)


async def clone_list_cmd(client, message):
    pending.pop(message.from_user.id, None)
    text, kb = await clone_list()
    await message.reply(text, reply_markup=kb)


async def clone_cmd(client, message):
    args = message.text.split()
    if len(args) not in (2, 3):
        return await message.reply(
            "Usage: <code>/clone BOT_TOKEN</code>\n\nTo adopt an existing database (keeps its users and settings):\n"
            "<code>/clone BOT_TOKEN DATABASE_NAME</code>")
    token = args[1]
    adopt = args[2] if len(args) == 3 else None
    if not TOKEN_RE.fullmatch(token):
        return await message.reply("Invalid bot token.")
    if token == BOTTOKEN:
        return await message.reply("Manager bot cannot be cloned.")
    try:
        me = await get_me(token)
        if not me.get("username"):
            raise RuntimeError("The bot must have a Telegram username.")
    except Exception as e:
        LOGGER.warning("Could not resolve bot token: %s", type(e).__name__)
        return await message.reply(f"Could not start bot.\n<code>{escape(str(e))}</code>")

    username = me["username"]
    bot_id = cid(username)  # also the clone's database name
    name = " ".join(filter(None, [me.get("first_name"), me.get("last_name")])) or username
    if bot_id in workers or await run(get_clone, bot_id):
        return await message.reply(f"@{username} is already registered.")

    # Save first, so the bot starts with its adopted settings already in place.
    worker = Bot(f"clone_{bot_id}", token, bot_id, register)
    try:
        adopted = await run(check_db, adopt) if adopt else 0
        await run(save_clone, bot_id, token, username, name, adopt)
        await worker.start()
    except Exception as e:
        LOGGER.exception("Failed to create clone %s", bot_id)
        await run(clones.delete_one, {"_id": bot_id})
        await worker.stop()
        return await message.reply(f"Could not start bot.\n<code>{escape(str(e))}</code>")

    workers[bot_id] = worker
    item = await run(get_clone, bot_id)
    text = f"✅ Clone started: <b>{escape(name)}</b> (@{username})\nDatabase: <code>{item['database']}</code>"
    if adopt:
        text += f"\nAdopted {adopted} users"
    await message.reply(text)
    await notify(f"🤖 <b>Clone Started</b>\n\n{worker.title}")


async def edit_input(client, message):
    uid = message.from_user.id
    if uid not in pending:
        return
    bot_id, field = pending[uid]
    worker = workers.get(bot_id)
    text = message.text.strip()
    old = old_db = None
    try:
        old, item = await asyncio.gather(run(settings, bot_id), run(get_clone, bot_id))
        old_db = item["database"]
        if field in ("files", "forcesub") and worker and num(text):
            await worker.client.get_chat(num(text))  # fail early if the bot can't see the channel
        await run(apply, bot_id, field, text)
        if worker:
            await worker.load()
    except Exception as e:
        if old is not None:  # roll back, so a bad value never leaves the clone half-configured
            try:
                if field == "database":
                    await run(set_database, bot_id, old_db)
                else:
                    await run(put, bot_id, **{k: old[k] for k in KEYS[field]})
                if worker:
                    await worker.load()
            except Exception:
                LOGGER.exception("Rollback failed for %s", bot_id)
        return await message.reply(
            f"❌ {escape(str(e))}\n\nSend a new value, or cancel.",
            reply_markup=Kb([[Btn("Cancel", callback_data=f"clone:{bot_id}")]]))
    pending.pop(uid, None)
    text, kb = await panel(await run(get_clone, bot_id))
    await message.reply(f"✅ {PROMPTS[field][0]} updated.\n\n{text}", reply_markup=kb)


async def manager_callbacks(client, query):
    uid = query.from_user.id
    action, _, rest = (query.data or "").partition(":")
    field, _, bot_id = rest.rpartition(":")
    pending.pop(uid, None)
    edit = query.message.edit_text

    try:
        if action == "clones":
            text, kb = await clone_list()
            await edit(text, reply_markup=kb)
            return await query.answer()

        item = await run(get_clone, bot_id)
        if not item:
            return await query.answer("Clone not found.", show_alert=True)
        cancel = Kb([[Btn("Cancel", callback_data=f"clone:{bot_id}")]])

        if action == "clone":
            text, kb = await panel(item)
            await edit(text, reply_markup=kb)
        elif action == "edit":
            pending[uid] = (bot_id, field)
            title, prompt = PROMPTS[field]
            await edit(f"<b>{escape(label(item))}</b> — {title}\n\n{prompt}", reply_markup=cancel)
        elif action == "restart":
            if not workers.get(bot_id):
                return await query.answer("Clone is not running.", show_alert=True)
            await workers[bot_id].restart()
            return await query.answer("Clone restarted.")
        elif action in DELETE:
            text, button, nxt = DELETE[action]
            await edit(text, reply_markup=Kb([[Btn(button, callback_data=f"{nxt}:{bot_id}")],
                                              [Btn("Cancel", callback_data=f"clone:{bot_id}")]]))
        elif action == "del3":
            worker = workers.pop(bot_id, None)
            if worker:
                await worker.stop()
            await run(delete_clone, bot_id)
            await edit("<b>Clone deleted.</b>\n\nIts bot entry and MongoDB database were permanently removed.",
                       reply_markup=Kb([[Btn("⬅️ Bot Clones", callback_data="clones")]]))
            return await query.answer("Deleted")
        await query.answer()
    except Exception:
        LOGGER.exception("Manager callback failed: %s", query.data)
        try:
            await query.answer("An error occurred.", show_alert=True)
        except Exception:
            pass


def register_manager(client):
    owner = filters.user(OWNER) & filters.private
    client.add_handler(MessageHandler(clone_list_cmd, filters.command(["start", "mybots"]) & owner))
    client.add_handler(MessageHandler(clone_cmd, filters.command("clone") & owner))
    client.add_handler(MessageHandler(edit_input, filters.text & ~filters.regex(r"^/") & owner))
    client.add_handler(CallbackQueryHandler(manager_callbacks, filters.user(OWNER)))


# ═════════════════════════ health + watchdog + main ═════════════════════════
WATCHDOG_INTERVAL = 60   # seconds between health checks
LIVENESS_TIMEOUT = 30    # seconds to wait for get_me()
MAX_ALERTS = 3           # stop alerting after this many failed restarts in a row
MAX_BACKOFF = 3600       # never wait more than 1 hour between restart attempts


async def health(request):
    ok = MANAGER.client.is_connected
    return web.json_response(
        {"status": "ok" if ok else "unhealthy", "manager": ok,
         "clones_running": sum(running(b) for b in workers), "clones_total": len(workers)},
        status=200 if ok else 503)


async def alive(client):
    """True only if the client is connected AND answers a real API call."""
    if not client.is_connected:
        return False
    try:
        await asyncio.wait_for(client.get_me(), LIVENESS_TIMEOUT)
        return True
    except Exception:
        return False


async def watchdog():
    state = {}  # key -> [failed restarts, earliest next attempt]
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL)
        for key, bot in [("manager", MANAGER), *workers.items()]:
            if await alive(bot.client):
                state[key] = [0, 0.0]
                continue
            fails, next_try = state.setdefault(key, [0, 0.0])
            if time.monotonic() < next_try:
                continue
            LOGGER.warning("%s is not responding; restarting (failed attempts so far: %s)", key, fails)
            try:
                await bot.restart()
                state[key] = [0, 0.0]
                await notify(f"🔄 <b>Auto-Restarted</b>\n\n{bot.title}")
            except Exception as e:
                fails += 1
                wait = min(WATCHDOG_INTERVAL * 2 ** fails, MAX_BACKOFF)
                state[key] = [fails, time.monotonic() + wait]
                LOGGER.exception("%s restart failed", key)
                if fails <= MAX_ALERTS:
                    await notify(f"❌ <b>Restart Failed</b>\n\n{bot.title}\nAttempt: {fails}\n"
                                 f"Next try in: {wait}s\nError: <code>{escape(str(e))}</code>")


async def main():
    global MANAGER
    MANAGER = Bot("manager", BOTTOKEN, "manager", register_manager, store=False)
    await MANAGER.start()
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    watch = None
    try:
        await notify(f"🟢 <b>Manager Online</b>\n\n{MANAGER.title}")
        # Health endpoint first, so the host sees the app as alive while clones boot.
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()

        for item in await run(get_clones):
            bot_id = item["_id"]
            # Registered before starting, so a clone that fails at boot is retried by the watchdog.
            worker = workers[bot_id] = Bot(f"clone_{bot_id}", item["token"], bot_id, register)
            try:
                await worker.start()
                await run(set_info, bot_id, worker.username, worker.name)  # keep stored names fresh
                await notify(f"🟢 <b>Clone Online</b>\n\n{worker.title}")
            except Exception as e:
                LOGGER.exception("Clone %s failed to start", bot_id)
                await notify(f"❌ <b>Clone Failed To Start</b>\n\n{escape(label(item))}\n"
                             f"Error: <code>{escape(str(e))}</code>\n\nIt will be retried automatically.")

        watch = asyncio.create_task(watchdog())
        await asyncio.Event().wait()
    finally:
        if watch:
            watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)
        for worker in list(workers.values()):
            await worker.stop()
        await MANAGER.stop()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
