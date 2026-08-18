import re
from pyrogram import filters
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot import StoreBot
from config import OWNER, ADMINS, BOTTOKEN
from database import clone_id, save_clone, get_clone, get_clones, delete_clone, get_setting

workers = {}
TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")


def manager_admin(user_id):
    return user_id == OWNER or user_id in ADMINS


def owner(user_id):
    return user_id == OWNER


def clone_buttons(docs):
    rows = []
    for x in docs:
        bot_id = x["_id"]
        status = "🟢" if bot_id in workers else "🔴"
        rows.append([InlineKeyboardButton(f"{status} @{x.get('username', 'unknown')}", callback_data=f"clone:{bot_id}")])
    return rows


async def start_manager(client, message):
    await message.reply(
        "<b>🌟 Hello {}</b>\n\n<b>I'm a File Store Bot Manager 🤖</b>\n\nManage and run your bot clones from here.".format(message.from_user.first_name),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🤖 Bot Clones", callback_data="clones")]])
    )


async def clone(client, message):
    if not manager_admin(message.from_user.id):
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
        save_clone(bot_id, token, worker.username)
        workers[bot_id] = worker
        await message.reply(f"Clone started: @{worker.username}\nID: <code>{bot_id}</code>")
    except Exception as e:
        await worker.stop()
        await message.reply(f"Could not start bot.\n<code>{e}</code>")


async def mybots(client, message):
    if not manager_admin(message.from_user.id):
        return
    docs = get_clones()
    if not docs:
        return await message.reply("No clones.")
    await message.reply("<b>Bot Clones</b>", reply_markup=InlineKeyboardMarkup(clone_buttons(docs)))


async def deletebot(client, message):
    if not owner(message.from_user.id):
        return
    args = message.text.split(maxsplit=2)
    if len(args) == 2:
        return await message.reply(f"Confirm deletion with:\n<code>/deletebot {args[1]} confirm</code>")
    if len(args) != 3 or args[2].lower() != "confirm":
        return await message.reply("Use confirm to delete the clone.")
    bot_id = args[1].strip()
    if not get_clone(bot_id):
        return await message.reply("Clone not found.")
    worker = workers.pop(bot_id, None)
    if worker:
        await worker.stop()
    delete_clone(bot_id)
    await message.reply("Clone and all its MongoDB data deleted.")


async def manager_callbacks(client, query):
    data = query.data or ""
    if data == "clones":
        if not owner(query.from_user.id):
            await query.answer()
            return
        docs = get_clones()
        if not docs:
            await query.answer("No clones.", show_alert=True)
            return
        await query.message.edit_text("<b>Bot Clones</b>", reply_markup=InlineKeyboardMarkup(clone_buttons(docs)))
        await query.answer()
        return

    if data.startswith("clone:"):
        if not owner(query.from_user.id):
            await query.answer()
            return
        bot_id = data.split(":", 1)[1]
        item = get_clone(bot_id)
        if not item:
            await query.answer("Clone not found.", show_alert=True)
            return
        worker = workers.get(bot_id)
        site = get_setting(bot_id, "shortsite", "")
        api = get_setting(bot_id, "shortapi", "")
        files = get_setting(bot_id, "files", 0)
        forcesub = get_setting(bot_id, "forcesub", 0)
        admins = get_setting(bot_id, "admins", [])
        status = "🟢 Running" if worker else "🔴 Stopped"
        text = (
            f"<b>Clone Settings</b>\n\n"
            f"Status: {status}\n"
            f"Username: @{item.get('username', 'unknown')}\n"
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
            [InlineKeyboardButton("🗑 Delete Clone", callback_data=f"delete:{bot_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="clones")]
        ]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        await query.answer()
        return

    if data.startswith("delete:"):
        if not owner(query.from_user.id):
            await query.answer()
            return
        bot_id = data.split(":", 1)[1]
        if not get_clone(bot_id):
            await query.answer("Clone not found.", show_alert=True)
            return
        await query.message.edit_text(
            "<b>Delete this clone?</b>\n\nThis will stop the bot and permanently delete its MongoDB database and all stored users/settings.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚠️ Yes, Delete", callback_data=f"confirm_delete:{bot_id}")],
                [InlineKeyboardButton("Cancel", callback_data=f"clone:{bot_id}")]
            ])
        )
        await query.answer()
        return

    if data.startswith("confirm_delete:"):
        if not owner(query.from_user.id):
            await query.answer()
            return
        bot_id = data.split(":", 1)[1]
        if not get_clone(bot_id):
            await query.answer("Clone not found.", show_alert=True)
            return
        worker = workers.pop(bot_id, None)
        if worker:
            await worker.stop()
        delete_clone(bot_id)
        await query.message.edit_text("<b>Clone deleted.</b>\n\nIts bot entry and MongoDB database were permanently removed.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Bot Clones", callback_data="clones")]]))
        await query.answer("Deleted")


def register_manager(client):
    H = MessageHandler
    client.add_handler(H(start_manager, filters.command("start") & filters.private))
    client.add_handler(H(clone, filters.command("clone") & filters.private))
    client.add_handler(H(mybots, filters.command("mybots") & filters.private))
    client.add_handler(H(deletebot, filters.command("deletebot") & filters.private))
    client.add_handler(CallbackQueryHandler(manager_callbacks))
