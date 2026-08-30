import asyncio
import logging
from aiohttp import web
from bot import StoreBot
from config import BOTTOKEN, PORT, ADMINS, FORCESUB
from database import get_clones, migrate_clone_id
from handlers import register
from manager import register_manager, workers

LOGGER = logging.getLogger(__name__)


async def health(request):
    manager = request.app["manager"]
    if not manager.client.is_connected:
        return web.json_response({"status": "unhealthy", "manager": False}, status=503)
    return web.json_response({
        "status": "ok",
        "manager": True,
        "clones_running": sum(1 for worker in workers.values() if worker.client.is_connected),
        "clones_total": len(workers),
    })


async def restart_manager(manager):
    await manager.stop()
    await manager.client.start()
    me = await manager.client.get_me()
    manager.username = me.username or me.first_name
    manager.client.username = manager.username
    manager.client.bot_id = "manager"
    manager.client.admins = ADMINS
    manager.client.forcesub = FORCESUB


async def watchdog(manager):
    while True:
        await asyncio.sleep(60)
        if not manager.client.is_connected:
            try:
                LOGGER.warning("Manager disconnected; restarting")
                await restart_manager(manager)
            except Exception:
                LOGGER.exception("Manager restart failed")

        for bot_id, worker in list(workers.items()):
            if worker.client.is_connected:
                continue
            try:
                LOGGER.warning("Clone %s disconnected; restarting", bot_id)
                await worker.restart()
            except Exception:
                LOGGER.exception("Clone %s restart failed", bot_id)


async def migrate_legacy_clones():
    """Convert old hash IDs to Telegram usernames while preserving their DB names."""
    migrated = []
    for item in await asyncio.to_thread(get_clones):
        old_id = str(item.get("_id", ""))
        username = str(item.get("username") or "").lstrip("@").strip()
        if username and old_id == username:
            continue
        token = item.get("token")
        if not token:
            LOGGER.warning("Skipping clone %s: no token", old_id)
            continue
        resolver = StoreBot(f"migrate_{abs(hash(token))}", token, "migration")
        try:
            await resolver.client.start()
            me = await resolver.client.get_me()
            username = me.username
            if not username:
                LOGGER.warning("Skipping legacy clone %s: bot has no username", old_id)
                continue
            new_id = username.lstrip("@").strip()
            await asyncio.to_thread(migrate_clone_id, old_id, new_id, new_id)
            migrated.append((old_id, new_id))
            LOGGER.info("Migrated clone ID %s -> @%s", old_id, new_id)
        except Exception:
            LOGGER.exception("Failed to migrate clone %s", old_id)
        finally:
            try:
                if resolver.client.is_connected:
                    await resolver.client.stop()
            except Exception:
                LOGGER.exception("Failed to close migration client for %s", old_id)
    return migrated


async def main():
    if not BOTTOKEN:
        raise RuntimeError("BOTTOKEN is missing")

    manager = StoreBot("manager", BOTTOKEN, "manager")
    await manager.client.start()
    try:
        me = await manager.client.get_me()
        manager.username = me.username or me.first_name
        manager.client.bot_id = "manager"
        manager.client.admins = ADMINS
        manager.client.forcesub = FORCESUB
        register_manager(manager.client)
        LOGGER.info("Manager started @%s", manager.username)

        await migrate_legacy_clones()

        for item in await asyncio.to_thread(get_clones):
            try:
                worker = StoreBot(f"clone_{item['_id']}", item["token"], item["_id"])
                register(worker.client)
                await worker.start()
                workers[item["_id"]] = worker
            except Exception:
                LOGGER.exception("Clone %s failed to start", item.get("_id"))

        app = web.Application()
        app["manager"] = manager
        app.router.add_get("/", health)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()

        watch_task = asyncio.create_task(watchdog(manager))
        try:
            await asyncio.Event().wait()
        finally:
            watch_task.cancel()
            await asyncio.gather(watch_task, return_exceptions=True)
            for worker in list(workers.values()):
                await worker.stop()
            await manager.stop()
            await runner.cleanup()
    except Exception:
        await manager.stop()
        raise


if __name__ == "__main__":
    asyncio.run(main())
