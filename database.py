from pymongo import MongoClient
from config import MONGODB, DBNAME, FILES, FORCESUB, SHORTSITE, SHORTAPI, ADMINS

LOGGER = __import__("logging").getLogger(__name__)

if not MONGODB:
    raise RuntimeError("MONGODB is missing")
if not DBNAME:
    raise RuntimeError("DBNAME is missing")

mongo = MongoClient(MONGODB, serverSelectionTimeoutMS=10000)
main_db = mongo[DBNAME]
clones = main_db["clones"]


def clone_id(username):
    """Use the Telegram bot username as the human-readable clone ID."""
    return str(username).lstrip("@").strip().lower()


def clone_db(bot_id):
    item = clones.find_one({"_id": bot_id}, {"database": 1})
    return mongo[item.get("database", f"clone_{bot_id}")] if item else mongo[f"clone_{bot_id}"]


def users(bot_id):
    return clone_db(bot_id)["users"]


def settings(bot_id):
    return clone_db(bot_id)["config"]


def save_clone(bot_id, token, username, display_name=None):
    old = clones.find_one({"_id": bot_id}) or {}
    current = settings(bot_id).find_one({"_id": "settings"}) or {}
    current.pop("_id", None)

    defaults = {
        "files": FILES,
        "forcesub": FORCESUB,
        "shortsite": SHORTSITE,
        "shortapi": SHORTAPI,
        "admins": ADMINS,
    }
    for key, value in defaults.items():
        current.setdefault(key, value)

    data = {
        "_id": bot_id,
        "token": token,
        "username": username,
        "display_name": display_name or old.get("display_name") or (f"@{username}" if username else "Unknown Bot"),
        "database": old.get("database", f"clone_{bot_id}"),
        "settings": current,
    }
    clones.replace_one({"_id": bot_id}, data, upsert=True)
    settings(bot_id).replace_one({"_id": "settings"}, current, upsert=True)
    return data


def get_clone(bot_id):
    return clones.find_one({"_id": bot_id})


def get_clones():
    return list(clones.find())


def delete_clone(bot_id):
    item = clones.find_one({"_id": bot_id}, {"database": 1})
    clones.delete_one({"_id": bot_id})
    mongo.drop_database(item.get("database", f"clone_{bot_id}") if item else f"clone_{bot_id}")


def migrate_clone_id(old_id, new_id, username):
    """Rename a legacy hash clone ID without moving/deleting its database."""
    if old_id == new_id:
        return True
    old = clones.find_one({"_id": old_id})
    if not old:
        return False
    existing = clones.find_one({"_id": new_id})
    if existing and existing.get("token") != old.get("token"):
        raise RuntimeError(f"Clone username @{username} already belongs to another clone")
    old["_id"] = new_id
    old["username"] = username
    clones.replace_one({"_id": new_id}, old, upsert=True)
    if old_id != new_id:
        clones.delete_one({"_id": old_id})
    return True


def get_setting(bot_id, key, default=None):
    item = settings(bot_id).find_one({"_id": "settings"}) or {}
    return item.get(key, default)


def set_setting(bot_id, key, value):
    settings(bot_id).update_one({"_id": "settings"}, {"$set": {key: value}}, upsert=True)


def present_user(bot_id, user_id):
    return users(bot_id).find_one({"_id": user_id}, {"_id": 1}) is not None


def add_user(bot_id, user_id):
    users(bot_id).update_one({"_id": user_id}, {"$setOnInsert": {"_id": user_id}}, upsert=True)


def delete_user(bot_id, user_id):
    users(bot_id).delete_one({"_id": user_id})


def all_users(bot_id):
    return list(users(bot_id).find({}, {"_id": 1}, batch_size=500))


def user_count(bot_id):
    return users(bot_id).count_documents({})


def database_storage(db):
    total = 0
    for name in db.list_collection_names():
        try:
            s = db.command("collStats", name)
            total += s.get("storageSize", 0) + s.get("totalIndexSize", 0)
        except Exception:
            LOGGER.exception("Failed to read collection stats for %s", name)
    return total


def cluster_storage():
    total = database_storage(main_db)
    for item in clones.find({}, {"database": 1}):
        name = item.get("database")
        if name:
            total += database_storage(mongo[name])
    return total


def collection_storage(bot_id):
    return database_storage(clone_db(bot_id))


def shortener(bot_id):
    return get_setting(bot_id, "shortsite", SHORTSITE), get_setting(bot_id, "shortapi", SHORTAPI)


def set_shortener(bot_id, site, api):
    set_setting(bot_id, "shortsite", site)
    set_setting(bot_id, "shortapi", api)
