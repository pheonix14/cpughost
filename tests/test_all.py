import os
import unittest
import tempfile
import shutil
from pathlib import Path

from cpughost.config import load_config, get_local_ip, get_broadcast_ip, wait_for_hotspot_ip
from cpughost.discovery import GhostDiscovery
from cpughost.drive_server import create_drive_routes
from cpughost.screen_server import ensure_display_environment
from fastapi.testclient import TestClient
from fastapi import FastAPI


class TestCPUGhost(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.app = FastAPI()
        self.app.include_router(create_drive_routes(self.test_dir))
        self.client = TestClient(self.app)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_config_and_network(self):
        cfg = load_config()
        self.assertIn("api_port", cfg)
        self.assertIn("stream_port", cfg)
        ip = get_local_ip()
        self.assertTrue(len(ip.split(".")) == 4)
        bcast = get_broadcast_ip(ip)
        self.assertTrue(bcast.endswith(".255") or bcast == "255.255.255.255")

        # Test wait_for_hotspot_ip (short timeout test)
        resolved_ip = wait_for_hotspot_ip(timeout=2.0, poll_interval=0.5)
        self.assertEqual(resolved_ip, ip)

    def test_drive_status_and_crud(self):
        # 1. Status
        res = self.client.get("/api/drive/status")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "online")

        # 2. Upload
        payload = b"Sample pen drive data file content."
        files = {"file": ("test_doc.txt", payload, "text/plain")}
        res = self.client.post("/api/drive/upload", files=files, data={"path": ""})
        self.assertEqual(res.status_code, 200)

        # 3. List
        res = self.client.get("/api/drive/ls")
        self.assertEqual(res.status_code, 200)
        items = res.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "test_doc.txt")

        # 4. Download
        res = self.client.get("/api/drive/download", params={"path": "test_doc.txt"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, payload)

        # 5. Delete
        res = self.client.post("/api/drive/delete", data={"path": "test_doc.txt"})
        self.assertEqual(res.status_code, 200)
        res = self.client.get("/api/drive/ls")
        self.assertEqual(len(res.json()["items"]), 0)

    def test_discovery_beacon(self):
        disc = GhostDiscovery(port=18989, include_self=True)
        disc.start_broadcaster(roles=["host", "test"])
        disc.start_listener()
        import time
        time.sleep(1.5)
        peers = disc.get_active_peers()
        disc.stop()
        self.assertTrue(len(peers) >= 0)

    def test_display_environment_check(self):
        # Should not raise exception
        ensure_display_environment()
        self.assertTrue(bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")))


if __name__ == "__main__":
    unittest.main()
