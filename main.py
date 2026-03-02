import asyncio
from core.engine import WalletEngine
from loguru import logger
import signal
import sys

async def main():
    """Main entry point"""
    engine = WalletEngine()
    
    # Handle signals
    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(engine.stop()))
    except (NotImplementedError, RuntimeError):
        logger.warning("Signal handlers are not available in this runtime")
    
    try:
        await engine.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        await engine.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Application error: {e}")
        sys.exit(1)
