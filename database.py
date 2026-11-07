"""MongoDB layer shared by the manager and the clones.

Layout:
  <DBNAME>.clones     one document per clone: token, username, display name, database name
  <bot username>      one database per clone (named after its bot username), holding
      users           everyone who pressed /start on that clone
      config          "settings" (files, forcesub, shortener, admins) and "info" (bot name)
"""
import re

from pymongo import MongoClient

from config import DBNAME, DEFAULTS, LOGGER, MONGODB

mongo = MongoClient(MONGODB, serverSelectionTimeoutMS=10000)
clones = mongo[DBNAME]["clones"]


# ───────────────────────── clones ─────────────────────────
def clone_id(username):
    """The clone's ID (and default database name) is its bot username, lowercase."""
    return str(username).lstrip("@").strip().lower()


def get_clone(bot_id):
    return clones.find_one({"_id": bot_id})


def get_clones():
    return list(clones.find())


def clone_db(bot_id):
    """The database that belongs to this clone."""
    item = clones.find_one({"_id": bot_id}, {"database": 1}) or {}
    return mongo[item.get("database") or bot_id]


def check_database(name):
    """Make sure an existing database can be given to a clone. Returns its user count."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,38}", name):
        raise ValueError("Invalid database name.")
    if name in (DBNAME, "admin", "local", "config"):
        raise ValueError("That database cannot be used.")
    count = mongo[name]["users"].estimated_document_count()
    if not count:
        raise ValueError(f"Database '{name}' has no users - check the name.")
    return count


def save_clone(bot_id, token, username, display_name, database=None):
    """Register a clone (or update it). Its database is the bot username unless one is adopted."""
    old = clones.find_one({"_id": bot_id}) or {}
    database = database or old.get("database") or (bot_id if bot_id != DBNAME else f"clone_{bot_id}")
    config = mongo[database]["config"]

    current = config.find_one({"_id": "settings"}) or {}
    current.pop("_id", None)

    # An old sarah-jisoo database keeps its shortener in a different document.
    legacy = config.find_one({"_id": "shortener_config"}) or {}
    if legacy.get("shortener") and legacy.get("shortener_api"):
        current.setdefault("shortsite", legacy["shortener"])
        current.setdefault("shortapi", legacy["shortener_api"])

    for key, value in DEFAULTS.items():
        current.setdefault(key, value)
    config.replace_one({"_id": "settings"}, current, upsert=True)

    clones.replace_one({"_id": bot_id}, {
        "_id": bot_id,
        "token": token,
        "username": username,
        "display_name": display_name,
        "database": database,
    }, upsert=True)
    set_info(bot_id, username, display_name)


def set_info(bot_id, username, display_name):
    """Keep the bot's name up to date, in the manager and in the clone's own database."""
    clones.update_one({"_id": bot_id}, {"$set": {"username": username, "display_name": display_name}})
    clone_db(bot_id)["config"].update_one(
        {"_id": "info"}, {"$set": {"username": username, "name": display_name}}, upsert=True)


def set_database(bot_id, name):
    """Move a clone to another (existing) database."""
    item = clones.find_one({"_id": bot_id})
    save_clone(bot_id, item["token"], item["username"], item["display_name"], name)


def delete_clone(bot_id):
    """Remove the clone and permanently drop its database."""
    item = clones.find_one({"_id": bot_id}, {"database": 1})
    clones.delete_one({"_id": bot_id})
    mongo.drop_database((item or {}).get("database") or bot_id)


# ───────────────────────── settings ─────────────────────────
def settings(bot_id):
    """All settings of a clone, with defaults filled in."""
    saved = clone_db(bot_id)["config"].find_one({"_id": "settings"}) or {}
    result = {**DEFAULTS, **saved}
    result["files"] = int(result["files"])
    result["forcesub"] = int(result["forcesub"])
    result["admins"] = [int(x) for x in result["admins"]]
    return result


def put(bot_id, **values):
    """Save one or more settings, e.g. put(bot_id, files=-100123)."""
    clone_db(bot_id)["config"].update_one({"_id": "settings"}, {"$set": values}, upsert=True)


# ───────────────────────── users ─────────────────────────
def add_user(bot_id, user_id):
    clone_db(bot_id)["users"].update_one({"_id": user_id}, {"$setOnInsert": {"_id": user_id}}, upsert=True)


def del_user(bot_id, user_id):
    clone_db(bot_id)["users"].delete_one({"_id": user_id})


def all_users(bot_id):
    return [user["_id"] for user in clone_db(bot_id)["users"].find({}, {"_id": 1}, batch_size=500)]


def user_count(bot_id):
    return clone_db(bot_id)["users"].count_documents({})


# ───────────────────────── storage stats ─────────────────────────
def storage(database):
    total = 0
    for name in database.list_collection_names():
        try:
            stats = database.command("collStats", name)
            total += stats.get("storageSize", 0) + stats.get("totalIndexSize", 0)
        except Exception:
            LOGGER.exception("Failed to read collection stats for %s", name)
    return total


def bot_storage(bot_id):
    return storage(clone_db(bot_id))


def cluster_storage():
    total = storage(mongo[DBNAME])
    for item in clones.find({}, {"database": 1}):
        if item.get("database"):
            total += storage(mongo[item["database"]])
    return total
