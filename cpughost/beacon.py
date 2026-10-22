import time
import json
import socket
from cpughost.config import load_config, get_local_ip, get_broadcast_ip
from cpughost.discovery import BEACON_MAGIC

def run_beacon():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    cfg = load_config()
    target_ip = cfg.get("last_peer_ip", "10.176.124.231")

    while True:
        try:
            local_ip = get_local_ip()
            bcast_ip = get_broadcast_ip(local_ip)

            payload = {
                "magic": BEACON_MAGIC,
                "node_name": cfg.get("node_name", "zero"),
                "node_ip": local_ip,
                "api_port": cfg.get("api_port", 8888),
                "stream_port": cfg.get("stream_port", 8889),
                "roles": ["client", "viewer", "primary"],
                "time": time.time()
            }
            data = json.dumps(payload).encode("utf-8")
            sock.sendto(data, (bcast_ip, cfg.get("discovery_port", 8989)))
            if target_ip:
                sock.sendto(data, (target_ip, cfg.get("discovery_port", 8989)))
            try:
                sock.sendto(data, ("255.255.255.255", cfg.get("discovery_port", 8989)))
            except Exception:
                pass
        except Exception:
            pass
        time.sleep(1.5)

if __name__ == "__main__":
    run_beacon()
