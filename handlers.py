import asyncio
import re
import aiohttp
import logging
from urllib.parse import quote
from pyrogram import filters
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated

from config import START_MESSAGE, FORCE_SUB_MESSAGE, SHORTSITE, SHORTAPI, ADMIN_USERNAME, MONGO_LIMIT_MB
from helper_func import encode, decode, get_messages, get_message_id, is_admin, is_subscribed
from database import (
    present_user, add_user, delete_user, all_users, user_count,
    collection_storage, cluster_storage, set_shortener, shortener
)

LOGGER = logging.getLogger(__name__)


def register(client):
    client.add_handler(MessageHandler(start, filters.command("start") & filters.private))
    client.add_handler(MessageHandler(broadcast, filters.command("broadcast") & filters.private))
    client.add_handler(MessageHandler(stats, filters.command("stats") & filters.private))
    client.add_handler(MessageHandler(shortener_cmd, filters.command(["shortener", "shortlink"]) & filters.private))
    client.add_handler(MessageHandler(batch, filters.command("batch") & filters.private))
    client.add_handler(MessageHandler(genlink, filters.command("genlink") & filters.private))
    client.add_handler(MessageHandler(auto_shortener, (filters.text | filters.caption | filters.forwarded) & filters.private))
    client.add_handler(CallbackQueryHandler(callbacks))


def spawn(client, coro):
    """Run long work (file delivery, broadcasts) in the background.

    Pyrogram only runs a few handlers at a time per bot; a handler that sleeps
    for minutes would otherwise block every other user of that bot.
    """
    tasks = getattr(client, "bg_tasks", None)
    if tasks is None:
        tasks = client.bg_tasks = set()
    task = asyncio.create_task(coro)
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task


def delivering(client):
    """User IDs that currently have files being delivered by this bot."""
    users = getattr(client, "delivering", None)
    if users is None:
        users = client.delivering = set()
    return users


def share_url(link):
    return f"https://telegram.me/share/url?url={quote(link, safe='')}"


def link_id(raw, channel):
    """Decode one message ID from a link, rejecting payloads not made for this channel."""
    number = int(raw)
    if number <= 0 or number % channel:
        raise ValueError("link was not generated for this files channel")
    return number // channel


async def send_files(client, user_id, messages):
    try:
        for msg in messages:
            if not msg or msg.empty:
                continue
            while True:
                try:
                    await msg.copy(
                        user_id,
                        caption=msg.caption.html if msg.caption else None
                    )
                    await asyncio.sleep(0.5)
                    break
                except FloodWait as e:
                    LOGGER.warning("FloodWait of %s seconds for user %s on bot %s", e.value, user_id, client.bot_id)
                    await asyncio.sleep(e.value + 1)
                except (UserIsBlocked, InputUserDeactivated):
                    LOGGER.info("User %s is unreachable on bot %s; stopping delivery", user_id, client.bot_id)
                    return
                except Exception:
                    LOGGER.exception("File send error for bot %s", client.bot_id)
                    break
    finally:
        delivering(client).discard(user_id)


async def start(client, message):
    if not message.from_user:
        return

    user_id = message.from_user.id
    first_name = message.from_user.first_name or "User"

    if not await is_subscribed(None, client, message):
        return await not_joined(client, message)

    # A database problem must never stop people from getting their files.
    try:
        if not await asyncio.to_thread(present_user, client.bot_id, user_id):
            LOGGER.info("Adding user %s to bot %s", user_id, client.bot_id)
            await asyncio.to_thread(add_user, client.bot_id, user_id)
    except Exception:
        LOGGER.exception("Could not save user %s on bot %s", user_id, client.bot_id)

    if len(message.command) < 2:
        admin_url = f"https://t.me/{ADMIN_USERNAME.lstrip('@')}"
        return await message.reply_text(
            START_MESSAGE.format(first=first_name),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("👨‍💻 Admin", url=admin_url)
            ]])
        )

    try:
        value = await decode(message.command[1])
        args = value.split("-")
        channel = abs(client.db_channel.id)

        if len(args) == 2:
            ids = [link_id(args[1], channel)]
        elif len(args) == 3:
            a = link_id(args[1], channel)
            b = link_id(args[2], channel)
            ids = range(a, b + 1) if a <= b else range(a, b - 1, -1)
        else:
            raise ValueError("unexpected link format")

        if len(ids) > 100:
            return await message.reply("This link contains too many files.")
    except Exception as e:
        LOGGER.warning("Invalid start link for bot %s: %s", client.bot_id, e)
        return await message.reply("Invalid link.")

    if user_id in delivering(client):
        return await message.reply("Your previous files are still being sent. Please wait a moment.")

    try:
        messages = await get_messages(client, ids)
    except Exception:
        LOGGER.exception("Could not fetch files for bot %s", client.bot_id)
        return await message.reply("Could not fetch the files right now. Please try again later.")

    delivering(client).add(user_id)
    spawn(client, send_files(client, user_id, messages))


async def not_joined(client, message):
    first_name = message.from_user.first_name if message.from_user else "User"
    if not client.forcesub:
        return await message.reply_text(
            START_MESSAGE.format(first=first_name)
        )

    buttons = [[
        InlineKeyboardButton("Join Channel", url=client.invitelink)
    ]]

    if len(message.command) > 1:
        buttons.append([InlineKeyboardButton(
            "Try Again",
            url=f"https://t.me/{client.username}?start={message.command[1]}"
        )])

    await message.reply(
        FORCE_SUB_MESSAGE.format(first=first_name),
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def broadcast(client, message):
    if not message.from_user or not is_admin(client, message.from_user.id):
        return

    if not message.reply_to_message:
        return await message.reply("Reply to a message.")

    if getattr(client, "broadcast_running", False):
        return await message.reply("A broadcast is already running.")

    # Claim the lock first so two /broadcast commands can't both start.
    client.broadcast_running = True
    client.broadcast_cancelled = False

    cancel = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast")
    ]])

    try:
        total = await asyncio.to_thread(user_count, client.bot_id)
        users = await asyncio.to_thread(all_users, client.bot_id)
        status = await message.reply(
            f"<b>Broadcasting...</b>\n\nTotal: <code>{total}</code>\nSent: <code>0</code>",
            reply_markup=cancel
        )
    except Exception:
        client.broadcast_running = False
        raise

    # Runs in the background so the bot stays responsive (and Cancel keeps working).
    spawn(client, run_broadcast(client, message.reply_to_message, status, users, total, cancel))


async def run_broadcast(client, source, status, users, total, cancel):
    sent = failed = blocked = deleted = 0

    try:
        for user in users:
            user_id = user["_id"]
            if client.broadcast_cancelled:
                break

            try:
                await source.copy(user_id)
                sent += 1
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
                if client.broadcast_cancelled:
                    break
                try:
                    await source.copy(user_id)
                    sent += 1
                except Exception:
                    LOGGER.exception("Broadcast retry failed for user %s on bot %s", user_id, client.bot_id)
                    failed += 1
            except UserIsBlocked:
                await asyncio.to_thread(delete_user, client.bot_id, user_id)
                blocked += 1
            except InputUserDeactivated:
                await asyncio.to_thread(delete_user, client.bot_id, user_id)
                deleted += 1
            except Exception:
                LOGGER.exception("Broadcast failed for user %s on bot %s", user_id, client.bot_id)
                failed += 1

            processed = sent + failed + blocked + deleted
            if processed % 25 == 0:
                try:
                    await status.edit(
                        f"<b>Broadcasting...</b>\n\nTotal: <code>{total}</code>\nSent: <code>{sent}</code>\nFailed: <code>{failed}</code>",
                        reply_markup=cancel
                    )
                except Exception:
                    pass

        processed = sent + failed + blocked + deleted
        title = "Broadcast Cancelled" if client.broadcast_cancelled else "Broadcast Completed"

        await status.edit(
            f"<b>{title}</b>\n\n"
            f"Total: <code>{total}</code>\n"
            f"Sent: <code>{sent}</code>\n"
            f"Blocked: <code>{blocked}</code>\n"
            f"Deleted: <code>{deleted}</code>\n"
            f"Failed: <code>{failed}</code>\n"
            f"Remaining: <code>{max(0, total - processed)}</code>"
        )
    except Exception:
        LOGGER.exception("Broadcast crashed on bot %s", client.bot_id)
    finally:
        client.broadcast_running = False
        client.broadcast_cancelled = False


async def stats(client, message):
    if not message.from_user or not is_admin(client, message.from_user.id):
        return

    limit = MONGO_LIMIT_MB * 1024 * 1024
    mine = await asyncio.to_thread(collection_storage, client.bot_id)
    total = await asyncio.to_thread(cluster_storage)
    other = max(0, total - mine)
    left = max(0, limit - total)

    def size(n):
        if n >= 1024 * 1024:
            return f"{n / 1024 / 1024:.2f} MB"
        return f"{n / 1024:.2f} KB"

    await message.reply(
        f"<b>Bot Stats</b>\n\n"
        f"Users: <code>{await asyncio.to_thread(user_count, client.bot_id)}</code>\n\n"
        f"This bot: <code>{size(mine)}</code>\n"
        f"Other bots: <code>{size(other)}</code>\n"
        f"MongoDB: <code>{size(total)} / {MONGO_LIMIT_MB} MB</code>\n"
        f"Left: <code>{size(left)}</code>"
    )


async def shortener_cmd(client, message):
    if not message.from_user or not is_admin(client, message.from_user.id):
        return

    args = message.text.split(maxsplit=2)
    if len(args) == 3:
        await asyncio.to_thread(set_shortener, client.bot_id, args[1], args[2])
        return await message.reply(
            f"<b>Shortener Updated</b>\n\nSite: <code>{args[1]}</code>\nAPI: <code>{args[2]}</code>"
        )

    site, api = await asyncio.to_thread(shortener, client.bot_id)
    await message.reply(
        f"<b>Current Shortener</b>\n\nSite: <code>{site or SHORTSITE}</code>\nAPI: <code>{api or SHORTAPI}</code>"
    )


async def batch(client, message):
    if not message.from_user or not is_admin(client, message.from_user.id):
        return

    if not hasattr(client, "input_states"):
        client.input_states = {}

    client.input_states[message.from_user.id] = {
        "mode": "batch",
        "step": 1
    }
    await message.reply("Forward first DB message or send its link.")


async def genlink(client, message):
    if not message.from_user or not is_admin(client, message.from_user.id):
        return

    if not hasattr(client, "input_states"):
        client.input_states = {}

    client.input_states[message.from_user.id] = {
        "mode": "genlink"
    }
    await message.reply("Forward DB message or send its link.")


async def batch_input(client, message):
    if not message.from_user:
        return
    user_id = message.from_user.id

    if not is_admin(client, user_id):
        return

    state = getattr(client, "input_states", {}).get(user_id)
    if not state:
        return

    msg_id = get_message_id(client, message)
    if not msg_id:
        return await message.reply("Invalid DB message. Forward a message from the FILES channel or send its link.")

    if state["mode"] == "genlink":
        client.input_states.pop(user_id, None)
        raw = f"get-{msg_id * abs(client.db_channel.id)}"
        link = f"https://telegram.me/{client.username}?start={await encode(raw)}"
        slink = await get_shortlink(client, link)

        return await message.reply(
            f"<b>Link:</b> {link}\n\n<b>Slink:</b> {slink}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Share Link", url=share_url(link)),
                InlineKeyboardButton("Share Slink", url=share_url(slink))
            ]])
        )

    if state["step"] == 1:
        client.input_states[user_id] = {
            "mode": "batch",
            "step": 2,
            "first_id": msg_id
        }
        return await message.reply("Forward last DB message or send its link.")

    first_id = state["first_id"]
    client.input_states.pop(user_id, None)

    if msg_id < first_id:
        first_id, msg_id = msg_id, first_id

    raw = f"get-{first_id * abs(client.db_channel.id)}-{msg_id * abs(client.db_channel.id)}"
    link = f"https://telegram.me/{client.username}?start={await encode(raw)}"
    slink = await get_shortlink(client, link)

    await message.reply(
        f"<b>Link:</b> {link}\n\n<b>Slink:</b> {slink}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Share Link", url=share_url(link)),
            InlineKeyboardButton("Share Slink", url=share_url(slink))
        ]])
    )


async def auto_shortener(client, message):
    if not message.from_user or not is_admin(client, message.from_user.id):
        return

    if getattr(client, "input_states", {}).get(message.from_user.id):
        return await batch_input(client, message)

    if message.command:
        return

    original = message.text or message.caption
    if not original:
        return

    match = re.search(r"https?://\S+", original)
    if not match:
        return

    link = match.group(0).rstrip(".,!?)]}>\"'")
    link = (
        link.replace("https://t.me/", "https://telegram.me/")
        .replace("http://t.me/", "https://telegram.me/")
        .replace("http://telegram.me/", "https://telegram.me/")
    )
    if "telegram.me/+" in link:
        link = link.replace("telegram.me/+", "telegram.me/%2B")

    slink = await get_shortlink(client, link)

    await message.reply_text(
        f"<b>Original:-</b> {original}\n\n<b>Short Link:-</b> {slink}",
        quote=True,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Share Short Link", url=share_url(slink))
        ]])
    )


async def get_http_session(client):
    session = getattr(client, "http_session", None)
    if session is None or session.closed:
        session = aiohttp.ClientSession()
        client.http_session = session
    return session


async def get_shortlink(client, link):
    custom_site, custom_api = await asyncio.to_thread(shortener, client.bot_id)

    API = custom_api or SHORTAPI
    URL = custom_site or SHORTSITE

    API = str(API).strip()
    URL = str(URL).strip().rstrip("/")
    link = link.replace("http://", "https://", 1)

    if not API or not URL:
        return link

    if URL == "api.shareus.in":
        url = f"https://{URL}/shortLink"
        params = {"token": API, "format": "json", "link": link}
        try:
            session = await get_http_session(client)
            async with session.get(url, params=params, raise_for_status=True) as response:
                data = await response.json(content_type=None)
                if data.get("status") == "success":
                    return data.get("shortlink", link)
        except Exception:
            LOGGER.exception("Shortener request failed for bot %s", client.bot_id)
        return link

    url = f"https://{URL}/api"
    params = {"api": API, "url": link}

    try:
        session = await get_http_session(client)
        async with session.get(url, params=params, raise_for_status=True) as response:
            data = await response.json(content_type=None)
            if data.get("status") == "success":
                return data.get("shortenedUrl", link)
    except Exception:
        LOGGER.exception("Shortener request failed for bot %s", client.bot_id)

    return link


async def callbacks(client, query):
    if query.data == "cancel_broadcast":
        if not query.from_user or not is_admin(client, query.from_user.id):
            return await query.answer("Not authorized.", show_alert=True)
        client.broadcast_cancelled = True
        await query.answer("Broadcast cancelled.")
