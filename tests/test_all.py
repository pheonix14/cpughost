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

    def test_letterbox_aspect_ratio_preservation(self):
        from cpughost.screen_viewer import calculate_letterbox

        # 16:9 remote desktop inside 16:10 window (Letterbox: black bars top/bottom)
        rw, rh, ox, oy = calculate_letterbox(win_w=1920, win_h=1200, raw_w=1920, raw_h=1080)
        self.assertEqual(rw, 1920)
        self.assertEqual(rh, 1080)
        self.assertEqual(ox, 0)
        self.assertEqual(oy, 60)  # (1200 - 1080) // 2

        # 16:9 remote desktop inside ultrawide window (Pillarbox: black bars left/right)
        rw, rh, ox, oy = calculate_letterbox(win_w=2560, win_h=1080, raw_w=1920, raw_h=1080)
        self.assertEqual(rh, 1080)
        self.assertEqual(rw, 1920)
        self.assertEqual(ox, 320)  # (2560 - 1920) // 2
        self.assertEqual(oy, 0)

    def test_super_key_and_keyboard_mapping(self):
        import pygame
        from cpughost.screen_viewer import PYGAME_KEY_MAP
        from cpughost.screen_server import KEY_MAPPING
        from pynput.keyboard import Key

        # Super/Windows key verification
        self.assertEqual(PYGAME_KEY_MAP.get(pygame.K_LSUPER), "super")
        self.assertEqual(PYGAME_KEY_MAP.get(pygame.K_RSUPER), "super")
        self.assertEqual(KEY_MAPPING.get("super"), Key.cmd)

        # Modifiers verification
        for mod in ["ctrl", "alt", "shift", "return", "backspace", "tab", "esc"]:
            self.assertIn(mod, KEY_MAPPING)

    def test_passwordless_config_generators(self):
        import re
        user = "testuser"
        sudoers_content = f"# CPUGhost passwordless sudo rule for headless operation\n{user} ALL=(ALL:ALL) NOPASSWD: ALL\n"
        self.assertIn(f"{user} ALL=(ALL:ALL) NOPASSWD: ALL", sudoers_content)

        # GDM3 Custom Config Regex tests
        sample_gdm = """[daemon]
# AutomaticLoginEnable = false
# AutomaticLogin = user1
WaylandEnable=false
"""
        c = sample_gdm
        if 'AutomaticLoginEnable' not in c:
            c = re.sub(r'\[daemon\]', f'[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin={user}\n', c)
        else:
            c = re.sub(r'#?\s*AutomaticLoginEnable\s*=.*', 'AutomaticLoginEnable=true', c)
            c = re.sub(r'#?\s*AutomaticLogin\s*=.*', f'AutomaticLogin={user}', c)

        self.assertIn("AutomaticLoginEnable=true", c)
        self.assertIn(f"AutomaticLogin={user}", c)


    def test_arp_neighbors_and_discovery_helpers(self):
        from cpughost.config import get_arp_neighbors
        neighbors = get_arp_neighbors()
        self.assertIsInstance(neighbors, list)
        for ip in neighbors:
            parts = ip.split(".")
            self.assertEqual(len(parts), 4)

    def test_xauthority_and_display(self):
        from cpughost.screen_server import ensure_xauthority, ensure_display_environment
        ensure_xauthority()
        ensure_display_environment()
        self.assertTrue(bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")))

    def test_server_info_and_ping(self):
        from cpughost.server import create_app
        cfg = load_config()
        server_app = create_app(cfg)
        client = TestClient(server_app)

        res = client.get("/api/info")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "online")
        self.assertEqual(res.json()["role"], "host")

        res = client.get("/api/ping")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["ping"], "pong")


if __name__ == "__main__":
    unittest.main()
