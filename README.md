# Multi File Store Bot

One main manager bot can run multiple file-store clone bots in the same Koyeb instance.

## Environment variables

- `BOTTOKEN` - main manager bot token
- `MONGODB` - MongoDB connection URI
- `DBNAME` - MongoDB database name
- `APP_ID` - Telegram API ID
- `API_HASH` - Telegram API hash
- `OWNER` - main manager owner ID
- `ADMINS` - default clone admins, space separated IDs
- `FILES` - default files/database channel ID
- `FORCESUB` - default force-sub channel ID
- `SHORTSITE` - default shortener site
- `SHORTAPI` - default shortener API
- `WORKERS` - Pyrogram workers
- `PORT` - web health-check port

## Manager commands

`/clone BOT_TOKEN` - add and start a clone

`/mybots` - list clones

`/deletebot BOT_ID` - stop and remove a clone

## Clone commands

`/genlink` - generate a file link

`/batch` - generate a batch link

`/shortener site.com API` - change only that clone's shortener

`/shortener` - show that clone's current shortener

`/broadcast` - broadcast a replied message

`/stats` - show users and MongoDB usage

Clone-specific overrides are stored in `bot_<bot_id>_config`. If a value is absent, the default from `config.py` is used.
