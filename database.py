from pymongo import MongoClient
from config import MONGODB, DBNAME

client = MongoClient(MONGODB)
db = client[DBNAME]
clones = db["clones"]


def users(bot_id):
    return db[f"bot_{bot_id}_users"]


def settings(bot_id):
    return db[f"bot_{bot_id}_config"]


def add_user(bot_id, user_id):
    users(bot_id).update_one({"_id": user_id}, {"$set": {"_id": user_id}}, upsert=True)


def all_users(bot_id):
    return [x["_id"] for x in users(bot_id).find({}, {"_id": 1})]


def remove_user(bot_id, user_id):
    users(bot_id).delete_one({"_id": user_id})


def get_settings(bot_id):
    doc = settings(bot_id).find_one({"_id": "settings"}) or {}
    return doc


def set_settings(bot_id, values):
    settings(bot_id).update_one({"_id": "settings"}, {"$set": values}, upsert=True)
