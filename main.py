import asyncio
from bot import Manager

async def main():
    manager = Manager()
    await manager.start()
    await asyncio.Event().wait()

asyncio.run(main())
