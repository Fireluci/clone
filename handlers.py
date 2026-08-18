import asyncio
import re
import aiohttp
from pyrogram import filters
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import START_MESSAGE, FORCE_SUB_MESSAGE, FILES, FORCESUB, SHORTSITE, SHORTAPI, ADMINS
from helper_func import encode, decode, get_messages, get_message_id, is_admin, is_subscribed
from database import present_user, add_user, delete_user, all_users, user_count, collection_storage, cluster_storage, set_shortener, shortener


def register(client):
    client.add_handler(__import__("pyrogram").handlers.MessageHandler(start, filters.command("start") & filters.private))
    client.add_handler(__import__("pyrogram").handlers.MessageHandler(broadcast, filters.command("broadcast") & filters.private))
    client.add_handler(__import__("pyrogram").handlers.MessageHandler(stats, filters.command("stats") & filters.private))
    client.add_handler(__import__("pyrogram").handlers.MessageHandler(shortener_cmd, filters.command(["shortener", "shortlink"]) & filters.private))
    client.add_handler(__import__("pyrogram").handlers.MessageHandler(batch, filters.command("batch") & filters.private))
    client.add_handler(__import__("pyrogram").handlers.MessageHandler(batch_input, filters.private))
    client.add_handler(__import__("pyrogram").handlers.MessageHandler(genlink, filters.command("genlink") & filters.private))
    client.add_handler(__import__("pyrogram").handlers.MessageHandler(auto_shortener, (filters.text | filters.caption) & filters.private))
    client.add_handler(__import__("pyrogram").handlers.CallbackQueryHandler(callbacks))


async def start(client, message):
    if not await is_subscribed(None, client, message):
        return await not_joined(client, message)

    if not present_user(client.bot_id, message.from_user.id):
        add_user(client.bot_id, message.from_user.id)

    if len(message.text) <= 7:
        return await message.reply_text(
            START_MESSAGE.format(first=message.from_user.first_name),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔒 Close", callback_data="close")]])
        )

    try:
        value = await decode(message.text.split(" ", 1)[1])
        args = value.split("-")
        if len(args) == 2:
            ids = [int(args[1]) // abs(client.db_channel.id)]
        elif len(args) == 3:
            a = int(args[1]) // abs(client.db_channel.id)
            b = int(args[2]) // abs(client.db_channel.id)
            ids = range(a, b + 1) if a <= b else range(a, b - 1, -1)
        else:
            return
        messages = await get_messages(client, ids)
    except Exception:
        return await message.reply("Invalid link.")

    for msg in messages:
        try:
            await msg.copy(message.from_user.id, caption=msg.caption.html if msg.caption else None)
            await asyncio.sleep(0.5)
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception:
            pass


async def not_joined(client, message):
    if not client.forcesub:
        return await message.reply_text(START_MESSAGE.format(first=message.from_user.first_name))
    buttons = [[InlineKeyboardButton("Join Channel", url=client.invitelink)]]
    if len(message.command) > 1:
        buttons.append([InlineKeyboardButton("Try Again", url=f"https://t.me/{client.username}?start={message.command[1]}")])
    await message.reply(FORCE_SUB_MESSAGE.format(first=message.from_user.first_name), reply_markup=InlineKeyboardMarkup(buttons), quote=True)


async def broadcast(client, message):
    if not is_admin(client, message.from_user.id):
        return
    if not message.reply_to_message:
        return await message.reply("Reply to a message.")

    users = all_users(client.bot_id)
    client.broadcast_cancelled = False
    cancel = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast")]])
    status = await message.reply(f"<b>Broadcasting...</b>\n\nTotal: <code>{len(users)}</code>\nSent: <code>0</code>", reply_markup=cancel)
    sent = failed = blocked = deleted = 0

    for user_id in users:
        if client.broadcast_cancelled:
            break
        try:
            await message.reply_to_message.copy(user_id)
            sent += 1
        except FloodWait as e:
            await asyncio.sleep(e.value)
            if client.broadcast_cancelled:
                break
            try:
                await message.reply_to_message.copy(user_id)
                sent += 1
            except Exception:
                failed += 1
        except UserIsBlocked:
            delete_user(client.bot_id, user_id)
            blocked += 1
        except InputUserDeactivated:
            delete_user(client.bot_id, user_id)
            deleted += 1
        except Exception:
            failed += 1

        processed = sent + failed + blocked + deleted
        if processed % 25 == 0:
            try:
                await status.edit(f"<b>Broadcasting...</b>\n\nTotal: <code>{len(users)}</code>\nSent: <code>{sent}</code>\nFailed: <code>{failed}</code>", reply_markup=cancel)
            except Exception:
                pass

    processed = sent + failed + blocked + deleted
    title = "Broadcast Cancelled" if client.broadcast_cancelled else "Broadcast Completed"
    await status.edit(f"<b>{title}</b>\n\nTotal: <code>{len(users)}</code>\nSent: <code>{sent}</code>\nBlocked: <code>{blocked}</code>\nDeleted: <code>{deleted}</code>\nFailed: <code>{failed}</code>\nRemaining: <code>{len(users) - processed}</code>")


async def stats(client, message):
    if not is_admin(client, message.from_user.id):
        return
    mine = collection_storage(client.bot_id)
    total = cluster_storage()
    other = max(0, total - mine)
    limit = 512 * 1024 * 1024
    left = max(0, limit - total)

    def size(n):
        return f"{n / 1024 / 1024:.2f} MB" if n >= 1024 * 1024 else f"{n / 1024:.2f} KB"

    await message.reply(f"<b>Bot Stats</b>\n\nUsers: <code>{user_count(client.bot_id)}</code>\n\nThis bot: <code>{size(mine)}</code>\nOther bots: <code>{size(other)}</code>\nMongoDB: <code>{size(total)} / 512 MB</code>\nLeft: <code>{size(left)}</code>")


async def shortener_cmd(client, message):
    if not is_admin(client, message.from_user.id):
        return
    args = message.text.split(maxsplit=2)
    if len(args) == 3:
        set_shortener(client.bot_id, args[1], args[2])
        return await message.reply(f"Shortener updated.\nSite: `{args[1]}`\nAPI: `{args[2]}`")
    site, api = shortener(client.bot_id)
    await message.reply(f"Site: `{site}`\nAPI: `{api}`")


async def batch(client, message):
    if not is_admin(client, message.from_user.id):
        return

    try:
        first = await client.ask(
            message.chat.id,
            "Forward first DB message or send its link.",
            filters=filters.forwarded | filters.text,
            timeout=60
        )

        first_id = await get_message_id(client, first)

        if not first_id:
            return await first.reply("Invalid DB message.")

        last = await client.ask(
            message.chat.id,
            "Forward last DB message or send its link.",
            filters=filters.forwarded | filters.text,
            timeout=60
        )

        last_id = await get_message_id(client, last)

        if not last_id:
            return await last.reply("Invalid DB message.")

        raw = f"get-{first_id * abs(client.db_channel.id)}-{last_id * abs(client.db_channel.id)}"
        link = f"https://telegram.me/{client.username}?start={await encode(raw)}"
        slink = await get_shortlink(client, link)

        await last.reply(
            f"<b>Link:</b> {link}\n\n<b>Slink:</b> {slink}"
        )

    except Exception as e:
        await message.reply(
            f"<b>Batch error:</b>\n<code>{e}</code>"
        )


async def genlink(client, message):
    if not is_admin(client, message.from_user.id):
        return
    try:
        msg = await client.ask(message.chat.id, "Forward DB message or send its link.", filters=(filters.forwarded | (filters.text & ~filters.forwarded)), timeout=60)
    except Exception:
        return
    msg_id = await get_message_id(client, msg)
    if not msg_id:
        return await msg.reply("Invalid DB message.")

    link = f"https://telegram.me/{client.username}?start={await encode(f'get-{msg_id * abs(client.db_channel.id)}')}"
    slink = await get_shortlink(client, link)
    await msg.reply(f"<b>Link:</b> {link}\n\n<b>Slink:</b> {slink}")


async def auto_shortener(client, message):
    if not is_admin(client, message.from_user.id):
        return
    if message.command and message.command[0].lower() in {"start", "broadcast", "batch", "genlink", "stats", "shortener", "shortlink"}:
        return
    original = message.text or message.caption
    if not original:
        return
    match = re.search(r"https?://\S+", original)
    if not match:
        return
    link = match.group(0).rstrip(".,!?)]}>\"'")
    link = link.replace("https://t.me/", "https://telegram.me/").replace("http://t.me/", "https://telegram.me/").replace("http://telegram.me/", "https://telegram.me/")
    if "telegram.me/+" in link:
        link = link.replace("telegram.me/+", "telegram.me/%2B")
    slink = await get_shortlink(client, link)
    await message.reply_text(f"<b>Original:-</b> {original}\n\n<b>Short Link:-</b> {slink}")


async def get_shortlink(client, link):
    site, api = shortener(client.bot_id)
    if not site or not api:
        return link
    if site == "api.shareus.in":
        url = f"https://{site}/shortLink"
        params = {"token": api, "format": "json", "link": link}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, raise_for_status=True, ssl=False) as response:
                    data = await response.json(content_type="text/html")
                    if data.get("status") == "success":
                        return data.get("shortlink", link)
        except Exception:
            pass
        return f"https://{site}/shortLink?token={api}&format=json&link={link}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://{site}/api", params={"api": api, "url": link}, raise_for_status=True, ssl=False) as response:
                data = await response.json(content_type=None)
                if data.get("status") == "success":
                    return data.get("shortenedUrl", link)
    except Exception:
        pass
    return f"https://{site}/api?api={api}&url={link}"


async def callbacks(client, query):
    if query.data == "close":
        try:
            await query.message.delete()
        except Exception:
            pass
    elif query.data == "cancel_broadcast":
        if is_admin(client, query.from_user.id):
            client.broadcast_cancelled = True
            await query.answer("Broadcast cancelled.")
        else:
            await query.answer("Not authorized.", show_alert=True)
