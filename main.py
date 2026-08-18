import asyncio
from aiohttp import web
from bot import StoreBot
from config import BOTTOKEN, PORT, ADMINS, FORCESUB
from database import get_clones
from handlers import register
from manager import register_manager, workers


async def health(_):
    return web.Response(text="OK")


async def main():
    if not BOTTOKEN: raise RuntimeError("BOTTOKEN is missing")
    manager = StoreBot("manager", BOTTOKEN, "manager")
    await manager.client.start()
    me = await manager.client.get_me()
    manager.username = me.username or me.first_name
    manager.client.bot_id = "manager"
    manager.client.admins = ADMINS
    manager.client.forcesub = FORCESUB
    register_manager(manager.client)
    print(f"Manager started @{manager.username}")
    for item in get_clones():
        try:
            worker = StoreBot(f"clone_{item['_id']}", item["token"], item["_id"])
            register(worker.client); await worker.start(); workers[item["_id"]] = worker
        except Exception as e:
            print(f"Clone {item['_id']} failed: {e}")
    app = web.Application(); app.router.add_get("/", health)
    runner = web.AppRunner(app); await runner.setup(); await web.TCPSite(runner, "0.0.0.0", PORT).start()
    try:
        await asyncio.Event().wait()
    finally:
        for worker in list(workers.values()): await worker.stop()
        await manager.client.stop(); await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
