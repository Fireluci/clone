import asyncio
import re
from html import escape

import aiohttp
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup

from clone import register
from config import API_HASH, APP_ID, BOTTOKEN, LOGGER, OWNER, WORKERS
from database import (check_database, clone_id, clones, delete_clone, get_clone, get_clones, put,
                      save_clone, set_database, settings, user_count)

run = asyncio.to_thread
MANAGER = None  # the manager's Bot, created by create_manager()
workers = {}    # clone ID -> its running Bot
pending = {}    # owner's user ID -> (clone ID, setting) while the manager waits for a new value

TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")

# Settings the owner can edit from a clone's panel: key -> (button title, prompt)
PROMPTS = {
    "files": ("Files Channel",
              "Send the new <b>files channel ID</b> (like <code>-100123456789</code>). "
              "The bot must be an admin there."),
    "forcesub": ("ForceSub",
                 "Send the <b>force-sub channel ID</b>, or <code>0</code> to turn it off. "
                 "The bot must be an admin there."),
    "shortener": ("Shortener",
                  "Send <code>site.com API</code>, or <code>off</code> to disable the shortener."),
    "admins": ("Admins",
               "Send the <b>admin user IDs</b> separated by spaces. They replace the current list."),
    "database": ("Database",
                 "Send the <b>name of an existing database</b> to move this clone to "
                 "(it must already contain a users collection)."),
}
# Which saved settings each editable field covers (used to roll back a bad edit).
KEYS = {"files": ("files",), "forcesub": ("forcesub",), "admins": ("admins",),
        "shortener": ("shortsite", "shortapi")}
# The delete confirmations: action -> (text, button label, next action)
DELETE_STEPS = {
    "del": ("<b>First confirmation</b>\n\nDelete this clone and permanently erase its MongoDB database?",
            "⚠️ Yes, Continue", "del2"),
    "del2": ("<b>FINAL CONFIRMATION</b>\n\nThis cannot be undone. Permanently delete the clone, all users, "
             "and its MongoDB database?", "🛑 YES, DELETE PERMANENTLY", "del3"),
}


# ═══════════════════════════ Bot: one running bot ═══════════════════════════
class Bot:
    """A Pyrogram client plus start / stop / restart. Clones (store=True) also load their settings."""

    def __init__(self, session, token, bot_id, register_handlers=None, store=True):
        self.args = dict(name=session, api_id=APP_ID, api_hash=API_HASH, bot_token=token, workers=WORKERS)
        self.client = Client(**self.args)
        self.bot_id = bot_id
        self.register_handlers = register_handlers
        self.store = store
        self.username = None
        self.name = bot_id   # the bot's display name
        self.title = bot_id  # "Name (@username)", safe to put in HTML messages

    async def load(self):
        """(Re)read this bot's settings and resolve its files and force-sub channels."""
        client = self.client
        client.cfg = cfg = await run(settings, self.bot_id)
        if not cfg["files"]:
            raise RuntimeError("FILES is not configured")

        client.db_channel = await client.get_chat(cfg["files"])
        client.invitelink = None
        if cfg["forcesub"]:
            chat = await client.get_chat(cfg["forcesub"])
            client.invitelink = chat.invite_link or await client.export_chat_invite_link(cfg["forcesub"])

    async def start(self):
        client = self.client
        if client.is_connected:
            return
        await client.start()
        try:
            me = await client.get_me()
            self.username = me.username or me.first_name
            self.name = " ".join(filter(None, [me.first_name, me.last_name])) or self.username
            self.title = f"{escape(self.name)} (@{me.username})" if me.username else escape(self.name)
            client.username = self.username
            client.bot_id = self.bot_id
            client.set_parse_mode(ParseMode.HTML)

            if self.store:
                await self.load()
            # Pyrogram drops handlers when a client stops, so they are added again after every (re)start.
            if self.register_handlers and not client.dispatcher.groups:
                self.register_handlers(client)
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
        # Re-using a stopped Pyrogram client is unreliable (it can come back connected but silent),
        # so every restart builds a brand-new client.
        self.client = Client(**self.args)
        await self.start()


def create_manager():
    global MANAGER
    MANAGER = Bot("manager", BOTTOKEN, "manager", register_manager, store=False)
    return MANAGER


# ═══════════════════════════ small helpers ═══════════════════════════
def label(item):
    """Plain-text name of a clone: "Name (@username)"."""
    name, username = item.get("display_name"), item.get("username")
    if name and username:
        return f"{name} (@{username})"
    return name or (f"@{username}" if username else item["_id"])


def running(bot_id):
    worker = workers.get(bot_id)
    return bool(worker and worker.client.is_connected)


def number(text):
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"'{text}' is not a number.") from None


async def notify(text):
    """Message the owner from the manager bot."""
    try:
        await MANAGER.client.send_message(OWNER, text)
    except Exception:
        LOGGER.exception("Failed to notify owner")


async def get_me(token):
    """Ask Telegram who a bot token belongs to (also proves the token works)."""
    async with aiohttp.ClientSession() as session, session.get(f"https://api.telegram.org/bot{token}/getMe") as response:
        data = await response.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Invalid bot token."))
    return data["result"]


# ═══════════════════════════ screens ═══════════════════════════
async def clone_list():
    """The list of clones as (text, buttons)."""
    docs = await run(get_clones)
    if not docs:
        return "<b>No clones yet.</b>\n\nAdd one with <code>/clone BOT_TOKEN</code>", None
    rows = []
    for doc in docs:
        status = "🟢" if running(doc["_id"]) else "🔴"
        rows.append([Button(f"{status} {label(doc)}", callback_data=f"clone:{doc['_id']}")])
    return "<b>Bot Clones</b>", Markup(rows)


async def panel(item):
    """The settings screen of one clone as (text, buttons)."""
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

    edit_buttons = [Button(f"✏️ {title}", callback_data=f"edit:{key}:{bot_id}") for key, (title, _) in PROMPTS.items()]
    rows = [edit_buttons[i:i + 2] for i in range(0, len(edit_buttons), 2)]
    rows.append([Button("🔄 Restart", callback_data=f"restart:{bot_id}"),
                 Button("🗑 Delete", callback_data=f"del:{bot_id}")])
    rows.append([Button("⬅️ Back", callback_data="clones")])
    return text, Markup(rows)


# ═══════════════════════════ commands ═══════════════════════════
async def list_command(client, message):
    """/start and /mybots"""
    pending.pop(message.from_user.id, None)
    text, buttons = await clone_list()
    await message.reply(text, reply_markup=buttons)


async def clone_command(client, message):
    """/clone BOT_TOKEN [DATABASE_NAME]"""
    args = message.text.split()
    if len(args) not in (2, 3):
        return await message.reply(
            "Usage: <code>/clone BOT_TOKEN</code>\n\n"
            "To adopt an existing database (keeps its users and settings):\n"
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
    bot_id = clone_id(username)  # also the name of the clone's database
    name = " ".join(filter(None, [me.get("first_name"), me.get("last_name")])) or username
    if bot_id in workers or await run(get_clone, bot_id):
        return await message.reply(f"@{username} is already registered.")

    # Save before starting, so the bot starts with its (possibly adopted) settings already in place.
    worker = Bot(f"clone_{bot_id}", token, bot_id, register)
    try:
        adopted = await run(check_database, adopt) if adopt else 0
        await run(save_clone, bot_id, token, username, name, adopt)
        await worker.start()
    except Exception as e:
        LOGGER.exception("Failed to create clone %s", bot_id)
        await run(clones.delete_one, {"_id": bot_id})  # leaves an adopted database untouched
        await worker.stop()
        return await message.reply(f"Could not start bot.\n<code>{escape(str(e))}</code>")

    workers[bot_id] = worker
    item = await run(get_clone, bot_id)
    text = f"✅ Clone started: <b>{escape(name)}</b> (@{username})\nDatabase: <code>{item['database']}</code>"
    if adopt:
        text += f"\nAdopted {adopted} users"
    await message.reply(text)
    await notify(f"🤖 <b>Clone Started</b>\n\n{worker.title}")


async def restart_command(client, message):
    """/restart BOT_ID"""
    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        return await message.reply("Usage: /restart BOT_ID")
    bot_id = args[1].strip().lower()
    worker = workers.get(bot_id)
    if not worker:
        return await message.reply("Clone is not running.")
    try:
        await worker.restart()
        await message.reply(f"✅ Clone <code>{bot_id}</code> restarted.")
    except Exception as e:
        LOGGER.exception("Failed to restart clone %s", bot_id)
        await message.reply(f"Restart failed.\n<code>{escape(str(e))}</code>")


async def delete_command(client, message):
    """/deletebot BOT_ID, then /deletebot BOT_ID confirm, then the buttons."""
    args = message.text.split(maxsplit=2)
    if len(args) == 2:
        return await message.reply(f"First confirmation required:\n<code>/deletebot {args[1]} confirm</code>")
    if len(args) != 3 or args[2].lower() != "confirm":
        return await message.reply("Use the exact confirmation shown above.")

    bot_id = args[1].strip().lower()
    if not await run(get_clone, bot_id):
        return await message.reply("Clone not found.")
    await message.reply(
        "<b>Second confirmation required.</b>\n\n"
        "This permanently deletes the clone and its MongoDB database.\n\nUse the button below to continue.",
        reply_markup=Markup([[Button("⚠️ Permanently Delete", callback_data=f"del2:{bot_id}")],
                             [Button("Cancel", callback_data=f"clone:{bot_id}")]]))


# ═══════════════════════════ editing settings ═══════════════════════════
def apply_setting(bot_id, field, text):
    """Validate and save one setting. Raises ValueError with a readable message on bad input."""
    text = text.strip()

    if field in ("files", "forcesub"):
        value = number(text)
        if field == "files" and not value:
            raise ValueError("The files channel cannot be 0.")
        put(bot_id, **{field: value})

    elif field == "admins":
        ids = [number(x) for x in text.replace(",", " ").split()]
        if not ids:
            raise ValueError("Send at least one user ID.")
        put(bot_id, admins=ids)

    elif field == "shortener":
        parts = ["", ""] if text.lower() == "off" else text.split()
        if len(parts) != 2:
            raise ValueError("Send: site.com API (or off)")
        put(bot_id, shortsite=re.sub(r"^https?://", "", parts[0]).rstrip("/"), shortapi=parts[1])

    else:  # database
        check_database(text)
        set_database(bot_id, text)


async def edit_input(client, message):
    """The owner's reply to an "edit" prompt: save the new value and apply it to the running clone."""
    user_id = message.from_user.id
    if user_id not in pending:
        return
    bot_id, field = pending[user_id]
    worker = workers.get(bot_id)
    text = message.text.strip()
    old_settings = old_database = None

    try:
        old_settings, item = await asyncio.gather(run(settings, bot_id), run(get_clone, bot_id))
        old_database = item["database"]
        if field in ("files", "forcesub") and worker and number(text):
            await worker.client.get_chat(number(text))  # fail early if the bot can't see the channel
        await run(apply_setting, bot_id, field, text)
        if worker:
            await worker.load()
    except Exception as e:
        if old_settings is not None:  # roll back, so a bad value never leaves the clone half-configured
            try:
                if field == "database":
                    await run(set_database, bot_id, old_database)
                else:
                    await run(put, bot_id, **{key: old_settings[key] for key in KEYS[field]})
                if worker:
                    await worker.load()
            except Exception:
                LOGGER.exception("Rollback failed for %s", bot_id)
        return await message.reply(
            f"❌ {escape(str(e))}\n\nSend a new value, or cancel.",
            reply_markup=Markup([[Button("Cancel", callback_data=f"clone:{bot_id}")]]))

    pending.pop(user_id, None)
    panel_text, buttons = await panel(await run(get_clone, bot_id))
    await message.reply(f"✅ {PROMPTS[field][0]} updated.\n\n{panel_text}", reply_markup=buttons)


# ═══════════════════════════ buttons ═══════════════════════════
async def callbacks(client, query):
    """Every button of the manager. Callback data looks like "action", "action:bot" or "edit:field:bot"."""
    user_id = query.from_user.id
    action, _, rest = (query.data or "").partition(":")
    field, _, bot_id = rest.rpartition(":")  # field is only filled for "edit:field:bot"
    pending.pop(user_id, None)
    edit = query.message.edit_text

    try:
        if action == "clones":
            text, buttons = await clone_list()
            await edit(text, reply_markup=buttons)
            return await query.answer()

        item = await run(get_clone, bot_id)
        if not item:
            return await query.answer("Clone not found.", show_alert=True)
        cancel = Markup([[Button("Cancel", callback_data=f"clone:{bot_id}")]])

        if action == "clone":
            text, buttons = await panel(item)
            await edit(text, reply_markup=buttons)

        elif action == "edit":
            pending[user_id] = (bot_id, field)
            title, prompt = PROMPTS[field]
            await edit(f"<b>{escape(label(item))}</b> — {title}\n\n{prompt}", reply_markup=cancel)

        elif action == "restart":
            if not workers.get(bot_id):
                return await query.answer("Clone is not running.", show_alert=True)
            await workers[bot_id].restart()
            return await query.answer("Clone restarted.")

        elif action in DELETE_STEPS:
            text, button, next_action = DELETE_STEPS[action]
            await edit(text, reply_markup=Markup([[Button(button, callback_data=f"{next_action}:{bot_id}")],
                                                  [Button("Cancel", callback_data=f"clone:{bot_id}")]]))

        elif action == "del3":
            worker = workers.pop(bot_id, None)
            if worker:
                await worker.stop()
            await run(delete_clone, bot_id)
            await edit("<b>Clone deleted.</b>\n\nIts bot entry and MongoDB database were permanently removed.",
                       reply_markup=Markup([[Button("⬅️ Bot Clones", callback_data="clones")]]))
            return await query.answer("Deleted")

        await query.answer()
    except Exception:
        LOGGER.exception("Manager callback failed: %s", query.data)
        try:
            await query.answer("An error occurred.", show_alert=True)
        except Exception:
            pass


def register_manager(client):
    """Attach the manager's handlers. Everything is owner-only."""
    owner = filters.user(OWNER) & filters.private
    client.add_handler(MessageHandler(list_command, filters.command(["start", "mybots"]) & owner))
    client.add_handler(MessageHandler(clone_command, filters.command("clone") & owner))
    client.add_handler(MessageHandler(restart_command, filters.command("restart") & owner))
    client.add_handler(MessageHandler(delete_command, filters.command("deletebot") & owner))
    client.add_handler(MessageHandler(edit_input, filters.text & ~filters.regex(r"^/") & owner))
    client.add_handler(CallbackQueryHandler(callbacks, filters.user(OWNER)))
