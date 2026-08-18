import asyncio
import re
import aiohttp
from pyrogram import filters
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import START_MESSAGE, FORCE_SUB_MESSAGE
from helper_func import encode, decode, get_messages, get_message_id, is_admin, is_subscribed
from database import present_user, add_user, delete_user, all_users, user_count, collection_storage, cluster_storage, set_shortener, shortener


def register(client):
    H = __import__("pyrogram").handlers.MessageHandler
    client.add_handler(H(start, filters.command("start") & filters.private))
    client.add_handler(H(broadcast, filters.command("broadcast") & filters.private))
    client.add_handler(H(stats, filters.command("stats") & filters.private))
    client.add_handler(H(shortener_cmd, filters.command(["shortener", "shortlink"]) & filters.private))
    client.add_handler(H(batch, filters.command("batch") & filters.private))
    client.add_handler(H(genlink, filters.command("genlink") & filters.private))
    client.add_handler(H(input_handler, filters.private))
    client.add_handler(H(auto_shortener, (filters.text | filters.caption) & filters.private))
    client.add_handler(__import__("pyrogram").handlers.CallbackQueryHandler(callbacks))


async def start(client, message):
    if not await is_subscribed(None, client, message):
        return await not_joined(client, message)
    if not present_user(client.bot_id, message.from_user.id):
        add_user(client.bot_id, message.from_user.id)
    if len(message.text or "") <= 7:
        return await message.reply_text(START_MESSAGE.format(first=message.from_user.first_name), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👨‍💻 Admin", url="https://t.me/MrKrazyBot")]]))
    try:
        args = (await decode(message.text.split(" ", 1)[1])).split("-")
        channel_id = abs(client.db_channel.id)
        if len(args) == 2:
            n = int(args[1])
            if n % channel_id: raise ValueError
            ids = [n // channel_id]
        elif len(args) == 3:
            x, y = int(args[1]), int(args[2])
            if x % channel_id or y % channel_id: raise ValueError
            a, b = x // channel_id, y // channel_id
            ids = range(a, b + 1) if a <= b else range(a, b - 1, -1)
        else: raise ValueError
        messages = await get_messages(client, ids)
    except Exception:
        return await message.reply("Invalid link.")
    for msg in messages:
        if not msg: continue
        try:
            await msg.copy(message.from_user.id, caption=msg.caption.html if msg.caption else None)
            await asyncio.sleep(0.5)
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception:
            pass


async def not_joined(client, message):
    if not client.forcesub:
        return await message.reply_text(START_MESSAGE.format(first=message.from_user.first_name), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👨‍💻 Admin", url="https://t.me/MrKrazyBot")]]))
    buttons = [[InlineKeyboardButton("Join Channel", url=client.invitelink)], [InlineKeyboardButton("👨‍💻 Admin", url="https://t.me/MrKrazyBot")]]
    if len(message.command) > 1:
        buttons.append([InlineKeyboardButton("Try Again", url=f"https://t.me/{client.username}?start={message.command[1]}")])
    await message.reply(FORCE_SUB_MESSAGE.format(first=message.from_user.first_name), reply_markup=InlineKeyboardMarkup(buttons), quote=True)


async def broadcast(client, message):
    if not is_admin(client, message.from_user.id): return
    if not message.reply_to_message: return await message.reply("Reply to a message.")
    if getattr(client, "broadcast_task", None): return await message.reply("A broadcast is already running.")
    users = all_users(client.bot_id)
    client.broadcast_cancelled = False
    cancel = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast")]])
    status = await message.reply("<b>Broadcasting...</b>\n\nSent: <code>0</code>", reply_markup=cancel)
    client.broadcast_task = asyncio.current_task()
    sent = failed = blocked = deleted = 0
    try:
        for row in users:
            if client.broadcast_cancelled: break
            user_id = row["_id"]
            try:
                await message.reply_to_message.copy(user_id); sent += 1
            except FloodWait as e:
                remaining = e.value
                while remaining > 0 and not client.broadcast_cancelled:
                    step = min(remaining, 2); await asyncio.sleep(step); remaining -= step
                if client.broadcast_cancelled: break
                try:
                    await message.reply_to_message.copy(user_id); sent += 1
                except Exception: failed += 1
            except UserIsBlocked:
                delete_user(client.bot_id, user_id); blocked += 1
            except InputUserDeactivated:
                delete_user(client.bot_id, user_id); deleted += 1
            except Exception:
                failed += 1
            processed = sent + failed + blocked + deleted
            if processed % 25 == 0:
                try:
                    await status.edit(f"<b>Broadcasting...</b>\n\nSent: <code>{sent}</code>\nFailed: <code>{failed}</code>", reply_markup=cancel)
                except Exception: pass
    finally:
        client.broadcast_task = None
    processed = sent + failed + blocked + deleted
    title = "Broadcast Cancelled" if client.broadcast_cancelled else "Broadcast Completed"
    try:
        await status.edit(f"<b>{title}</b>\n\nSent: <code>{sent}</code>\nBlocked: <code>{blocked}</code>\nDeleted: <code>{deleted}</code>\nFailed: <code>{failed}</code>\nRemaining: <code>{max(0, user_count(client.bot_id) - processed)}</code>")
    except Exception: pass


async def stats(client, message):
    if not is_admin(client, message.from_user.id): return
    mine, total = collection_storage(client.bot_id), cluster_storage()
    other = max(0, total - mine); left = max(0, 512 * 1024 * 1024 - total)
    def size(n): return f"{n / 1024 / 1024:.2f} MB" if n >= 1024 * 1024 else f"{n / 1024:.2f} KB"
    await message.reply(f"<b>Bot Stats</b>\n\nUsers: <code>{user_count(client.bot_id)}</code>\n\nThis bot: <code>{size(mine)}</code>\nOther bots: <code>{size(other)}</code>\nMongoDB: <code>{size(total)} / 512 MB</code>\nLeft: <code>{size(left)}</code>")


async def shortener_cmd(client, message):
    if not is_admin(client, message.from_user.id): return
    args = message.text.split(maxsplit=2)
    if len(args) == 3:
        set_shortener(client.bot_id, args[1].strip(), args[2].strip())
        return await message.reply(f"Shortener updated.\nSite: `{args[1]}`\nAPI: `{args[2]}`")
    site, api = shortener(client.bot_id); await message.reply(f"Site: `{site}`\nAPI: `{api}`")


async def batch(client, message):
    if not is_admin(client, message.from_user.id): return
    client.input_states = getattr(client, "input_states", {})
    client.input_states[message.from_user.id] = {"type": "batch", "first": None}
    await message.reply("Forward first DB message or send its link.")


async def genlink(client, message):
    if not is_admin(client, message.from_user.id): return
    client.input_states = getattr(client, "input_states", {})
    client.input_states[message.from_user.id] = {"type": "genlink", "first": None}
    await message.reply("Forward DB message or send its link.")


async def input_handler(client, message):
    user_id = message.from_user.id
    state = getattr(client, "input_states", {}).get(user_id)
    if not state or not is_admin(client, user_id): return
    msg_id = get_message_id(client, message)
    if not msg_id: return await message.reply("Invalid DB message.")
    if state["type"] == "genlink":
        client.input_states.pop(user_id, None); return await send_link(client, message, msg_id)
    if state["first"] is None:
        state["first"] = msg_id; return await message.reply("Forward last DB message or send its link.")
    first_id = state["first"]; client.input_states.pop(user_id, None)
    await send_link(client, message, first_id, msg_id)


async def send_link(client, message, first_id, last_id=None):
    channel_id = abs(client.db_channel.id)
    raw = f"get-{first_id * channel_id}" + (f"-{last_id * channel_id}" if last_id is not None else "")
    link = f"https://t.me/{client.username}?start={await encode(raw)}"
    short = await get_shortlink(client, link)
    if short == link:
        return await message.reply(f"<b>Link:</b> {link}\n\n<b>Shortener:</b> failed")
    await message.reply(f"<b>Link:</b> {link}\n\n<b>Slink:</b> {short}")


async def auto_shortener(client, message):
    if not is_admin(client, message.from_user.id): return
    if getattr(client, "input_states", {}).get(message.from_user.id): return
    if message.command and message.command[0].lower() in {"start","broadcast","batch","genlink","stats","shortener","shortlink"}: return
    original = message.text or message.caption
    if not original: return
    match = re.search(r"https?://\S+", original)
    if not match: return
    link = match.group(0).rstrip(".,!?)]}>\"'")
    short = await get_shortlink(client, link)
    if short == link: return await message.reply("Shortener failed. Check SHORTSITE/SHORTAPI.")
    await message.reply(f"<b>Original:</b> {link}\n\n<b>Short Link:</b> {short}")


async def get_shortlink(client, link):
    site, api = shortener(client.bot_id)
    site, api = (site or "").strip().rstrip("/"), (api or "").strip()
    if not site or not api: return link
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get(f"https://{site}/api", params={"api": api, "url": link}, ssl=False) as response:
                text = (await response.text()).strip()
                if response.status != 200: return link
                try: data = await response.json(content_type=None)
                except Exception: data = None
                if isinstance(data, dict):
                    for key in ("shortenedUrl","shorturl","shortened_url","shortUrl","shortlink","link"):
                        value = data.get(key)
                        if isinstance(value, str) and value.startswith(("http://","https://")): return value
                if text.startswith(("http://","https://")): return text
    except Exception: pass
    return link


async def callbacks(client, query):
    if query.data == "cancel_broadcast":
        if is_admin(client, query.from_user.id):
            client.broadcast_cancelled = True; await query.answer("Broadcast cancelling...")
        else: await query.answer("Not authorized.", show_alert=True)
