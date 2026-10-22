import os
import io
import time
import json
import shutil
import asyncio
import threading
import subprocess
from pathlib import Path
from typing import Set, Optional, Tuple
import mss
from PIL import Image
from pynput.mouse import Controller as MouseController, Button as MouseButton
from pynput.keyboard import Controller as KeyboardController, Key
import websockets
from rich.console import Console

from .config import load_config

console = Console()

KEY_MAPPING = {
    "super": Key.cmd,
    "Key.cmd": Key.cmd,
    "Key.cmd_l": Key.cmd,
    "Key.cmd_r": Key.cmd_r,
    "left meta": Key.cmd,
    "right meta": Key.cmd_r,
    "left super": Key.cmd,
    "right super": Key.cmd_r,
    "meta": Key.cmd,
    "windows": Key.cmd,
    "win": Key.cmd,
    "ctrl": Key.ctrl,
    "Key.ctrl": Key.ctrl,
    "left ctrl": Key.ctrl,
    "right ctrl": Key.ctrl_r,
    "alt": Key.alt,
    "Key.alt": Key.alt,
    "left alt": Key.alt,
    "right alt": Key.alt_r,
    "shift": Key.shift,
    "Key.shift": Key.shift,
    "left shift": Key.shift,
    "right shift": Key.shift_r,
    "return": Key.enter,
    "enter": Key.enter,
    "Key.enter": Key.enter,
    "backspace": Key.backspace,
    "Key.backspace": Key.backspace,
    "tab": Key.tab,
    "Key.tab": Key.tab,
    "esc": Key.esc,
    "escape": Key.esc,
    "Key.esc": Key.esc,
    "space": Key.space,
    "Key.space": Key.space,
    "delete": Key.delete,
    "Key.delete": Key.delete,
    "insert": Key.insert,
    "Key.insert": Key.insert,
    "up": Key.up,
    "Key.up": Key.up,
    "down": Key.down,
    "Key.down": Key.down,
    "left": Key.left,
    "Key.left": Key.left,
    "right": Key.right,
    "Key.right": Key.right,
    "home": Key.home,
    "Key.home": Key.home,
    "end": Key.end,
    "Key.end": Key.end,
    "page up": Key.page_up,
    "page down": Key.page_down,
    "caps lock": Key.caps_lock,
    "caps_lock": Key.caps_lock,
    "num lock": Key.num_lock,
    "scroll lock": Key.scroll_lock,
}
for f in range(1, 13):
    if hasattr(Key, f"f{f}"):
        KEY_MAPPING[f"f{f}"] = getattr(Key, f"f{f}")
KEY_MAP = KEY_MAPPING

KEYSYM_MAP = {
    "super": 0xffeb,
    "Key.cmd": 0xffeb,
    "Key.cmd_l": 0xffeb,
    "Key.cmd_r": 0xffec,
    "left meta": 0xffeb,
    "right meta": 0xffec,
    "left super": 0xffeb,
    "right super": 0xffec,
    "meta": 0xffeb,
    "windows": 0xffeb,
    "win": 0xffeb,
    "ctrl": 0xffe3,
    "Key.ctrl": 0xffe3,
    "left ctrl": 0xffe3,
    "right ctrl": 0xffe4,
    "alt": 0xffe9,
    "Key.alt": 0xffe9,
    "left alt": 0xffe9,
    "right alt": 0xffea,
    "shift": 0xffe1,
    "Key.shift": 0xffe1,
    "left shift": 0xffe1,
    "right shift": 0xffe2,
    "return": 0xff0d,
    "enter": 0xff0d,
    "Key.enter": 0xff0d,
    "backspace": 0xff08,
    "Key.backspace": 0xff08,
    "tab": 0xff09,
    "Key.tab": 0xff09,
    "esc": 0xff1b,
    "escape": 0xff1b,
    "Key.esc": 0xff1b,
    "space": 0x0020,
    "Key.space": 0x0020,
    "delete": 0xffff,
    "Key.delete": 0xffff,
    "insert": 0xff63,
    "Key.insert": 0xff63,
    "home": 0xff50,
    "Key.home": 0xff50,
    "end": 0xff57,
    "Key.end": 0xff57,
    "page up": 0xff55,
    "page down": 0xff56,
    "up": 0xff52,
    "Key.up": 0xff52,
    "down": 0xff54,
    "Key.down": 0xff54,
    "left": 0xff51,
    "Key.left": 0xff51,
    "right": 0xff53,
    "Key.right": 0xff53,
}
for f in range(1, 13):
    KEYSYM_MAP[f"f{f}"] = 0xffbe + f - 1


def ensure_xauthority():
    """Ensure XAUTHORITY is set to an active authority file for X11/Xwayland."""
    if os.environ.get("XAUTHORITY") and os.path.exists(os.environ["XAUTHORITY"]):
        return

    uid = os.getuid()
    run_dir = Path(f"/run/user/{uid}")
    candidates = []
    if run_dir.exists():
        candidates.extend(sorted(run_dir.glob(".mutter-Xwaylandauth.*"), key=lambda p: p.stat().st_mtime, reverse=True))
        candidates.extend(sorted(run_dir.glob("*xauth*"), key=lambda p: p.stat().st_mtime, reverse=True))
        candidates.extend(sorted(run_dir.glob(".Xauthority*"), key=lambda p: p.stat().st_mtime, reverse=True))
    candidates.append(Path.home() / ".Xauthority")
    candidates.append(Path("/tmp/.X11-unix/Xauthority"))

    for c in candidates:
        if c.exists() and c.is_file():
            os.environ["XAUTHORITY"] = str(c)
            return


def ensure_display_environment(resolution: str = "1920x1080"):
    """Ensure a valid X11 or Wayland display environment variable exists."""
    ensure_xauthority()

    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        for num in range(5):
            sock = f"/tmp/.X11-unix/X{num}"
            if os.path.exists(sock):
                os.environ["DISPLAY"] = f":{num}"
                console.print(f"[SCREEN] Attached to detected display: :{num}")
                return

        xvfb_bin = shutil.which("Xvfb")
        if xvfb_bin:
            try:
                res_spec = f"{resolution}x24"
                subprocess.Popen(
                    [xvfb_bin, ":99", "-screen", "0", res_spec, "-nocursor"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=os.setpgrp
                )
                time.sleep(0.5)
                os.environ["DISPLAY"] = ":99"
                console.print(f"[SCREEN] Spawned virtual monitor on display :99 ({resolution} Xvfb)")
                return
            except Exception:
                pass

        os.environ["DISPLAY"] = ":0"
    elif not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = ":0"


class WaylandScreenCapture:
    """
    Direct hardware-accelerated screen capture and input injection on Wayland (GNOME/Mutter).
    Pulls video frames via Mutter ScreenCast D-Bus and PipeWire without black screen.
    Uses linked Mutter RemoteDesktop for compositor-level global mouse/click/keyboard injection.
    """
    def __init__(self, quality: int = 75, scale: float = 1.0):
        self.quality = quality
        self.scale = scale
        self.bus = None
        self.rd_session_path = None
        self.session_id = None
        self.sc_session_path = None
        self.stream_path = None
        self.pipewire_node = None
        self.pipeline = None
        self.sink = None
        self.width = 1440
        self.height = 900
        self.active = False
        self.latest_frame: Optional[bytes] = None
        self.frame_id: int = 0
        self.lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._init_stream()

    def _init_stream(self):
        try:
            import gi
            gi.require_version("Gio", "2.0")
            gi.require_version("Gst", "1.0")
            from gi.repository import Gio, GLib, Gst
            Gst.init(None)

            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

            # 1. Try to create Mutter RemoteDesktop session for input injection
            try:
                rd_res = self.bus.call_sync(
                    "org.gnome.Mutter.RemoteDesktop",
                    "/org/gnome/Mutter/RemoteDesktop",
                    "org.gnome.Mutter.RemoteDesktop",
                    "CreateSession",
                    None, None, Gio.DBusCallFlags.NONE, -1, None
                )
                self.rd_session_path = rd_res.unpack()[0]
                sid_prop = self.bus.call_sync(
                    "org.gnome.Mutter.RemoteDesktop",
                    self.rd_session_path,
                    "org.freedesktop.DBus.Properties",
                    "Get",
                    GLib.Variant("(ss)", ("org.gnome.Mutter.RemoteDesktop.Session", "SessionId")),
                    None, Gio.DBusCallFlags.NONE, -1, None
                )
                self.session_id = sid_prop.unpack()[0]
            except Exception as e:
                self.rd_session_path = None
                self.session_id = None

            # 2. Create Mutter ScreenCast session (link RemoteDesktop if available)
            sc_opts = {"remote-desktop-session-id": GLib.Variant("s", self.session_id)} if self.session_id else {}
            res = self.bus.call_sync(
                "org.gnome.Mutter.ScreenCast",
                "/org/gnome/Mutter/ScreenCast",
                "org.gnome.Mutter.ScreenCast",
                "CreateSession",
                GLib.Variant("(a{sv})", (sc_opts,)),
                None, Gio.DBusCallFlags.NONE, -1, None
            )
            self.sc_session_path = res.unpack()[0]

            # 3. Record primary monitor
            res_stream = self.bus.call_sync(
                "org.gnome.Mutter.ScreenCast",
                self.sc_session_path,
                "org.gnome.Mutter.ScreenCast.Session",
                "RecordMonitor",
                GLib.Variant("(sa{sv})", ("", {})),
                None, Gio.DBusCallFlags.NONE, -1, None
            )
            self.stream_path = res_stream.unpack()[0]

            def on_signal(conn, sender_name, object_path, interface_name, signal_name, parameters, user_data):
                if signal_name == "PipeWireStreamAdded":
                    self.pipewire_node = parameters.unpack()[0]

            self.bus.signal_subscribe(
                "org.gnome.Mutter.ScreenCast",
                "org.gnome.Mutter.ScreenCast.Stream",
                "PipeWireStreamAdded",
                self.stream_path,
                None, Gio.DBusSignalFlags.NONE,
                on_signal, None
            )

            # Start sessions
            if self.rd_session_path:
                self.bus.call_sync(
                    "org.gnome.Mutter.RemoteDesktop",
                    self.rd_session_path,
                    "org.gnome.Mutter.RemoteDesktop.Session",
                    "Start",
                    None, None, Gio.DBusCallFlags.NONE, -1, None
                )
            else:
                self.bus.call_sync(
                    "org.gnome.Mutter.ScreenCast",
                    self.sc_session_path,
                    "org.gnome.Mutter.ScreenCast.Session",
                    "Start",
                    None, None, Gio.DBusCallFlags.NONE, -1, None
                )

            # Wait briefly for PipeWireStreamAdded signal
            loop = GLib.MainLoop()
            def check_node():
                if self.pipewire_node is not None:
                    loop.quit()
                    return False
                return True
            GLib.timeout_add(20, check_node)
            GLib.timeout_add(2000, loop.quit)
            loop.run()

            if not self.pipewire_node:
                self.close()
                return

            pipeline_str = (
                f"pipewiresrc path={self.pipewire_node} ! videoconvert n-threads=2 ! "
                f"jpegenc quality={self.quality} idct-method=ifast ! "
                f"appsink name=sink emit-signals=True max-buffers=1 drop=True sync=False"
            )
            self.pipeline = Gst.parse_launch(pipeline_str)
            self.sink = self.pipeline.get_by_name("sink")
            self.pipeline.set_state(Gst.State.PLAYING)
            self.active = True

            self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
            self._reader_thread.start()

        except Exception as e:
            console.print(f"[SCREEN] Wayland stream error: {e}")
            self.active = False
            self.close()

    def _reader_loop(self):
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        while self.active and self.sink:
            try:
                sample = self.sink.emit("pull-sample")
                if sample:
                    buf = sample.get_buffer()
                    caps = sample.get_caps()
                    if caps and caps.get_size() > 0:
                        s = caps.get_structure(0)
                        if s.has_field("width") and s.has_field("height"):
                            self.width = s.get_value("width")
                            self.height = s.get_value("height")
                    success, map_info = buf.map(Gst.MapFlags.READ)
                    if success:
                        data = bytes(map_info.data)
                        buf.unmap(map_info)
                        with self.lock:
                            self.latest_frame = data
                            self.frame_id += 1
                else:
                    time.sleep(0.005)
            except Exception:
                time.sleep(0.01)

    def get_latest_frame(self) -> Tuple[int, Optional[bytes]]:
        with self.lock:
            return self.frame_id, self.latest_frame

    def notify_pointer_motion_absolute(self, x: float, y: float) -> bool:
        if not self.bus or not self.rd_session_path or not self.stream_path:
            return False
        from gi.repository import GLib, Gio
        try:
            self.bus.call_sync(
                "org.gnome.Mutter.RemoteDesktop",
                self.rd_session_path,
                "org.gnome.Mutter.RemoteDesktop.Session",
                "NotifyPointerMotionAbsolute",
                GLib.Variant("(sdd)", (self.stream_path, float(x), float(y))),
                None, Gio.DBusCallFlags.NONE, -1, None
            )
            return True
        except Exception:
            return False

    def notify_pointer_button(self, button_code: int, state: bool) -> bool:
        if not self.bus or not self.rd_session_path:
            return False
        from gi.repository import GLib, Gio
        try:
            self.bus.call_sync(
                "org.gnome.Mutter.RemoteDesktop",
                self.rd_session_path,
                "org.gnome.Mutter.RemoteDesktop.Session",
                "NotifyPointerButton",
                GLib.Variant("(ib)", (int(button_code), bool(state))),
                None, Gio.DBusCallFlags.NONE, -1, None
            )
            return True
        except Exception:
            return False

    def notify_pointer_axis(self, dx: float, dy: float) -> bool:
        if not self.bus or not self.rd_session_path:
            return False
        from gi.repository import GLib, Gio
        try:
            self.bus.call_sync(
                "org.gnome.Mutter.RemoteDesktop",
                self.rd_session_path,
                "org.gnome.Mutter.RemoteDesktop.Session",
                "NotifyPointerAxis",
                GLib.Variant("(ddu)", (float(dx), float(dy), 0)),
                None, Gio.DBusCallFlags.NONE, -1, None
            )
            return True
        except Exception:
            return False

    def notify_keyboard_keysym(self, keysym: int, state: bool) -> bool:
        if not self.bus or not self.rd_session_path:
            return False
        from gi.repository import GLib, Gio
        try:
            self.bus.call_sync(
                "org.gnome.Mutter.RemoteDesktop",
                self.rd_session_path,
                "org.gnome.Mutter.RemoteDesktop.Session",
                "NotifyKeyboardKeysym",
                GLib.Variant("(ub)", (int(keysym), bool(state))),
                None, Gio.DBusCallFlags.NONE, -1, None
            )
            return True
        except Exception:
            return False

    def notify_keyboard_keycode(self, keycode: int, state: bool) -> bool:
        if not self.bus or not self.rd_session_path:
            return False
        from gi.repository import GLib, Gio
        try:
            self.bus.call_sync(
                "org.gnome.Mutter.RemoteDesktop",
                self.rd_session_path,
                "org.gnome.Mutter.RemoteDesktop.Session",
                "NotifyKeyboardKeycode",
                GLib.Variant("(ub)", (int(keycode), bool(state))),
                None, Gio.DBusCallFlags.NONE, -1, None
            )
            return True
        except Exception:
            return False

    def close(self):
        self.active = False
        try:
            if self.pipeline:
                import gi
                gi.require_version("Gst", "1.0")
                from gi.repository import Gst
                self.pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass

        try:
            if self.bus and self.rd_session_path:
                from gi.repository import Gio
                self.bus.call_sync(
                    "org.gnome.Mutter.RemoteDesktop",
                    self.rd_session_path,
                    "org.gnome.Mutter.RemoteDesktop.Session",
                    "Stop",
                    None, None, Gio.DBusCallFlags.NONE, -1, None
                )
        except Exception:
            pass

        try:
            if self.bus and self.sc_session_path:
                from gi.repository import Gio
                self.bus.call_sync(
                    "org.gnome.Mutter.ScreenCast",
                    self.sc_session_path,
                    "org.gnome.Mutter.ScreenCast.Session",
                    "Stop",
                    None, None, Gio.DBusCallFlags.NONE, -1, None
                )
        except Exception:
            pass


class GhostScreenServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8889, fps: int = 30, quality: int = 75, scale: float = 1.0):
        self.host = host
        self.port = port
        self.fps = fps
        self.quality = quality
        self.scale = scale
        self.running = False
        self.clients: Set[websockets.WebSocketServerProtocol] = set()

        self.mouse: Optional[MouseController] = None
        self.keyboard: Optional[KeyboardController] = None
        self.wayland_cap: Optional[WaylandScreenCapture] = None
        self.sct: Optional[mss.mss] = None
        self.monitor = None
        self.active_screen_id: int = 1
        self.screens: Dict[int, dict] = {}
        self.screen_names: Dict[int, str] = {}
        self.phone_controller = None

        self._init_input_controllers()
        self._init_screen_capture()

    def _init_input_controllers(self):
        try:
            self.mouse = MouseController()
            self.keyboard = KeyboardController()
        except Exception:
            self.mouse = None
            self.keyboard = None

    def _refresh_screens_catalog(self):
        """
        Enumerate physical monitors and construct virtual workspaces up to 8+ screens.
        Also registers Screen 9 for Android Phone ('Caesar').
        """
        self.screens = {}
        self.screen_names = {}

        if self.sct and self.sct.monitors:
            total = len(self.sct.monitors)
            if total > 1:
                self.screens[0] = self.sct.monitors[0]
                self.screen_names[0] = f"All Screens ({self.sct.monitors[0]['width']}x{self.sct.monitors[0]['height']})"
                for i in range(1, total):
                    self.screens[i] = self.sct.monitors[i]
                    self.screen_names[i] = f"Monitor {i} ({self.sct.monitors[i]['width']}x{self.sct.monitors[i]['height']})"
            elif total == 1:
                self.screens[0] = self.sct.monitors[0]
                self.screens[1] = self.sct.monitors[0]
                self.screen_names[0] = f"Display 0 ({self.sct.monitors[0]['width']}x{self.sct.monitors[0]['height']})"
                self.screen_names[1] = f"Primary Display ({self.sct.monitors[0]['width']}x{self.sct.monitors[0]['height']})"

        if self.wayland_cap and self.wayland_cap.active:
            w_mon = {"left": 0, "top": 0, "width": self.wayland_cap.width, "height": self.wayland_cap.height}
            self.screens[1] = w_mon
            self.screen_names[1] = f"Wayland Primary ({w_mon['width']}x{w_mon['height']})"
            if 0 not in self.screens:
                self.screens[0] = w_mon
                self.screen_names[0] = f"All Screens ({w_mon['width']}x{w_mon['height']})"

        base_mon = self.screens.get(1, self.screens.get(0, {"left": 0, "top": 0, "width": 1920, "height": 1080}))
        bx = base_mon.get("left", 0)
        by = base_mon.get("top", 0)
        bw = base_mon.get("width", 1920)
        bh = base_mon.get("height", 1080)
        hw = max(1, bw // 2)
        hh = max(1, bh // 2)

        # Multi-Screen workspaces for indices 2 through 8 to guarantee >5 screens support
        virtual_defs = {
            2: (bx, by, hw, hh, "Screen 2: Top-Left"),
            3: (bx + hw, by, hw, hh, "Screen 3: Top-Right"),
            4: (bx, by + hh, hw, hh, "Screen 4: Bottom-Left"),
            5: (bx + hw, by + hh, hw, hh, "Screen 5: Bottom-Right"),
            6: (bx + hw // 2, by + hh // 2, hw, hh, "Screen 6: Center Focused"),
            7: (bx, by, hw, bh, "Screen 7: Left Split"),
            8: (bx + hw, by, hw, bh, "Screen 8: Right Split"),
        }
        for idx, (vx, vy, vw, vh, vname) in virtual_defs.items():
            if idx not in self.screens:
                self.screens[idx] = {"left": vx, "top": vy, "width": vw, "height": vh}
                self.screen_names[idx] = vname

        # Screen 9: Android Phone ("Caesar")
        self.screens[9] = {"left": 0, "top": 0, "width": 1080, "height": 2400, "is_phone": True}
        self.screen_names[9] = "Caesar Phone (Android)"

        if self.active_screen_id not in self.screens:
            self.active_screen_id = 1
        self.monitor = self.screens.get(self.active_screen_id, base_mon)

    def _init_screen_capture(self) -> bool:
        is_wayland = bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland")
        if is_wayland:
            try:
                self.wayland_cap = WaylandScreenCapture(quality=self.quality, scale=self.scale)
                if self.wayland_cap.active:
                    self._refresh_screens_catalog()
                    console.print(f"[SCREEN] Attached to Wayland screen ({self.wayland_cap.width}x{self.wayland_cap.height}) via Mutter ScreenCast + RemoteDesktop")
                    console.print(f"[SCREEN] Initialized Multi-Screen support (Screens 1-8 + Caesar Phone)")
                    return True
            except Exception as e:
                console.print(f"[SCREEN] Wayland stream error: {e}")
                self.wayland_cap = None

        try:
            ensure_display_environment()
            mss_factory = getattr(mss, "MSS", mss.mss)
            self.sct = mss_factory()
            self._refresh_screens_catalog()
            console.print(f"[SCREEN] Initialized Multi-Screen support: {len(self.screens)} screens available (Screens 1-8 + Caesar Phone)")
            return self.monitor is not None
        except Exception:
            self.sct = None
            self.monitor = None
            return False

    def handle_input_event(self, data: dict):
        if not self.monitor:
            if not self._init_screen_capture():
                return

        event_type = data.get("type")
        if not event_type:
            return

        # Multi-Screen Selection
        if event_type == "select_screen":
            try:
                target_id = int(data.get("screen_id", 1))
                self._refresh_screens_catalog()
                if target_id in self.screens:
                    self.active_screen_id = target_id
                    self.monitor = self.screens[target_id]
                    console.print(f"[SCREEN] Active screen switched to: [bold cyan]{self.screen_names.get(target_id, target_id)}[/bold cyan]")
            except Exception:
                pass
            return

        # Android Phone ("Caesar") Touch & Key Forwarding
        if self.active_screen_id == 9:
            try:
                if not self.phone_controller:
                    from .phone import PhoneController
                    self.phone_controller = PhoneController()
                if event_type == "mouse_down":
                    rel_x = max(0.0, min(1.0, float(data.get("x", 0.0))))
                    rel_y = max(0.0, min(1.0, float(data.get("y", 0.0))))
                    px = int(rel_x * self.monitor.get("width", 1080))
                    py = int(rel_y * self.monitor.get("height", 2400))
                    self.phone_controller.tap(px, py)
                elif event_type in ("key_down", "key_up"):
                    if event_type == "key_down":
                        raw_k = data.get("key", "")
                        if raw_k in ("esc", "Key.esc", "back"):
                            self.phone_controller.back()
                        elif raw_k in ("super", "Key.cmd", "home"):
                            self.phone_controller.home()
                        elif raw_k in ("tab", "Key.tab", "app_switch"):
                            self.phone_controller.app_switch()
                        elif len(raw_k) == 1:
                            self.phone_controller.type_text(raw_k)
            except Exception:
                pass
            return

        mon_w = self.monitor.get("width", 1920)
        mon_h = self.monitor.get("height", 1080)
        mon_left = self.monitor.get("left", 0)
        mon_top = self.monitor.get("top", 0)

        # Linux input button codes: 272 = BTN_LEFT, 273 = BTN_RIGHT, 274 = BTN_MIDDLE
        BTN_CODES = {"left": 272, "right": 273, "middle": 274}

        try:
            if event_type == "mouse_move":
                rel_x = max(0.0, min(1.0, float(data.get("x", 0.0))))
                rel_y = max(0.0, min(1.0, float(data.get("y", 0.0))))
                abs_x = int(mon_left + rel_x * mon_w)
                abs_y = int(mon_top + rel_y * mon_h)

                handled = False
                if self.wayland_cap and self.wayland_cap.active:
                    handled = self.wayland_cap.notify_pointer_motion_absolute(abs_x, abs_y)
                if not handled:
                    if not self.mouse:
                        self._init_input_controllers()
                    if self.mouse:
                        self.mouse.position = (abs_x, abs_y)

            elif event_type in ("mouse_down", "mouse_up"):
                btn_name = data.get("button", "left")
                is_down = (event_type == "mouse_down")
                btn_code = BTN_CODES.get(btn_name, 272)

                # Update position first if coordinates are provided
                if "x" in data and "y" in data:
                    rel_x = max(0.0, min(1.0, float(data["x"])))
                    rel_y = max(0.0, min(1.0, float(data["y"])))
                    abs_x = int(mon_left + rel_x * mon_w)
                    abs_y = int(mon_top + rel_y * mon_h)
                    if self.wayland_cap and self.wayland_cap.active:
                        self.wayland_cap.notify_pointer_motion_absolute(abs_x, abs_y)
                    if not self.mouse:
                        self._init_input_controllers()
                    if self.mouse:
                        self.mouse.position = (abs_x, abs_y)

                handled = False
                if self.wayland_cap and self.wayland_cap.active:
                    handled = self.wayland_cap.notify_pointer_button(btn_code, is_down)
                if not handled:
                    if not self.mouse:
                        self._init_input_controllers()
                    if self.mouse:
                        pynput_btn = MouseButton.left
                        if btn_name == "right":
                            pynput_btn = MouseButton.right
                        elif btn_name == "middle":
                            pynput_btn = MouseButton.middle
                        if is_down:
                            self.mouse.press(pynput_btn)
                        else:
                            self.mouse.release(pynput_btn)

            elif event_type == "scroll":
                dx = int(data.get("dx", 0))
                dy = int(data.get("dy", 0))
                handled = False
                if self.wayland_cap and self.wayland_cap.active:
                    handled = self.wayland_cap.notify_pointer_axis(dx, dy)
                if not handled:
                    if not self.mouse:
                        self._init_input_controllers()
                    if self.mouse:
                        self.mouse.scroll(dx, dy)

            elif event_type in ("key_down", "key_up"):
                raw_key = data.get("key", "")
                is_down = (event_type == "key_down")
                handled = False

                # 1. Try Wayland Mutter RemoteDesktop keysym / keycode
                if self.wayland_cap and self.wayland_cap.active:
                    # Special check: Super / Windows key handling (evdev 125, X11 133, keysym 0xffeb)
                    if raw_key in (
                        "super", "Key.cmd", "Key.cmd_l", "Key.cmd_r",
                        "left meta", "right meta", "left super", "right super",
                        "meta", "super_l", "super_r", "windows", "win"
                    ):
                        if hasattr(self.wayland_cap, "notify_keyboard_keycode"):
                            handled = self.wayland_cap.notify_keyboard_keycode(133, is_down)
                            if not handled:
                                handled = self.wayland_cap.notify_keyboard_keycode(125, is_down)
                        if not handled:
                            handled = self.wayland_cap.notify_keyboard_keysym(0xffeb, is_down)

                    if not handled:
                        keysym = KEYSYM_MAP.get(raw_key)
                        if keysym is None and len(raw_key) == 1:
                            keysym = ord(raw_key)
                        if keysym is not None:
                            handled = self.wayland_cap.notify_keyboard_keysym(keysym, is_down)

                # 2. Fallback to pynput
                if not handled:
                    if not self.keyboard:
                        self._init_input_controllers()
                    if self.keyboard:
                        k = KEY_MAP.get(raw_key, raw_key)
                        try:
                            if is_down:
                                self.keyboard.press(k)
                            else:
                                self.keyboard.release(k)
                        except Exception:
                            pass

        except Exception:
            pass

    async def capture_and_broadcast_loop(self):
        interval = 1.0 / max(1, self.fps)
        last_frame_id = -1
        last_send_time = 0.0

        while self.running:
            start_time = time.time()
            if self.clients:
                if not (self.wayland_cap and self.wayland_cap.active) and not (self.sct and self.monitor):
                    self._init_screen_capture()

                # 1. Android Phone Screen ("Caesar")
                if self.active_screen_id == 9:
                    try:
                        from .phone import capture_phone_frame
                        p_bytes = capture_phone_frame()
                        if p_bytes:
                            ready_clients = [
                                c for c in self.clients
                                if getattr(c, "transport", None) is None or c.transport.get_write_buffer_size() < 100_000
                            ]
                            if ready_clients:
                                websockets.broadcast(ready_clients, p_bytes)
                    except Exception:
                        pass

                # 2. Wayland screen capture (Primary)
                elif self.wayland_cap and self.wayland_cap.active and self.active_screen_id == 1:
                    frame_id, frame_bytes = self.wayland_cap.get_latest_frame()
                    now = time.time()
                    if frame_bytes and (frame_id != last_frame_id or (now - last_send_time) >= 1.0):
                        last_frame_id = frame_id
                        last_send_time = now
                        if self.monitor:
                            self.monitor["width"] = self.wayland_cap.width
                            self.monitor["height"] = self.wayland_cap.height

                        ready_clients = [
                            c for c in self.clients
                            if getattr(c, "transport", None) is None or c.transport.get_write_buffer_size() < 100_000
                        ]
                        if ready_clients:
                            websockets.broadcast(ready_clients, frame_bytes)

                # 3. MSS multi-screen capture (Screens 0 through 8)
                elif self.sct and self.monitor:
                    try:
                        sct_img = self.sct.grab(self.monitor)
                        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                        if self.scale != 1.0:
                            new_w = max(320, int(sct_img.width * self.scale))
                            new_h = max(240, int(sct_img.height * self.scale))
                            img = img.resize((new_w, new_h), Image.BILINEAR)

                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=self.quality, optimize=False)
                        frame_bytes = buf.getvalue()

                        ready_clients = [
                            c for c in self.clients
                            if getattr(c, "transport", None) is None or c.transport.get_write_buffer_size() < 100_000
                        ]
                        if ready_clients:
                            websockets.broadcast(ready_clients, frame_bytes)
                    except Exception:
                        self.sct = None
                        self.monitor = None

            elapsed = time.time() - start_time
            sleep_time = max(0.001, interval - elapsed)
            await asyncio.sleep(sleep_time)

    async def start(self):
        self.running = True
        console.print(f"[SCREEN] Starting Screen Server on ws://{self.host}:{self.port}...")

        async def handler(websocket):
            self.clients.add(websocket)
            console.print(f"[SCREEN] Viewer connected from {websocket.remote_address}")
            try:
                async for message in websocket:
                    try:
                        event_data = json.loads(message)
                        self.handle_input_event(event_data)
                    except Exception:
                        pass
            finally:
                self.clients.discard(websocket)
                console.print("[SCREEN] Viewer disconnected.")

        server = await websockets.serve(
            handler,
            self.host,
            self.port,
            max_size=10 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20
        )
        await asyncio.gather(server.wait_closed(), self.capture_and_broadcast_loop())

    def stop(self):
        self.running = False
        if self.wayland_cap:
            self.wayland_cap.close()
