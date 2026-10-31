import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())
import logging
import time
from aiohttp import web
from bot import StoreBot
from config import BOTTOKEN, PORT, ADMINS, FORCESUB
from database import get_clones
from handlers import register
import manager as mgr
from manager import register_manager, workers, notify_owner, set_manager_client

WATCHDOG_INTERVAL = 60      # seconds between health checks
LIVENESS_TIMEOUT = 30       # seconds to wait for get_me()
MAX_FAILURE_ALERTS = 3      # stop alerting the owner after this many failed restarts
MAX_BACKOFF = 3600          # never wait more than 1 hour between restart attempts


async def health(request):
    manager_bot = request.app["manager"]
    if not manager_bot.client.is_connected:
        return web.json_response({"status": "unhealthy", "manager": False}, status=503)
    return web.json_response({
        "status": "ok",
        "manager": True,
        "clones_running": sum(1 for worker in workers.values() if worker.client.is_connected),
        "clones_total": len(workers),
    })


async def restart_manager(manager_bot):
    await manager_bot.stop()
    await manager_bot.client.start()
    # Pyrogram drops handlers on stop(); add them back or the manager goes silent.
    if not manager_bot.client.dispatcher.groups:
        register_manager(manager_bot.client)
    me = await manager_bot.client.get_me()
    manager_bot.username = me.username or me.first_name
    manager_bot.client.username = manager_bot.username
    manager_bot.client.bot_id = "manager"
    manager_bot.client.admins = ADMINS
    manager_bot.client.forcesub = FORCESUB
    set_manager_client(manager_bot.client)


async def alive(client):
    """True only if the client is connected AND answers a real API call."""
    if not client.is_connected:
        return False
    try:
        await asyncio.wait_for(client.get_me(), LIVENESS_TIMEOUT)
        return True
    except Exception:
        return False


def backoff(fails):
    return min(WATCHDOG_INTERVAL * 2 ** fails, MAX_BACKOFF)


async def watchdog(manager_bot):
    states = {}
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL)

        targets = [("manager", manager_bot, lambda: restart_manager(manager_bot))]
        targets += [(bot_id, worker, worker.restart) for bot_id, worker in list(workers.items())]

        for key in list(states):
            if key not in {t[0] for t in targets}:
                del states[key]

        for key, bot, restart in targets:
            state = states.setdefault(key, {"fails": 0, "next": 0.0})
            if await alive(bot.client):
                state["fails"] = 0
                state["next"] = 0.0
                continue
            if time.monotonic() < state["next"]:
                continue

            is_manager = key == "manager"
            name = f"@{bot.username}" if bot.username else str(key)
            LOGGER.warning("%s %s is not responding; restarting (failed attempts so far: %s)",
                           "Manager" if is_manager else "Clone", name, state["fails"])
            try:
                await restart()
                state["fails"] = 0
                state["next"] = 0.0
                if is_manager:
                    await notify_owner(f"🔄 <b>Manager Restarted</b>\n\nManager: @{bot.username}")
                elif not bot.alerted:
                    await notify_owner(f"🔄 <b>Clone Auto-Restarted</b>\n\nBot: {name}\nID: <code>{key}</code>")
            except Exception as e:
                state["fails"] += 1
                wait = backoff(state["fails"])
                state["next"] = time.monotonic() + wait
                LOGGER.exception("%s %s restart failed", "Manager" if is_manager else "Clone", name)
                if state["fails"] <= MAX_FAILURE_ALERTS:
                    label = "Manager" if is_manager else "Clone"
                    await notify_owner(
                        f"❌ <b>{label} Restart Failed</b>\n\nBot: {name}\nID: <code>{key}</code>\n"
                        f"Attempt: {state['fails']}\nNext try in: {wait}s\nError: <code>{e}</code>"
                    )


async def main():
    if not BOTTOKEN:
        raise RuntimeError("BOTTOKEN environment variable is missing")

    manager_bot = StoreBot("manager", BOTTOKEN, "manager")
    await manager_bot.client.start()
    try:
        me = await manager_bot.client.get_me()
        manager_bot.username = me.username or me.first_name
        manager_bot.client.bot_id = "manager"
        manager_bot.client.admins = ADMINS
        manager_bot.client.forcesub = FORCESUB

        set_manager_client(manager_bot.client)
        register_manager(manager_bot.client)

        LOGGER.info("Manager started @%s", manager_bot.username)
        await notify_owner(f"🟢 <b>Manager Online</b>\n\nManager: @{manager_bot.username}")

        # Start the health endpoint first so the host sees the app as alive while clones boot.
        app = web.Application()
        app["manager"] = manager_bot
        app.router.add_get("/", health)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()

        for item in await asyncio.to_thread(get_clones):
            bot_id = item["_id"]
            # Register the worker before starting it, so a clone that fails at boot
            # is still known to the watchdog and /restart and gets retried.
            worker = StoreBot(f"clone_{bot_id}", item["token"], bot_id, register)
            workers[bot_id] = worker
            try:
                await worker.start()
                if not worker.alerted:
                    await notify_owner(
                        f"🟢 <b>Clone Online</b>\n\nBot: @{worker.username or bot_id}\nID: <code>{bot_id}</code>"
                    )
            except Exception as e:
                LOGGER.exception("Clone %s failed to start", bot_id)
                await notify_owner(
                    f"❌ <b>Clone Failed To Start</b>\n\nID: <code>{bot_id}</code>\nError: <code>{e}</code>\n\n"
                    f"It will be retried automatically."
                )

        watch_task = asyncio.create_task(watchdog(manager_bot))
        try:
            await asyncio.Event().wait()
        finally:
            watch_task.cancel()
            await asyncio.gather(watch_task, return_exceptions=True)
            for worker in list(workers.values()):
                await worker.stop()
            await manager_bot.stop()
            await runner.cleanup()
    except Exception:
        await manager_bot.stop()
        raise


if __name__ == "__main__":
    asyncio.run(main())
