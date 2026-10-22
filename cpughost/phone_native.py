"""
CPUGhost Native Phone Mirror & Control Engine (Zero scrcpy dependency).
High-performance hardware-accelerated wireless screen mirror and touch controller.
Connects over Wi-Fi Hotspot (10.176.124.51:5555) or USB with zero wires needed next time.
"""

import os
import sys
import time
import math
import queue
import shutil
import socket
import struct
import threading
import subprocess
from typing import Optional, Tuple
import pygame
from rich.console import Console
from .config import load_config, save_config, get_local_ip, get_default_gateway_ip, get_arp_neighbors

console = Console()


def detect_phone_serial(target_ip: Optional[str] = None) -> Optional[str]:
    """
    Detect phone target over wireless hotspot (port 5555) or USB.
    Auto-arms wireless TCP/IP so next time wires are never needed.
    """
    adb_bin = shutil.which("adb")
    if not adb_bin:
        console.print("[ERR] adb not found. Install with: sudo apt install adb")
        return None

    # 1. Check existing connected devices
    try:
        res = subprocess.run([adb_bin, "devices", "-l"], capture_output=True, text=True, timeout=3.0)
        lines = [l.strip() for l in res.stdout.splitlines()[1:] if l.strip()]
        for line in lines:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serial = parts[0]
                if ":" in serial:
                    # Already wireless
                    return serial
                else:
                    # USB detected: auto-arm TCP/IP 5555 for wireless
                    console.print(f"[PHONE] Detected USB phone {serial}. Arming wireless ADB on port 5555...")
                    subprocess.run([adb_bin, "-s", serial, "tcpip", "5555"], capture_output=True, timeout=4.0)
                    time.sleep(1.0)
    except Exception:
        pass

    # 2. Determine target phone IP on hotspot
    phone_ip = target_ip
    if not phone_ip:
        gw = get_default_gateway_ip()
        local_ip = get_local_ip()
        if gw and gw != "0.0.0.0" and gw != local_ip and not gw.startswith("127."):
            phone_ip = gw
        else:
            neighbors = get_arp_neighbors()
            for n in neighbors:
                if n != local_ip:
                    phone_ip = n
                    break

    if not phone_ip:
        cfg = load_config()
        phone_ip = cfg.get("phone_ip", "10.176.124.51")

    # 3. Connect to phone wirelessly over port 5555
    target = f"{phone_ip}:5555"
    console.print(f"[PHONE] Connecting wirelessly to phone at {target}...")
    try:
        c_res = subprocess.run([adb_bin, "connect", target], capture_output=True, text=True, timeout=5.0)
        out = c_res.stdout.lower() + c_res.stderr.lower()
        if "connected" in out or "already connected" in out:
            console.print(f"[OK] Wireless connection active: {target}")
            cfg = load_config()
            cfg["phone_ip"] = phone_ip
            save_config(cfg)
            return target
    except Exception as e:
        console.print(f"[WARN] Connect: {e}")

    # Fallback to any active device
    try:
        res = subprocess.run([adb_bin, "devices"], capture_output=True, text=True, timeout=2.0)
        for line in res.stdout.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                return parts[0]
    except Exception:
        pass

    return target


class GhostNativePhoneMirror:
    """
    Pure native CPUGhost phone screen mirror & interactive controller.
    Runs completely in Python/Pygame with zero external dependencies like scrcpy.
    """

    def __init__(self, serial: str, phone_name: str = "Caesar"):
        self.serial = serial
        self.phone_name = phone_name
        self.running = False
        self.phone_w = 1080
        self.phone_h = 2340

        # Query physical resolution
        self._detect_resolution()

        # Window dimensions preserving natural phone aspect ratio
        self.win_h = 880
        aspect = self.phone_w / max(1, self.phone_h)
        self.win_w = max(360, int(self.win_h * aspect))

        # Stream resolution for smooth wireless transmission
        self.stream_w = 540
        self.stream_h = int(self.stream_w * (self.phone_h / self.phone_w))
        # Ensure even numbers for video encoder
        self.stream_w = self.stream_w - (self.stream_w % 2)
        self.stream_h = self.stream_h - (self.stream_h % 2)

        self.frame_queue = queue.Queue(maxsize=2)
        self.input_queue = queue.Queue()
        self.fps = 0
        self.frame_count = 0
        self.last_fps_time = time.time()

        # Touch tracking
        self.mouse_down = False
        self.drag_start = (0, 0)
        self.drag_start_time = 0.0

    def _detect_resolution(self):
        try:
            res = subprocess.run(
                ["adb", "-s", self.serial, "shell", "wm", "size"],
                capture_output=True, text=True, timeout=3.0
            )
            for line in res.stdout.splitlines():
                if "size:" in line.lower():
                    dim = line.split(":")[-1].strip()
                    if "x" in dim:
                        w, h = dim.split("x")
                        self.phone_w = int(w.strip())
                        self.phone_h = int(h.strip())
                        break
        except Exception:
            self.phone_w = 1080
            self.phone_h = 2340

    def _ensure_free_mouse(self):
        """Guarantee mouse cursor is never trapped, locked, or hidden."""
        try:
            pygame.mouse.set_visible(True)
        except Exception:
            pass
        try:
            pygame.event.set_grab(False)
        except Exception:
            pass

    def _input_worker(self):
        """Execute ADB touch, gesture, and keyboard commands asynchronously."""
        while self.running:
            try:
                cmd_args = self.input_queue.get(timeout=0.1)
                subprocess.run(
                    ["adb", "-s", self.serial, "shell"] + cmd_args,
                    capture_output=True,
                    timeout=1.5
                )
            except queue.Empty:
                continue
            except Exception:
                pass

    def _stream_worker(self):
        """
        Stream live H.264 video from Android hardware encoder directly to raw RGB frames.
        Automatically recovers and restarts seamlessly when a stream segment completes.
        """
        frame_bytes = self.stream_w * self.stream_h * 3

        while self.running:
            adb_cmd = [
                "adb", "-s", self.serial, "exec-out",
                "screenrecord",
                "--output-format=h264",
                f"--size={self.stream_w}x{self.stream_h}",
                "--bit-rate=4000000",
                "--time-limit=180",
                "-"
            ]
            ffmpeg_cmd = [
                "ffmpeg", "-loglevel", "quiet",
                "-f", "h264", "-i", "pipe:0",
                "-an", "-sn",
                "-c:v", "rawvideo", "-pix_fmt", "rgb24",
                "-f", "rawvideo", "pipe:1"
            ]

            p_adb = None
            p_ff = None
            try:
                p_adb = subprocess.Popen(adb_cmd, stdout=subprocess.PIPE)
                p_ff = subprocess.Popen(ffmpeg_cmd, stdin=p_adb.stdout, stdout=subprocess.PIPE)
                p_adb.stdout.close()

                while self.running:
                    raw = p_ff.stdout.read(frame_bytes)
                    if len(raw) != frame_bytes:
                        break

                    if self.frame_queue.full():
                        try:
                            self.frame_queue.get_nowait()
                        except queue.Empty:
                            pass
                    self.frame_queue.put(raw)

            except Exception:
                pass
            finally:
                if p_ff:
                    try:
                        p_ff.terminate()
                    except Exception:
                        pass
                if p_adb:
                    try:
                        p_adb.terminate()
                    except Exception:
                        pass
                time.sleep(0.5)

    def start(self):
        console.print(f"[PHONE] Starting CPUGhost Native Mirror for {self.phone_name} ({self.serial})...")
        console.print(f"[PHONE] Resolution: {self.phone_w}x{self.phone_h} (Wireless Hotspot)")

        self.running = True

        # Input worker thread
        in_t = threading.Thread(target=self._input_worker, daemon=True)
        in_t.start()

        # Video stream worker thread
        st_t = threading.Thread(target=self._stream_worker, daemon=True)
        st_t.start()

        self._run_gui()

    def _run_gui(self):
        pygame.init()
        win_title = f"CPUGhost Phone - {self.phone_name} [{self.serial}]"
        pygame.display.set_caption(win_title)
        screen = pygame.display.set_mode((self.win_w, self.win_h), pygame.RESIZABLE)
        clock = pygame.time.Clock()

        self._ensure_free_mouse()

        current_surface = None
        font = pygame.font.SysFont("monospace", 13)

        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    break

                elif event.type == pygame.VIDEORESIZE:
                    self.win_w, self.win_h = event.w, event.h
                    screen = pygame.display.set_mode((self.win_w, self.win_h), pygame.RESIZABLE)
                    self._ensure_free_mouse()

                elif event.type == pygame.MOUSEMOTION:
                    self._ensure_free_mouse()

                elif event.type == pygame.MOUSEBUTTONDOWN:
                    self._ensure_free_mouse()
                    mx, my = event.pos
                    if event.button == 1:  # Left click start
                        self.mouse_down = True
                        self.drag_start = (mx, my)
                        self.drag_start_time = time.time()
                    elif event.button == 3:  # Right click = Android BACK
                        self.input_queue.put(["input", "keyevent", "4"])
                    elif event.button == 2:  # Middle click = Android HOME
                        self.input_queue.put(["input", "keyevent", "3"])

                elif event.type == pygame.MOUSEBUTTONUP:
                    self._ensure_free_mouse()
                    if event.button == 1 and self.mouse_down:
                        self.mouse_down = False
                        mx, my = event.pos
                        sx, sy = self.drag_start
                        dist = math.hypot(mx - sx, my - sy)
                        elapsed_ms = int((time.time() - self.drag_start_time) * 1000)

                        px1 = int(sx * (self.phone_w / max(1, self.win_w)))
                        py1 = int(sy * (self.phone_h / max(1, self.win_h)))

                        if dist < 8:
                            # Single tap
                            self.input_queue.put(["input", "tap", str(px1), str(py1)])
                        else:
                            # Swipe / Drag
                            px2 = int(mx * (self.phone_w / max(1, self.win_w)))
                            py2 = int(my * (self.phone_h / max(1, self.win_h)))
                            dur = max(100, min(800, elapsed_ms))
                            self.input_queue.put(["input", "swipe", str(px1), str(py1), str(px2), str(py2), str(dur)])

                elif event.type == pygame.MOUSEWHEEL:
                    mx, my = pygame.mouse.get_pos()
                    px = int(mx * (self.phone_w / max(1, self.win_w)))
                    py = int(my * (self.phone_h / max(1, self.win_h)))
                    delta = int(self.phone_h * 0.18)

                    if event.y > 0:
                        # Scroll up -> swipe down
                        self.input_queue.put(["input", "swipe", str(px), str(py), str(px), str(py + delta), "120"])
                    else:
                        # Scroll down -> swipe up
                        self.input_queue.put(["input", "swipe", str(px), str(py), str(px), str(py - delta), "120"])

                elif event.type == pygame.KEYDOWN:
                    # Global hotkeys
                    mods = pygame.key.get_mods()
                    if (mods & pygame.KMOD_CTRL):
                        if event.key == pygame.K_h:  # Ctrl+H = Home
                            self.input_queue.put(["input", "keyevent", "3"])
                            continue
                        elif event.key == pygame.K_b:  # Ctrl+B = Back
                            self.input_queue.put(["input", "keyevent", "4"])
                            continue
                        elif event.key == pygame.K_s:  # Ctrl+S = Recent Apps
                            self.input_queue.put(["input", "keyevent", "187"])
                            continue
                        elif event.key == pygame.K_p:  # Ctrl+P = Power
                            self.input_queue.put(["input", "keyevent", "26"])
                            continue
                        elif event.key == pygame.K_UP:  # Volume Up
                            self.input_queue.put(["input", "keyevent", "24"])
                            continue
                        elif event.key == pygame.K_DOWN:  # Volume Down
                            self.input_queue.put(["input", "keyevent", "25"])
                            continue

                    # Direct Navigation Keys
                    if event.key in (pygame.K_ESCAPE, pygame.K_F4):
                        self.input_queue.put(["input", "keyevent", "4"])  # BACK
                    elif event.key in (pygame.K_HOME, pygame.K_F3):
                        self.input_queue.put(["input", "keyevent", "3"])  # HOME
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        self.input_queue.put(["input", "keyevent", "66"])  # ENTER
                    elif event.key == pygame.K_BACKSPACE:
                        self.input_queue.put(["input", "keyevent", "67"])  # BACKSPACE
                    elif event.key == pygame.K_TAB:
                        self.input_queue.put(["input", "keyevent", "61"])  # TAB
                    elif event.unicode and event.unicode.isprintable():
                        # Direct typing pass-through
                        ch = event.unicode
                        if ch == " ":
                            self.input_queue.put(["input", "text", "%s"])
                        else:
                            escaped = ch.replace("&", "\\&").replace("'", "\\'").replace('"', '\\"').replace("(", "\\(").replace(")", "\\)")
                            self.input_queue.put(["input", "text", escaped])

            # Receive incoming video frame
            try:
                raw_bytes = self.frame_queue.get_nowait()
                surf = pygame.image.frombuffer(raw_bytes, (self.stream_w, self.stream_h), "RGB")
                current_surface = pygame.transform.smoothscale(surf, (self.win_w, self.win_h))
                self.frame_count += 1
                now = time.time()
                if now - self.last_fps_time >= 1.0:
                    self.fps = self.frame_count
                    self.frame_count = 0
                    self.last_fps_time = now
            except queue.Empty:
                pass

            # Render display
            if current_surface:
                screen.blit(current_surface, (0, 0))
            else:
                screen.fill((15, 23, 42))
                title = font.render(f"Connecting to {self.phone_name}...", True, (0, 215, 230))
                sub = font.render("Receiving hardware screen stream...", True, (148, 163, 184))
                screen.blit(title, (20, self.win_h // 2 - 20))
                screen.blit(sub, (20, self.win_h // 2 + 5))

            pygame.display.flip()
            clock.tick(60)

        self.running = False
        pygame.quit()
        console.print("[PHONE] Native Phone Mirror closed.")


def run_native_phone_mirror(target_ip: Optional[str] = None, phone_name: str = "Caesar"):
    """CLI launcher for native CPUGhost phone mirror."""
    serial = detect_phone_serial(target_ip)
    if not serial:
        console.print("[ERR] Could not find phone. Ensure hotspot or USB debugging is active.")
        return

    mirror = GhostNativePhoneMirror(serial=serial, phone_name=phone_name)
    mirror.start()
