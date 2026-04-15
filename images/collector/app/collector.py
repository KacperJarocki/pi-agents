import asyncio
import os
import structlog
from datetime import datetime
from typing import Dict, Optional
from collections import defaultdict
from pathlib import Path

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)

log = structlog.get_logger()


def build_tcpdump_command(
    interface: str, pcap_file: str, packet_count: int
) -> list[str]:
    return [
        "tcpdump",
        "-i",
        interface,
        "-Z",
        "root",
        "-n",
        "-p",
        "-s",
        "96",
        "-w",
        pcap_file,
        "-c",
        str(packet_count),
    ]


def build_tshark_command(pcap_file: str) -> list[str]:
    return [
        "tshark",
        "-r",
        pcap_file,
        "-T",
        "fields",
        "-e",
        "ip.src",
        "-e",
        "ip.dst",
        "-e",
        "tcp.srcport",
        "-e",
        "tcp.dstport",
        "-e",
        "udp.srcport",
        "-e",
        "udp.dstport",
        "-e",
        "_ws.col.Protocol",
        "-e",
        "frame.len",
        "-e",
        "tcp.len",
        "-e",
        "udp.length",
        "-e",
        "eth.addr",
        "-e",
        "dns.qry.name",
        "-E",
        "separator=|",
    ]


class TrafficCollector:
    def __init__(
        self,
        db,
        interface: str,
        batch_size: int = 25,
        flush_interval: int = 5,
        capture_packet_count: int = 25,
        capture_timeout: int = 5,
    ):
        self.db = db
        self.interface = interface
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.capture_packet_count = capture_packet_count
        self.capture_timeout = capture_timeout
        self.running = False
        self.flow_buffer = []
        self.device_cache: Dict[str, int] = {}
        self._process = None
        self._capture_task: Optional[asyncio.Task] = None
        self._flush_task: Optional[asyncio.Task] = None
        self._capture_cycle = 0

    def start(self):
        self.running = True
        self._capture_task = asyncio.create_task(self._capture_loop())
        self._flush_task = asyncio.create_task(self._flush_loop())
        log.info("collector_started", interface=self.interface)

    async def stop(self):
        self.running = False

        await self._stop_process()

        for task in [self._capture_task, self._flush_task]:
            if task:
                task.cancel()
        for task in [self._capture_task, self._flush_task]:
            if task:
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        log.info("collector_stopped")

    async def _stop_process(self):
        if not self._process:
            return
        if self._process.returncode is None:
            log.info("capture_process_terminate", pid=self._process.pid)
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
                log.info(
                    "capture_process_stopped",
                    pid=self._process.pid,
                    returncode=self._process.returncode,
                )
            except TimeoutError:
                log.warning("capture_process_kill", pid=self._process.pid)
                self._process.kill()
                await self._process.wait()
                log.warning(
                    "capture_process_killed",
                    pid=self._process.pid,
                    returncode=self._process.returncode,
                )

    async def _capture_loop(self):
        while self.running:
            self._capture_cycle += 1
            pcap_file = f"/tmp/capture_{os.getpid()}_{self._capture_cycle}.pcap"
            try:
                cmd = build_tcpdump_command(
                    self.interface, pcap_file, self.capture_packet_count
                )

                log.info(
                    "starting_tcpdump",
                    command=" ".join(cmd),
                    cycle=self._capture_cycle,
                    packet_count=self.capture_packet_count,
                    timeout=self.capture_timeout,
                )

                self._process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )

                timed_out = False
                try:
                    await asyncio.wait_for(
                        self._process.wait(), timeout=self.capture_timeout
                    )
                except TimeoutError:
                    timed_out = True
                    await self._stop_process()

                stderr = ""
                if self._process.stderr:
                    stderr_bytes = await self._process.stderr.read()
                    stderr = stderr_bytes.decode(errors="replace").strip()

                log.info(
                    "tcpdump_finished",
                    cycle=self._capture_cycle,
                    timed_out=timed_out,
                    returncode=self._process.returncode,
                    stderr=stderr or None,
                )

                await self._process_pcap(pcap_file, cycle=self._capture_cycle)

            except Exception as e:
                log.error("capture_error", error=str(e), cycle=self._capture_cycle)
                await asyncio.sleep(1)

    async def _process_pcap(self, pcap_file: str, cycle: int):
        pcap_path = Path(pcap_file)
        if not pcap_path.exists():
            log.warning("pcap_missing", cycle=cycle, path=pcap_file)
            return

        size_bytes = pcap_path.stat().st_size
        log.info("pcap_file_stats", cycle=cycle, path=pcap_file, size_bytes=size_bytes)
        if size_bytes == 0:
            pcap_path.unlink(missing_ok=True)
            log.warning("pcap_empty", cycle=cycle, path=pcap_file)
            return

        try:
            cmd = build_tshark_command(pcap_file)
            log.info("starting_tshark", cycle=cycle, command=" ".join(cmd))

            result = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await result.communicate()
            stderr_text = stderr.decode(errors="replace").strip()
            lines = [
                line for line in stdout.decode(errors="replace").split("\n") if line
            ]
            log.info(
                "tshark_finished",
                cycle=cycle,
                returncode=result.returncode,
                line_count=len(lines),
                stderr=stderr_text or None,
            )
            if result.returncode != 0:
                raise RuntimeError(f"tshark failed: {stderr_text}")

            parsed_count = 0
            skipped_count = 0

            for line in lines:
                parts = line.split("|")
                if len(parts) < 6:
                    skipped_count += 1
                    continue

                flow = self._parse_flow(parts)
                if flow:
                    self.flow_buffer.append(flow)
                    parsed_count += 1
                else:
                    skipped_count += 1

            log.info(
                "pcap_processed",
                cycle=cycle,
                parsed_count=parsed_count,
                skipped_count=skipped_count,
                buffer_size=len(self.flow_buffer),
            )

            pcap_path.unlink(missing_ok=True)

        except Exception as e:
            log.error(
                "pcap_processing_error", error=str(e), cycle=cycle, path=pcap_file
            )

    def _parse_flow(self, parts: list) -> Optional[dict]:
        try:
            src_ip = parts[0] if len(parts) > 0 and parts[0] else None
            dst_ip = parts[1] if len(parts) > 1 and parts[1] else None

            if not src_ip or not dst_ip or "." not in src_ip or "." not in dst_ip:
                return None

            # tshark fields list:
            # 0 ip.src, 1 ip.dst,
            # 2 tcp.srcport, 3 tcp.dstport,
            # 4 udp.srcport, 5 udp.dstport,
            # 6 protocol, 7 frame.len, 8 tcp.len, 9 udp.length,
            # 10 eth.addr, 11 dns.qry.name
            protocol = parts[6] if len(parts) > 6 and parts[6] else "UNKNOWN"

            if protocol == "TCP":
                src_port = parts[2] if len(parts) > 2 and parts[2] else "0"
                dst_port = parts[3] if len(parts) > 3 and parts[3] else "0"
            elif protocol == "UDP":
                src_port = parts[4] if len(parts) > 4 and parts[4] else "0"
                dst_port = parts[5] if len(parts) > 5 and parts[5] else "0"
            else:
                src_port = "0"
                dst_port = "0"

            frame_len = int(parts[7]) if len(parts) > 7 and parts[7].isdigit() else 0

            mac_addr = parts[10] if len(parts) > 10 and parts[10] else None
            dns_query = parts[11] if len(parts) > 11 and parts[11] else None

            # eth.addr may contain "src_mac,dst_mac"; take the first value.
            if mac_addr and "," in mac_addr:
                mac_addr = mac_addr.split(",", 1)[0]

            return {
                "src_ip": src_ip,
                "dst_ip": dst_ip,
                "src_port": int(src_port),
                "dst_port": int(dst_port),
                "protocol": protocol.upper(),
                "bytes": frame_len,
                "mac_address": mac_addr,
                "dns_query": dns_query,
                "timestamp": datetime.utcnow(),
            }
        except Exception as e:
            log.warning("flow_parse_error", error=str(e), parts=parts[:5])
            return None

    async def _flush_loop(self):
        while self.running:
            await asyncio.sleep(self.flush_interval)
            await self._flush_buffer()

    async def _flush_buffer(self):
        if not self.flow_buffer:
            return

        flows_to_process = self.flow_buffer[: self.batch_size]
        self.flow_buffer = self.flow_buffer[self.batch_size :]

        try:
            log.info(
                "flush_started",
                requested=len(flows_to_process),
                remaining_buffer=len(self.flow_buffer),
            )
            flow_ids = await self.db.insert_flows(flows_to_process)

            aggregated = self._aggregate_flows(flows_to_process)
            await self.db.update_device_stats(aggregated)

            log.info(
                "buffer_flushed",
                flows=len(flows_to_process),
                inserted=len(flow_ids),
                aggregated=len(aggregated),
            )
        except Exception as e:
            log.error("flush_error", error=str(e))
            self.flow_buffer = flows_to_process + self.flow_buffer

    def _aggregate_flows(self, flows: list) -> Dict[str, dict]:
        aggregated = defaultdict(
            lambda: {
                "total_bytes": 0,
                "packet_count": 0,
                "connections": set(),
                "dst_ips": set(),
                "dst_ports": set(),
            }
        )

        for flow in flows:
            src = flow["src_ip"]
            aggregated[src]["total_bytes"] += flow.get("bytes", 0)
            aggregated[src]["packet_count"] += 1
            aggregated[src]["connections"].add(flow["dst_ip"])
            aggregated[src]["dst_ips"].add(flow["dst_ip"])
            aggregated[src]["dst_ports"].add(flow["dst_port"])

        return aggregated
