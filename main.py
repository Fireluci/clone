import asyncio
import logging
from aiohttp import web
from bot import StoreBot
from config import BOTTOKEN, PORT, ADMINS, FORCESUB
from database import get_clones
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
