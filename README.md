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

The manager/default credentials may be supplied through environment variables. Clone tokens are stored in MongoDB because the manager must restart clones after a process restart.

Each clone gets its own MongoDB database named `clone_<id>`. Its `users` and `config` collections are isolated from other clones.

## Permissions

- **Owner:** clone creation, clone management, clone restart/deletion, and all clone features.
- **Admins:** clone-bot admin features such as broadcast, stats, shortener, batch and link generation, but no clone management.

## Commands

Manager:

- `/clone BOT_TOKEN` — owner only
- `/mybots` — owner only
- `/restart BOT_ID` — owner only
- `/deletebot BOT_ID` — owner only; requires two confirmations

Clone:

- `/start`
- `/genlink`
- `/batch`
- `/shortener`
- `/shortener site.com API`
- `/broadcast`
- `/stats`

## Reliability improvements

- MongoDB operations used by async handlers are moved off the event loop.
- HTTP shortener sessions are reused per bot and TLS verification remains enabled.
- Unexpected exceptions are logged with tracebacks instead of being silently swallowed.
- A watchdog automatically attempts to restart disconnected clones.
- The health endpoint reports manager health and running clone counts.

## Existing links

The link encoding/decoding format is kept compatible with the original bot. Existing links continue to work when the old bot token and its original `FILES` channel are kept on the clone.
