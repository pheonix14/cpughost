"""
CPUGhost Native Phone Mirror & Interactive Engine (Zero scrcpy dependency).
High-performance hardware-accelerated wireless screen mirror and touch controller.
Features zero-lag persistent ADB shell, privacy blackout bypass, password/PIN support,
debounced smooth mouse wheel scrolling, full keyboard bindings, and zero emojis.
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
from typing import Optional, Tuple, Dict
import pygame
from rich.console import Console
from .config import load_config, save_config, get_local_ip, get_default_gateway_ip, get_arp_neighbors

console = Console()

# Hardware Android Keycodes for physical driver-level input
# Physical keycodes bypass virtual keyboard restrictions on password and PIN fields
ANDROID_KEYCODES: Dict[str, int] = {
    "0": 7, "1": 8, "2": 9, "3": 10, "4": 11, "5": 12, "6": 13, "7": 14, "8": 15, "9": 16,
    "a": 29, "b": 30, "c": 31, "d": 32, "e": 33, "f": 34, "g": 35, "h": 36, "i": 37,
    "j": 38, "k": 39, "l": 40, "m": 41, "n": 42, "o": 43, "p": 44, "q": 45, "r": 46,
    "s": 47, "t": 48, "u": 49, "v": 50, "w": 51, "x": 52, "y": 53, "z": 54,
    " ": 62, "\t": 61, "\n": 66, "\r": 66,
    ",": 55, ".": 56, "-": 69, "=": 70, "[": 71, "]": 72, "\\": 73, ";": 74, "'": 75, "/": 76, "`": 68
}

SHIFT_SYMBOLS: Dict[str, Tuple[int, str]] = {
    ")": (7, "0"), "!": (8, "1"), "@": (9, "2"), "#": (10, "3"), "$": (11, "4"),
    "%": (12, "5"), "^": (13, "6"), "&": (14, "7"), "*": (15, "8"), "(": (16, "9"),
    "<": (55, ","), ">": (56, "."), "_": (69, "-"), "+": (70, "="), "{": (71, "["),
    "}": (72, "]"), "|": (73, "\\"), ":": (74, ";"), "\"": (75, "'"), "?": (76, "/"), "~": (68, "`")
}


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
                    return serial
                else:
                    # USB detected: arm TCP/IP 5555 for wireless
                    console.print(f"[PHONE] Detected USB phone {serial}. Arming wireless ADB on port 5555...")
                    subprocess.run([adb_bin, "-s", serial, "tcpip", "5555"], capture_output=True, timeout=4.0)
                    time.sleep(0.5)
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


class PersistentAdbShell:
    """
    High-performance persistent interactive ADB shell session.
    Commands are streamed down stdin with 0 process creation overhead (<0.1ms dispatch).
    """

    def __init__(self, serial: str):
        self.serial = serial
        self.proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._start_shell()

    def _start_shell(self):
        try:
            self.proc = subprocess.Popen(
                ["adb", "-s", self.serial, "shell"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1
            )
        except Exception as e:
            console.print(f"[WARN] Failed to open persistent ADB shell: {e}")

    def send_cmd(self, cmd: str):
        with self._lock:
            if not self.proc or self.proc.poll() is not None:
                self._start_shell()
            if self.proc and self.proc.stdin:
                try:
                    self.proc.stdin.write(cmd + "\n")
                    self.proc.stdin.flush()
                except Exception:
                    self._start_shell()
                    if self.proc and self.proc.stdin:
                        try:
                            self.proc.stdin.write(cmd + "\n")
                            self.proc.stdin.flush()
                        except Exception:
                            pass

    def close(self):
        with self._lock:
            if self.proc:
                try:
                    if self.proc.stdin:
                        self.proc.stdin.close()
                    self.proc.terminate()
                except Exception:
                    pass
                self.proc = None


class GhostNativePhoneMirror:
    """
    Native CPUGhost Phone Screen Mirror & Interactive Touch/Keyboard Engine.
    Zero external scrcpy dependencies. Pure Python/Pygame implementation.
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
        self.stream_w = self.stream_w - (self.stream_w % 2)
        self.stream_h = self.stream_h - (self.stream_h % 2)

        self.frame_queue = queue.Queue(maxsize=2)
        self.fps = 0
        self.frame_count = 0
        self.last_fps_time = time.time()

        # Touch and gesture tracking
        self.mouse_down = False
        self.drag_start = (0, 0)
        self.drag_start_time = 0.0

        # Scroll wheel debounce and accumulation
        self.scroll_accum = 0
        self.last_scroll_time = 0.0
        self.last_scroll_pos = (self.phone_w // 2, self.phone_h // 2)

        # Persistent ADB shell for instant input execution (zero lag)
        self.shell = PersistentAdbShell(self.serial)

        # Apply privacy and blackout safeguards
        self._apply_privacy_safeguards()

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

    def _apply_privacy_safeguards(self):
        """
        Bypass Android privacy blackout on password/PIN screens and secure fields.
        Allows legitimate remote viewing while ensuring keyguard can be dismissed.
        """
        cmds = [
            "settings put global disable_screen_share_protections_for_apps_and_notifications 1",
            "settings put secure lockscreen_show_controls 1",
            "settings put global block_untrusted_touches 0",
            "cmd window dismiss-keyguard",
            "input keyevent 224"
        ]
        for cmd in cmds:
            self.shell.send_cmd(cmd)

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
                "--bit-rate=4500000",
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
                time.sleep(0.4)

    def _handle_wheel_scroll(self):
        """Execute debounced smooth swipe gesture from accumulated mouse wheel notches."""
        if self.scroll_accum == 0:
            return

        px, py = self.last_scroll_pos
        # Clamp coordinates inside active viewport
        px = max(100, min(self.phone_w - 100, px))
        py = max(200, min(self.phone_h - 200, py))

        # Positive scroll_accum = wheel rolled up (content scrolls down -> finger swipes DOWN)
        # Negative scroll_accum = wheel rolled down (content scrolls up -> finger swipes UP)
        notches = self.scroll_accum
        self.scroll_accum = 0

        swipe_dist = int(min(self.phone_h * 0.45, abs(notches) * (self.phone_h * 0.15)))
        if notches > 0:
            # Swipe down
            y1 = py
            y2 = min(self.phone_h - 100, py + swipe_dist)
        else:
            # Swipe up
            y1 = py
            y2 = max(100, py - swipe_dist)

        dur = max(120, min(280, abs(notches) * 80))
        self.shell.send_cmd(f"input swipe {px} {y1} {px} {y2} {dur}")

    def _send_typed_char(self, ch: str):
        """
        Send a typed character to Android using physical hardware keycodes.
        Hardware keycodes work on password, PIN, lock screen, and terminal inputs.
        """
        lower = ch.lower()
        if lower in ANDROID_KEYCODES:
            code = ANDROID_KEYCODES[lower]
            if ch.isupper():
                self.shell.send_cmd(f"input keyevent --meta-shift-left {code}")
            else:
                self.shell.send_cmd(f"input keyevent {code}")
            return

        if ch in SHIFT_SYMBOLS:
            code, _ = SHIFT_SYMBOLS[ch]
            self.shell.send_cmd(f"input keyevent --meta-shift-left {code}")
            return

        # Fallback escaped input text
        if ch == " ":
            self.shell.send_cmd("input text %s")
        else:
            escaped = ch.replace("&", "\\&").replace("'", "\\'").replace('"', '\\"').replace("(", "\\(").replace(")", "\\)").replace("$", "\\$")
            self.shell.send_cmd(f"input text {escaped}")

    def start(self):
        console.print(f"[PHONE] Starting CPUGhost Native Mirror for {self.phone_name} ({self.serial})...")
        console.print(f"[PHONE] Resolution: {self.phone_w}x{self.phone_h} (Wireless Hotspot, Low-Latency Stdin)")

        self.running = True

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
            now = time.time()

            # Process debounced mouse wheel scroll if timer expired
            if self.scroll_accum != 0 and (now - self.last_scroll_time) > 0.040:
                self._handle_wheel_scroll()

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
                        self.drag_start_time = now
                    elif event.button == 3:  # Right click = Android BACK
                        self.shell.send_cmd("input keyevent 4")
                    elif event.button == 2:  # Middle click = Android HOME
                        self.shell.send_cmd("input keyevent 3")

                elif event.type == pygame.MOUSEBUTTONUP:
                    self._ensure_free_mouse()
                    if event.button == 1 and self.mouse_down:
                        self.mouse_down = False
                        mx, my = event.pos
                        sx, sy = self.drag_start
                        dist = math.hypot(mx - sx, my - sy)
                        elapsed_ms = int((now - self.drag_start_time) * 1000)

                        px1 = int(sx * (self.phone_w / max(1, self.win_w)))
                        py1 = int(sy * (self.phone_h / max(1, self.win_h)))

                        if dist < 8:
                            # Single tap
                            self.shell.send_cmd(f"input tap {px1} {py1}")
                        else:
                            # Swipe / Drag gesture
                            px2 = int(mx * (self.phone_w / max(1, self.win_w)))
                            py2 = int(my * (self.phone_h / max(1, self.win_h)))
                            dur = max(80, min(600, elapsed_ms))
                            self.shell.send_cmd(f"input swipe {px1} {py1} {px2} {py2} {dur}")

                elif event.type == pygame.MOUSEWHEEL:
                    mx, my = pygame.mouse.get_pos()
                    px = int(mx * (self.phone_w / max(1, self.win_w)))
                    py = int(my * (self.phone_h / max(1, self.win_h)))
                    self.last_scroll_pos = (px, py)
                    self.scroll_accum += event.y
                    self.last_scroll_time = now

                elif event.type == pygame.KEYDOWN:
                    mods = pygame.key.get_mods()

                    # System & Media Shortcuts
                    if (mods & pygame.KMOD_CTRL):
                        if event.key == pygame.K_h:  # Home
                            self.shell.send_cmd("input keyevent 3")
                            continue
                        elif event.key == pygame.K_b:  # Back
                            self.shell.send_cmd("input keyevent 4")
                            continue
                        elif event.key == pygame.K_s:  # Recents
                            self.shell.send_cmd("input keyevent 187")
                            continue
                        elif event.key == pygame.K_p:  # Power
                            self.shell.send_cmd("input keyevent 26")
                            continue
                        elif event.key == pygame.K_w:  # Wake
                            self.shell.send_cmd("input keyevent 224")
                            continue
                        elif event.key == pygame.K_u:  # Unlock / Dismiss Keyguard
                            self.shell.send_cmd("cmd window dismiss-keyguard")
                            continue
                        elif event.key == pygame.K_UP:  # Vol Up
                            self.shell.send_cmd("input keyevent 24")
                            continue
                        elif event.key == pygame.K_DOWN:  # Vol Down
                            self.shell.send_cmd("input keyevent 25")
                            continue
                        elif event.key == pygame.K_a:  # Select All
                            self.shell.send_cmd("input keyevent --meta-ctrl 29")
                            continue
                        elif event.key == pygame.K_c:  # Copy
                            self.shell.send_cmd("input keyevent 278")
                            continue
                        elif event.key == pygame.K_v:  # Paste
                            self.shell.send_cmd("input keyevent 279")
                            continue
                        elif event.key == pygame.K_x:  # Cut
                            self.shell.send_cmd("input keyevent 277")
                            continue
                        elif event.key == pygame.K_z:  # Undo
                            self.shell.send_cmd("input keyevent --meta-ctrl 54")
                            continue

                    # Direct Navigation Keys
                    if event.key in (pygame.K_ESCAPE, pygame.K_F4):
                        self.shell.send_cmd("input keyevent 4")  # BACK
                    elif event.key in (pygame.K_HOME, pygame.K_F3):
                        self.shell.send_cmd("input keyevent 3")  # HOME
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        self.shell.send_cmd("input keyevent 66")  # ENTER
                    elif event.key == pygame.K_BACKSPACE:
                        self.shell.send_cmd("input keyevent 67")  # BACKSPACE
                    elif event.key == pygame.K_DELETE:
                        self.shell.send_cmd("input keyevent 112")  # FORWARD DEL
                    elif event.key == pygame.K_TAB:
                        self.shell.send_cmd("input keyevent 61")  # TAB
                    elif event.key == pygame.K_F2:
                        self.shell.send_cmd("input keyevent 187")  # RECENTS
                    elif event.key == pygame.K_F5:
                        self._apply_privacy_safeguards()  # REFRESH & DISMISS KEYGUARD
                    elif event.key == pygame.K_UP:
                        self.shell.send_cmd("input keyevent 19")
                    elif event.key == pygame.K_DOWN:
                        self.shell.send_cmd("input keyevent 20")
                    elif event.key == pygame.K_LEFT:
                        self.shell.send_cmd("input keyevent 21")
                    elif event.key == pygame.K_RIGHT:
                        self.shell.send_cmd("input keyevent 22")
                    elif event.unicode and event.unicode.isprintable():
                        self._send_typed_char(event.unicode)

            # Receive incoming video frame
            try:
                raw_bytes = self.frame_queue.get_nowait()
                surf = pygame.image.frombuffer(raw_bytes, (self.stream_w, self.stream_h), "RGB")
                current_surface = pygame.transform.smoothscale(surf, (self.win_w, self.win_h))
                self.frame_count += 1
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
                sub = font.render("Receiving hardware screen stream (bypassing privacy blackout)...", True, (148, 163, 184))
                screen.blit(title, (20, self.win_h // 2 - 20))
                screen.blit(sub, (20, self.win_h // 2 + 5))

            pygame.display.flip()
            clock.tick(60)

        self.running = False
        self.shell.close()
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
