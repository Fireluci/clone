# MultiClone File Store Bot

One Koyeb instance can run a manager bot and multiple file-store clones.

## Environment variables

Set these on Koyeb for the manager/defaults:

- `BOTTOKEN` — manager bot token
- `MONGODB` — MongoDB cluster URI
- `DBNAME` — manager database name
- `APP_ID`
- `API_HASH`
- `OWNER`
- `FILES` — default file database channel
- `FORCESUB` — default force-sub channel, or `0`
- `SHORTSITE` — default shortener
- `SHORTAPI` — default shortener API
- `ADMINS` — default admin IDs separated by spaces
- `WORKERS`
- `PORT`

The manager token is the only bot token stored in Koyeb. Clone tokens are added with `/clone BOT_TOKEN` and saved in MongoDB.

Each clone gets its own MongoDB database named `clone_<id>`. Its `users` and `config` collections are isolated from other clones. Clone settings are initialized from the defaults and can be edited directly in MongoDB.

## Commands

Manager:

- `/clone BOT_TOKEN`
- `/mybots`
- `/deletebot BOT_ID`

Clone:

- `/start`
- `/genlink`
- `/batch`
- `/shortener`
- `/shortener site.com API`
- `/broadcast`
- `/stats`

## Existing links

The link encoding/decoding format is kept compatible with the original bot. Existing links continue to work when the old bot token and its original `FILES` channel are kept on the clone.
