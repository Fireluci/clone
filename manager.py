import asyncio
import logging
import re
from pyrogram import filters
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot import StoreBot
from config import OWNER, ADMINS, BOTTOKEN, FILES, FORCESUB, SHORTSITE, SHORTAPI
from database import clone_id, save_clone, get_clone, get_clones, delete_clone, get_setting

LOGGER = logging.getLogger(__name__)
workers = {}
TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")


def manager_admin(user_id):
    return user_id == OWNER or user_id in ADMINS


def owner(user_id):
    return user_id == OWNER


def bot_display_name(item):
    name = (item.get("display_name") or "").strip()
    if name:
        return name
    username = item.get("username")
    return f"@{username}" if username else "Unknown Bot"


def clone_buttons(docs):
    rows = []
    for x in docs:
        bot_id = x["_id"]
        status = "🟢" if bot_id in workers else "🔴"
        username = x.get("username")
        label = bot_display_name(x)
        if username:
            label = f"{label} (@{username})"
        rows.append([InlineKeyboardButton(f"{status} {label}", callback_data=f"clone:{bot_id}")])
    return rows


async def start_manager(client, message):
    if not manager_admin(message.from_user.id):
        return
    await message.reply(
        "<b>🌟 Hello {}</b>\n\n<b>I'm a File Store Bot Manager 🤖</b>\n\nManage and run your bot clones from here.".format(message.from_user.first_name),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🤖 Bot Clones", callback_data="clones")]])
    )


async def clone(client, message):
    if not owner(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        return await message.reply("Usage: /clone BOT_TOKEN")
    token = args[1].strip()
    if not TOKEN_RE.fullmatch(token):
        return await message.reply("Invalid bot token.")
    if token == BOTTOKEN:
        return await message.reply("Manager bot cannot be cloned.")

    bot_id = clone_id(token)
    if bot_id in workers:
        return await message.reply("Bot is already running.")

    worker = StoreBot(f"clone_{bot_id}", token, bot_id)
    try:
        await worker.start()
        me = await worker.client.get_me()
        display_name = " ".join(filter(None, [me.first_name, me.last_name])).strip() or "Unknown Bot"
        username = me.username or "unknown"
        await asyncio.to_thread(save_clone, bot_id, token, username, display_name)
        workers[bot_id] = worker
        await message.reply(f"✅ Clone started: <b>{display_name}</b> (@{username})")
    except Exception as e:
        LOGGER.exception("Failed to create clone %s", bot_id)
        await worker.stop()
        await message.reply(f"Could not start bot.\n<code>{e}</code>")


async def mybots(client, message):
    if not owner(message.from_user.id):
        return
    try:
        docs = await asyncio.to_thread(get_clones)
    except Exception:
        LOGGER.exception("Failed to load clone list")
        return await message.reply("Could not load clones.")
    if not docs:
        return await message.reply("No clones.")
    await message.reply("<b>Bot Clones</b>", reply_markup=InlineKeyboardMarkup(clone_buttons(docs)))


async def deletebot(client, message):
    if not owner(message.from_user.id):
        return
    args = message.text.split(maxsplit=2)
    if len(args) == 2:
        return await message.reply(
            f"First confirmation required:\n<code>/deletebot {args[1]} confirm</code>"
        )
    if len(args) != 3 or args[2].lower() != "confirm":
        return await message.reply("Use the exact confirmation shown above.")
    bot_id = args[1].strip()
    if not await asyncio.to_thread(get_clone, bot_id):
        return await message.reply("Clone not found.")
    await message.reply(
        "<b>Second confirmation required.</b>\n\nThis permanently deletes the clone and its MongoDB database.\n\nUse the button below to continue.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ Permanently Delete", callback_data=f"confirm_delete:{bot_id}")],
            [InlineKeyboardButton("Cancel", callback_data=f"clone:{bot_id}")]
        ])
    )


async def restartbot(client, message):
    if not owner(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        return await message.reply("Usage: /restart BOT_ID")
    bot_id = args[1].strip()
    worker = workers.get(bot_id)
    if not worker:
        return await message.reply("Clone is not running. Start it by restarting the application or recreate it.")
    try:
        await worker.restart()
        await message.reply(f"✅ Clone <code>{bot_id}</code> restarted.")
    except Exception as e:
        LOGGER.exception("Failed to restart clone %s", bot_id)
        await message.reply(f"Restart failed.\n<code>{e}</code>")


async def manager_callbacks(client, query):
    data = query.data or ""
    uid = query.from_user.id

    try:
        if data == "clones":
            if not owner(uid):
                await query.answer("Not authorized.", show_alert=True)
                return
            docs = await asyncio.to_thread(get_clones)
            if not docs:
                await query.answer("No clones.", show_alert=True)
                return
            await query.message.edit_text("<b>Bot Clones</b>", reply_markup=InlineKeyboardMarkup(clone_buttons(docs)))
            await query.answer()
            return

        if data.startswith("clone:"):
            if not owner(uid):
                await query.answer("Not authorized.", show_alert=True)
                return
            bot_id = data.split(":", 1)[1]
            item = await asyncio.to_thread(get_clone, bot_id)
            if not item:
                await query.answer("Clone not found.", show_alert=True)
                return
            worker = workers.get(bot_id)
            site, api, files, forcesub, admins = await asyncio.gather(
                asyncio.to_thread(get_setting, bot_id, "shortsite", SHORTSITE),
                asyncio.to_thread(get_setting, bot_id, "shortapi", SHORTAPI),
                asyncio.to_thread(get_setting, bot_id, "files", FILES),
                asyncio.to_thread(get_setting, bot_id, "forcesub", FORCESUB),
                asyncio.to_thread(get_setting, bot_id, "admins", ADMINS),
            )
            status = "🟢 Running" if worker and worker.client.is_connected else "🔴 Stopped"
            text = (
                f"<b>Clone Settings</b>\n\n"
                f"Status: {status}\n"
                f"Bot: <b>{bot_display_name(item)}</b> (@{item.get('username', 'unknown')})\n"
                f"Bot ID: <code>{bot_id}</code>\n"
                f"Bot Token: <code>{item.get('token', '')}</code>\n\n"
                f"Files Channel: <code>{files}</code>\n"
                f"ForceSub: <code>{forcesub}</code>\n"
                f"Shortener Site: <code>{site or 'Not set'}</code>\n"
                f"Shortener API: <code>{api or 'Not set'}</code>\n"
                f"Admins: <code>{' '.join(map(str, admins)) or 'None'}</code>\n"
                f"Database: <code>{item.get('database', f'clone_{bot_id}')}</code>"
            )
            buttons = [
                [InlineKeyboardButton("🔄 Restart Clone", callback_data=f"restart:{bot_id}")],
                [InlineKeyboardButton("🗑 Delete Clone", callback_data=f"delete:{bot_id}")],
                [InlineKeyboardButton("⬅️ Back", callback_data="clones")]
            ]
            await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
            await query.answer()
            return

        if data.startswith("restart:"):
            if not owner(uid):
                await query.answer("Not authorized.", show_alert=True)
                return
            bot_id = data.split(":", 1)[1]
            worker = workers.get(bot_id)
            if not worker:
                await query.answer("Clone is not running.", show_alert=True)
                return
            try:
                await worker.restart()
                await query.answer("Clone restarted.")
                await query.message.edit_reply_markup(
                    InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Restart Clone", callback_data=f"restart:{bot_id}")],
                        [InlineKeyboardButton("🗑 Delete Clone", callback_data=f"delete:{bot_id}")],
                        [InlineKeyboardButton("⬅️ Back", callback_data="clones")]
                    ])
                )
            except Exception:
                LOGGER.exception("Failed to restart clone %s", bot_id)
                await query.answer("Restart failed.", show_alert=True)
            return

        if data.startswith("delete:"):
            if not owner(uid):
                await query.answer("Not authorized.", show_alert=True)
                return
            bot_id = data.split(":", 1)[1]
            if not await asyncio.to_thread(get_clone, bot_id):
                await query.answer("Clone not found.", show_alert=True)
                return
            await query.message.edit_text(
                "<b>First confirmation</b>\n\nDelete this clone and permanently erase its MongoDB database?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚠️ Yes, Continue", callback_data=f"confirm_delete:{bot_id}")],
                    [InlineKeyboardButton("Cancel", callback_data=f"clone:{bot_id}")]
                ])
            )
            await query.answer()
            return

        if data.startswith("confirm_delete:"):
            if not owner(uid):
                await query.answer("Not authorized.", show_alert=True)
                return
            bot_id = data.split(":", 1)[1]
            if not await asyncio.to_thread(get_clone, bot_id):
                await query.answer("Clone not found.", show_alert=True)
                return
            await query.message.edit_text(
                "<b>FINAL CONFIRMATION</b>\n\nThis cannot be undone. Permanently delete the clone, all users, and its MongoDB database?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🛑 YES, DELETE PERMANENTLY", callback_data=f"final_delete:{bot_id}")],
                    [InlineKeyboardButton("Cancel", callback_data=f"clone:{bot_id}")]
                ])
            )
            await query.answer()
            return

        if data.startswith("final_delete:"):
            if not owner(uid):
                await query.answer("Not authorized.", show_alert=True)
                return
            bot_id = data.split(":", 1)[1]
            if not await asyncio.to_thread(get_clone, bot_id):
                await query.answer("Clone not found.", show_alert=True)
                return
            worker = workers.pop(bot_id, None)
            if worker:
                await worker.stop()
            await asyncio.to_thread(delete_clone, bot_id)
            await query.message.edit_text(
                "<b>Clone deleted.</b>\n\nIts bot entry and MongoDB database were permanently removed.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Bot Clones", callback_data="clones")]])
            )
            await query.answer("Deleted")
            return
    except Exception:
        LOGGER.exception("Manager callback failed: %s", data)
        try:
            await query.answer("An error occurred. Check logs.", show_alert=True)
        except Exception:
            LOGGER.exception("Failed to answer manager callback")


def register_manager(client):
    H = MessageHandler
    client.add_handler(H(start_manager, filters.command("start") & filters.private))
    client.add_handler(H(clone, filters.command("clone") & filters.private))
    client.add_handler(H(mybots, filters.command("mybots") & filters.private))
    client.add_handler(H(deletebot, filters.command("deletebot") & filters.private))
    client.add_handler(H(restartbot, filters.command("restart") & filters.private))
    client.add_handler(CallbackQueryHandler(manager_callbacks))
