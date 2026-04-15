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
    batch_size = int(os.getenv("BATCH_SIZE", "25"))
    flush_interval = int(os.getenv("FLUSH_INTERVAL", "5"))
    capture_packet_count = int(os.getenv("CAPTURE_PACKET_COUNT", str(batch_size)))
    capture_timeout = int(os.getenv("CAPTURE_TIMEOUT", str(flush_interval)))
    lan_subnet_cidr = os.getenv("LAN_SUBNET_CIDR", "192.168.50.0/24")
    lease_file_path = os.getenv("LEASE_FILE_PATH", "/gateway-state/dnsmasq.leases")
    
    log.info("starting_collector", 
             interface=interface, 
             database=database_path,
             batch_size=batch_size,
             flush_interval=flush_interval,
             capture_packet_count=capture_packet_count,
             capture_timeout=capture_timeout,
             lan_subnet_cidr=lan_subnet_cidr,
             lease_file_path=lease_file_path)
    
    db = Database(database_path)
    await db.init()

    if os.getenv("ENABLE_METRICS", "false").lower() == "true":
        start_http_server(int(os.getenv("METRICS_PORT", "9090")))
        log.info("collector_metrics_enabled", port=int(os.getenv("METRICS_PORT", "9090")))
    else:
        log.info("collector_metrics_disabled")

    collector = TrafficCollector(
        db=db,
        interface=interface,
        batch_size=batch_size,
        flush_interval=flush_interval,
        capture_packet_count=capture_packet_count,
        capture_timeout=capture_timeout,
        lan_subnet_cidr=lan_subnet_cidr,
        lease_file_path=lease_file_path,
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
