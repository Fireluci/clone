from pyrogram import filters
from pyrogram.handlers import MessageHandler
from bot import StoreBot
from config import OWNER, ADMINS
from database import clone_id, save_clone, get_clone, get_clones, delete_clone
from handlers import register

workers = {}


def manager_admin(user_id):
    return user_id == OWNER or user_id in ADMINS


async def clone(client, message):
    if not manager_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        return await message.reply("Usage: /clone BOT_TOKEN")

    token = args[1].strip()
    bot_id = clone_id(token)
    if bot_id in workers:
        return await message.reply("Bot is already running.")
    existing = get_clone(bot_id)
    worker = StoreBot(f"clone_{bot_id}", token, bot_id)
    try:
        register(worker.client)
        await worker.start()
        if not existing:
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
    lines = ["<b>Clones</b>"]
    for item in docs:
        status = "🟢" if item["_id"] in workers else "🔴"
        lines.append(f"{status} @{item.get('username', 'unknown')} — <code>{item['_id']}</code>")
    await message.reply("\n".join(lines))


async def deletebot(client, message):
    if not manager_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        return await message.reply("Usage: /deletebot BOT_ID")
    bot_id = args[1].strip()
    worker = workers.pop(bot_id, None)
    if worker:
        await worker.stop()
    delete_clone(bot_id)
    await message.reply("Clone deleted.")


def register_manager(client):
    client.add_handler(MessageHandler(clone, filters.command("clone") & filters.private))
    client.add_handler(MessageHandler(mybots, filters.command("mybots") & filters.private))
    client.add_handler(MessageHandler(deletebot, filters.command("deletebot") & filters.private))
