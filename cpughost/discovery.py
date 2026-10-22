import time
import json
import socket
import threading
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor

from .config import load_config, get_local_ip, get_broadcast_ip, get_arp_neighbors

BEACON_MAGIC = "CPUGHOST_BEACON_V1"


class GhostDiscovery:
    def __init__(self, port: int = 8989, include_self: bool = False):
        self.port = port
        self.include_self = include_self
        self.peers: Dict[str, dict] = {}
        self.lock = threading.Lock()
        self.running = False
        self._listener_thread: Optional[threading.Thread] = None
        self._broadcaster_thread: Optional[threading.Thread] = None

    def start_broadcaster(self, roles: List[str] = None):
        """
        Broadcast presence beacon on the hotspot network.
        Sends via broadcast and direct unicast to active neighbors to bypass AP isolation.
        """
        if roles is None:
            roles = ["host", "drive", "screen", "audio"]

        self.running = True

        def _broadcast_loop():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            cfg = load_config()

            while self.running:
                try:
                    local_ip = get_local_ip()
                    bcast_ip = get_broadcast_ip(local_ip)

                    payload = {
                        "magic": BEACON_MAGIC,
                        "node_name": cfg.get("node_name", "GhostNode"),
                        "node_ip": local_ip,
                        "api_port": cfg.get("api_port", 8888),
                        "stream_port": cfg.get("stream_port", 8889),
                        "audio_port": cfg.get("audio_port", 8890),
                        "roles": roles,
                        "time": time.time()
                    }
                    data = json.dumps(payload).encode("utf-8")

                    # 1. Subnet broadcast
                    try:
                        sock.sendto(data, (bcast_ip, self.port))
                    except Exception:
                        pass

                    # 2. Global broadcast as secondary
                    try:
                        sock.sendto(data, ("255.255.255.255", self.port))
                    except Exception:
                        pass

                    # 3. Direct unicast to active ARP neighbors to guarantee discovery over AP isolation
                    targets = set(get_arp_neighbors())
                    last_peer = cfg.get("last_peer_ip")
                    if last_peer and last_peer != local_ip and not last_peer.startswith("127."):
                        targets.add(last_peer)

                    for target_ip in targets:
                        try:
                            sock.sendto(data, (target_ip, self.port))
                        except Exception:
                            pass

                except Exception:
                    pass
                time.sleep(1.2)

            sock.close()

        self._broadcaster_thread = threading.Thread(target=_broadcast_loop, daemon=True)
        self._broadcaster_thread.start()

    def start_listener(self):
        """Listen for peer broadcasts on the hotspot network."""
        self.running = True

        def _listen_loop():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except AttributeError:
                pass

            try:
                sock.bind(("", self.port))
            except Exception:
                # If cannot bind, listener is disabled
                return

            sock.settimeout(1.0)
            local_ip = get_local_ip()

            while self.running:
                try:
                    data, addr = sock.recvfrom(4096)
                    info = json.loads(data.decode("utf-8"))
                    if info.get("magic") == BEACON_MAGIC:
                        sender_ip = info.get("node_ip")
                        # Fallback to physical socket source if reported IP was loopback or empty
                        if not sender_ip or sender_ip.startswith("127."):
                            sender_ip = addr[0]

                        # Ignore self unless include_self is True
                        if self.include_self or sender_ip != local_ip:
                            with self.lock:
                                self.peers[sender_ip] = {
                                    "ip": sender_ip,
                                    "node_name": info.get("node_name", "Unknown"),
                                    "api_port": info.get("api_port", 8888),
                                    "stream_port": info.get("stream_port", 8889),
                                    "roles": info.get("roles", []),
                                    "last_seen": time.time()
                                }
                except socket.timeout:
                    pass
                except Exception:
                    pass

                # Prune peers older than 10s
                with self.lock:
                    now = time.time()
                    stale = [ip for ip, p in self.peers.items() if now - p["last_seen"] > 10]
                    for ip in stale:
                        del self.peers[ip]

            sock.close()

        self._listener_thread = threading.Thread(target=_listen_loop, daemon=True)
        self._listener_thread.start()

    def get_active_peers(self) -> List[dict]:
        with self.lock:
            return list(self.peers.values())

    def find_host(self, timeout: float = 3.0) -> Optional[dict]:
        """Wait up to timeout seconds for a host beacon."""
        start = time.time()
        while time.time() - start < timeout:
            peers = self.get_active_peers()
            for p in peers:
                if "host" in p.get("roles", []):
                    return p
            time.sleep(0.3)
        return None

    def stop(self):
        self.running = False


def scan_hotspot_subnet(port: int = 8888, timeout: float = 0.4, include_self: bool = False) -> List[str]:
    """
    Fallback active scan across /24 subnet in case hotspot blocks UDP broadcast.
    Scans 254 IPs concurrently in under 1 second.
    """
    local_ip = get_local_ip()
    parts = local_ip.split(".")
    if len(parts) != 4 or parts[0] == "127":
        return []

    prefix = f"{parts[0]}.{parts[1]}.{parts[2]}."
    detected = []

    def _check_ip(ip: str):
        if not include_self and ip == local_ip:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((ip, port))
            detected.append(ip)
        except Exception:
            pass
        finally:
            sock.close()

    ips_to_test = [f"{prefix}{i}" for i in range(1, 255)]
    with ThreadPoolExecutor(max_workers=64) as pool:
        pool.map(_check_ip, ips_to_test)

    return detected
