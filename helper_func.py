import asyncio
import base64
import re
from pyrogram import filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait
from pyrogram.errors.exceptions.bad_request_400 import UserNotParticipant
from config import FORCESUB, ADMINS


def bot_admins(client):
    return getattr(client, "admins", ADMINS)


def is_admin(client, user_id):
    return user_id in bot_admins(client)


async def is_subscribed(_, client, update):
    forcesub = getattr(client, "forcesub", FORCESUB)
    if not forcesub or is_admin(client, update.from_user.id):
        return True
    try:
        member = await client.get_chat_member(forcesub, update.from_user.id)
        return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER)
    except UserNotParticipant:
        return False
    except Exception:
        return False


def subscribed():
    return filters.create(is_subscribed)


async def encode(value):
    return base64.urlsafe_b64encode(value.encode("ascii")).decode("ascii").rstrip("=")


async def decode(value):
    value = value.strip("=")
    return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode("ascii")).decode("ascii")


async def get_messages(client, message_ids):
    ids = list(message_ids)
    result = []
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        try:
            result.extend(await client.get_messages(client.db_channel.id, chunk))
        except FloodWait as e:
            await asyncio.sleep(e.value)
            result.extend(await client.get_messages(client.db_channel.id, chunk))
    return result


async def get_message_id(client, message):
    if message.forward_from_chat:
        if message.forward_from_chat.id == client.db_channel.id:
            return message.forward_from_message_id
        return 0
    if message.forward_sender_name or not message.text:
        return 0

    match = re.match(r"https://t.me/(?:c/)?(.*)/(\d+)", message.text)
    if not match:
        return 0

    channel = match.group(1)
    msg_id = int(match.group(2))
    if channel.isdigit():
        return msg_id if f"-100{channel}" == str(client.db_channel.id) else 0
    return msg_id if channel == client.db_channel.username else 0
