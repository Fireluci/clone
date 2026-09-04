import asyncio
import base64
import re
import logging
from pyrogram import filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait
from pyrogram.errors.exceptions.bad_request_400 import UserNotParticipant
from config import FORCESUB, ADMINS

LOGGER = logging.getLogger(__name__)


def bot_admins(client):
    return getattr(client, "admins", ADMINS)


def is_admin(client, user_id):
    return user_id in bot_admins(client)


async def is_subscribed(_, client, update):
    user = getattr(update, "from_user", None)
    if not user:
        return True

    user_id = user.id
    forcesub = getattr(client, "forcesub", FORCESUB)
    if not forcesub or is_admin(client, user_id):
        return True
    try:
        member = await client.get_chat_member(forcesub, user_id)
        return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER)
    except UserNotParticipant:
        return False
    except Exception:
        LOGGER.exception("ForceSub check failed for user %s on bot %s", user_id, getattr(client, "bot_id", "unknown"))
        return False


def subscribed():
    return filters.create(is_subscribed)


async def encode(value):
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


async def decode(value):
    value = value.strip()
    return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode()).decode()


async def get_messages(client, message_ids):
    ids = list(message_ids)
    result = []
    for i in range(0, len(ids), 200):
        try:
            result.extend(await client.get_messages(client.db_channel.id, ids[i:i + 200]))
        except FloodWait as e:
            LOGGER.warning("FloodWait while fetching messages for bot %s: %ss", getattr(client, "bot_id", "unknown"), e.value)
            await asyncio.sleep(e.value + 1)
            result.extend(await client.get_messages(client.db_channel.id, ids[i:i + 200]))
        except Exception:
            LOGGER.exception("Failed to fetch messages for bot %s", getattr(client, "bot_id", "unknown"))
            raise
    return result


def get_message_id(client, message):
    chat = message.forward_from_chat
    if chat:
        return message.forward_from_message_id if chat.id == client.db_channel.id else 0

    origin = getattr(message, "forward_origin", None)
    if origin:
        origin_chat = getattr(origin, "chat", None)
        origin_id = getattr(origin, "message_id", None)
        if origin_chat and origin_id:
            return origin_id if origin_chat.id == client.db_channel.id else 0

    text = (message.text or "").strip()
    if message.forward_sender_name or not text:
        return 0
    match = re.fullmatch(r"https?://(?:t\.me|telegram\.me)/(?:c/)?([^/]+)/([0-9]+)", text)
    if not match:
        return 0
    channel, msg_id = match.groups()
    if channel.isdigit():
        return int(msg_id) if int(f"-100{channel}") == client.db_channel.id else 0
    return int(msg_id) if channel.lower() == (client.db_channel.username or "").lower().lstrip("@") else 0
