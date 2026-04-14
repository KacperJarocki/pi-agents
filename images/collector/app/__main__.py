import asyncio
import signal
import os
import structlog
from datetime import datetime
from prometheus_client import start_http_server

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)

log = structlog.get_logger()

from .collector import TrafficCollector
from .database import Database

collector = None


async def main():
    global collector
    
    database_path = os.getenv("DATABASE_PATH", "/data/iot-security.db")
    interface = os.getenv("INTERFACE", "wlan0")
    batch_size = int(os.getenv("BATCH_SIZE", "100"))
    flush_interval = int(os.getenv("FLUSH_INTERVAL", "5"))
    
    log.info("starting_collector", 
             interface=interface, 
             database=database_path,
             batch_size=batch_size,
             flush_interval=flush_interval)
    
    db = Database(database_path)
    await db.init()
    
    # Expose Prometheus metrics (Service expects :9090)
    start_http_server(int(os.getenv("METRICS_PORT", "9090")))

    collector = TrafficCollector(
        db=db,
        interface=interface,
        batch_size=batch_size,
        flush_interval=flush_interval
    )

    stop_event = asyncio.Event()

    def signal_handler():
        log.info("received_shutdown_signal")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        collector.start()
        await stop_event.wait()
    except Exception as e:
        log.error("collector_error", error=str(e))
        raise
    finally:
        if collector:
            await collector.stop()
        await db.close()
        log.info("collector_stopped")


if __name__ == "__main__":
    asyncio.run(main())
