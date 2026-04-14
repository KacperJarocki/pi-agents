import asyncio
import os
import structlog
from datetime import datetime
from typing import Dict, Optional
from collections import defaultdict

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)

log = structlog.get_logger()


class TrafficCollector:
    def __init__(self, db, interface: str, batch_size: int = 100, flush_interval: int = 5):
        self.db = db
        self.interface = interface
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.running = False
        self.flow_buffer = []
        self.device_cache: Dict[str, int] = {}
        self._process = None
        self._capture_task: Optional[asyncio.Task] = None
        self._flush_task: Optional[asyncio.Task] = None
    
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
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
    
    async def _capture_loop(self):
        pcap_file = f"/tmp/capture_{os.getpid()}.pcap"
        
        while self.running:
            try:
                cmd = [
                    "tcpdump",
                    "-i", self.interface,
                    "-n", "-p", "-l",
                    "-s", "96",
                    "-w", pcap_file,
                    "-c", "100"
                ]
                
                log.info("starting_tcpdump", command=" ".join(cmd))
                
                self._process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                
                await asyncio.sleep(self.flush_interval)
                
                if self._process.returncode is None:
                    await self._stop_process()
                
                await self._process_pcap(pcap_file)
                
            except Exception as e:
                log.error("capture_error", error=str(e))
                await asyncio.sleep(1)
    
    async def _process_pcap(self, pcap_file: str):
        if not os.path.exists(pcap_file):
            return
        
        try:
            cmd = [
                "tshark",
                "-r", pcap_file,
                "-T", "fields",
                "-e", "ip.src",
                "-e", "ip.dst",
                "-e", "tcp.srcport",
                "-e", "tcp.dstport",
                "-e", "udp.srcport",
                "-e", "udp.dstport",
                "-e", "_ws.col.Protocol",
                "-e", "frame.len",
                "-e", "tcp.len",
                "-e", "udp.length",
                "-e", "eth.addr",
                "-e", "dns.qry.name",
                "-E", "separator=|",
            ]
            
            result = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            stdout, _ = await result.communicate()
            
            for line in stdout.decode().strip().split('\n'):
                if not line:
                    continue
                
                parts = line.split('|')
                if len(parts) < 6:
                    continue
                
                flow = self._parse_flow(parts)
                if flow:
                    self.flow_buffer.append(flow)
            
            os.remove(pcap_file)
            
        except Exception as e:
            log.error("pcap_processing_error", error=str(e))
    
    def _parse_flow(self, parts: list) -> Optional[dict]:
        try:
            src_ip = parts[0] if len(parts) > 0 and parts[0] else None
            dst_ip = parts[1] if len(parts) > 1 and parts[1] else None
            
            if not src_ip or not dst_ip or '.' not in src_ip or '.' not in dst_ip:
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
        
        flows_to_process = self.flow_buffer[:self.batch_size]
        self.flow_buffer = self.flow_buffer[self.batch_size:]
        
        try:
            flows = await self.db.insert_flows(flows_to_process)
            
            aggregated = self._aggregate_flows(flows_to_process)
            await self.db.update_device_stats(aggregated)
            
            log.info("buffer_flushed", 
                    flows=len(flows_to_process),
                    aggregated=len(aggregated))
        except Exception as e:
            log.error("flush_error", error=str(e))
            self.flow_buffer = flows_to_process + self.flow_buffer
    
    def _aggregate_flows(self, flows: list) -> Dict[str, dict]:
        aggregated = defaultdict(lambda: {
            "total_bytes": 0,
            "packet_count": 0,
            "connections": set(),
            "dst_ips": set(),
            "dst_ports": set(),
        })
        
        for flow in flows:
            src = flow["src_ip"]
            aggregated[src]["total_bytes"] += flow.get("bytes", 0)
            aggregated[src]["packet_count"] += 1
            aggregated[src]["connections"].add(flow["dst_ip"])
            aggregated[src]["dst_ips"].add(flow["dst_ip"])
            aggregated[src]["dst_ports"].add(flow["dst_port"])
        
        return aggregated
