# CPUGhost

Headless Dual-PC Hotspot Screen & Virtual Pen Drive System.

> **Free scrcpy alternative for PCs & mobile.** Connects multiple PCs together forming a huge chain like a web locally over hotspot or LAN, with headless multi-screen tabs, 60fps Caesar phone mirroring, and virtual pen drive file synchronization.

```
==================================================================
   ____ ____  _   _  ____ _   _  ___  ____ _____ 
  / ___|  _ \| | | |/ ___| | | |/ _ \/ ___|_   _|
 | |   | |_) | | | | |  _| |_| | | | \___ \ | |  
 | |___|  __/| |_| | |_| |  _  | |_| |___) || |  
  \____|_|    \___/ \____|_| |_|\___/|____/ |_|  
==================================================================
   HEADLESS DUAL-PC HOTSPOT SCREEN & VIRTUAL PEN DRIVE SYSTEM
==================================================================
```

## Overview

CPUGhost solves two common dual-PC problems when connected over a smartphone mobile hotspot:
1. **Save Monitor Cost (Headless Screen Mirroring)**: The secondary PC (ghost CPU) does not require a physical monitor. Its desktop is streamed in real time to a low-latency native window on the primary PC, with mouse and keyboard input pass-through.
2. **Virtual Pen Drive (Drive & File Exchange)**: The secondary PC's storage functions like a physical USB flash drive. Files can be pushed, pulled, listed, synced, and browsed via CLI or web UI.
3. **Zero Configuration Hotspot Discovery**: Automatically locates the other PC over mobile hotspot Wi-Fi via UDP beacons and concurrent subnet scanning.
4. **Clean CLI**: Controlled entirely through the `ghost` command with clean ASCII output (no emojis).

---

## Quick Start

### 1. Launch Interactive Dashboard
Simply type:
```bash
ghost
```

### 2. On the Headless Secondary PC (Ghost Host)
Start host services (Screen capture + Virtual Pen Drive + Beacon):
```bash
ghost host
```
Or start in the background as a daemon:
```bash
ghost host --daemon
```

### 3. On the Primary PC (Client / Viewer)
Auto-discover the host, sync the virtual pen drive, and open the screen window:
```bash
ghost client
```
Or connect directly to a specific IP address:
```bash
ghost client 10.176.124.73
```

---

## Multi-Screen & Android Phone ("Caesar") Support

CPUGhost now supports **more than 5 simultaneous screens** (Screens 1 through 8 + Caesar Phone + Panoramic View):

- **Switch Screens**: Press <kbd>F2</kbd> to cycle next screen, or press <kbd>Alt</kbd>+<kbd>1</kbd> .. <kbd>Alt</kbd>+<kbd>8</kbd> to jump directly to any screen.
- **Top Screen Tabs Bar**: Click any screen tab at the top of the viewer (`[1:Main]`, `[2:Scrn2]`, `[3:Scrn3]`, `[4:Scrn4]`, `[5:Scrn5]`, `[6:Scrn6]`, `[7:Left]`, `[8:Right]`, `[Caesar]`, `[0:All]`).
- **Toggle Screen Bar**: Press <kbd>F1</kbd> to show or hide the multi-screen tabs bar.
- **Phone Mirror & Control (Caesar)**:
  - Inside viewer: Press <kbd>F3</kbd> or click `[Caesar]` to view and control your phone!
  - High-performance popout window: Press <kbd>Shift</kbd> + <kbd>F3</kbd> or run:
    ```bash
    ghost phone              # Auto-detects Caesar via USB or Hotspot Wi-Fi
    ghost caesar             # Quick alias
    ghost phone 10.176.124.X # Direct wireless connect
    ```
- **List All Displays**:
  ```bash
  ghost screens
  ```

---

## Screen Viewer Window Controls

When the screen viewer window is open:
- **Click inside canvas**: Focuses window and locks keyboard directly to remote OS (Super/Windows key routes to Zorin, mouse cursor roams 100% free).
- **F1**: Toggle multi-screen tab bar.
- **F2**: Cycle next screen (Screens 1..8, Caesar Phone, Panoramic).
- **F3**: Toggle Caesar Phone screen (<kbd>Shift</kbd>+<kbd>F3</kbd> for 60fps scrcpy popout).
- **F4**: Send Android Home / App Switch when viewing Caesar.
- **F5**: Toggle 16:9 Landscape Mode vs Native Aspect Ratio.
- **F6**: Cycle custom Zorin mouse pointer accent colors in real time.
- **F7**: Cycle mouse pointer size (1.0x, 1.3x, 1.7x, 2.0x).
- **F8 / F9**: Zoom out / Zoom in screen canvas.
- **F10**: Reset zoom to 100% normal view.
- **F11**: Toggle Fullscreen.
- **Ctrl + Shift + Q**: Close viewer window.

---

## Mouse Pointer Color & Theme Customization

Customize the high-contrast colored cursor from the terminal:
```bash
# View available Zorin pointer colors
ghost pointer

# Set pointer color
ghost pointer cyan      # Zorin Cyan / Teal
ghost pointer green     # Zorin Emerald Green
ghost pointer orange    # Zorin Sunset Orange
ghost pointer red       # Zorin Crimson Red
ghost pointer blue      # Zorin Sky Blue
ghost pointer purple    # Zorin Violet
ghost pointer yellow    # Neon Yellow
ghost pointer pink      # Cyberpunk Pink
ghost pointer white     # Classic White

# Adjust pointer scale
ghost pointer --size 1.5
```

---

## Virtual Pen Drive Commands

- **List remote files**:
  ```bash
  ghost drive ls [path]
  ```
- **Push file or folder to ghost storage**:
  ```bash
  ghost drive push <local_file> [remote_path]
  ```
- **Pull file from ghost storage**:
  ```bash
  ghost drive pull <remote_file> [local_path]
  ```
- **Synchronize local folder with ghost storage**:
  ```bash
  ghost drive sync [local_dir]
  ```

---

## Phone Screen Mirroring (Phone -> PC Zero)

Easily mirror your Android smartphone's screen to PC Zero with low-latency hardware-accelerated video (60 fps), audio forwarding, and full mouse/keyboard pass-through.

### 1. On PC Zero:
Launch phone mirroring:
```bash
ghost phone
```
Or with custom options:
```bash
# Connect to specific phone IP
ghost phone 10.176.124.51

# Mirror with phone physical display turned off (save battery)
ghost phone --off

# Open Web Screen Stream in browser (no ADB needed)
ghost phone --web

# Interactive phone setup wizard
ghost phone --setup
```

### 2. On Your Phone (Termux):
Run CPUGhost directly on your phone:
```bash
# Display phone IP & enable port 5555 for PC Zero
ghost-phone
```

### Phone Mirror Controls on PC Zero:
| Shortcut / Key | Action on Android Phone |
| :--- | :--- |
| **Left Click & Drag** | Tap, swipe, and scroll on phone screen |
| **Right Click** or **Ctrl + B** | Android BACK button |
| **Middle Click** or **Ctrl + H** | Android HOME button |
| **Ctrl + S** | App Switcher / Recent Apps |
| **Ctrl + N** | Expand Notification shade |
| **Ctrl + P** | Turn phone screen ON / OFF |
| **Ctrl + F** | Toggle Fullscreen window |
| **Ctrl + Up / Down** | Volume Up / Down |
| **Ctrl + C / Ctrl + V** | Synchronize clipboard with phone |
| **Drag & Drop file** | Push file to phone Download folder / install APK |


---

## Network Discovery & Status

- **Scan hotspot for active CPUGhost nodes**:
  ```bash
  ghost scan
  ```
- **Show system and daemon status**:
  ```bash
  ghost status
  ```
- **Stop background daemon**:
  ```bash
  ghost stop
  ```

---

## Autostart on Boot & Zorin OS Setup

To have the secondary PC start CPUGhost automatically whenever powered on without a monitor:

### 1-Click Setup on Zorin OS:
Run the automated installer script:
```bash
./setup_zorin_host.sh
```

### Or using the CLI:
```bash
# Enable autostart on boot/login
ghost autostart enable

# Check autostart status
ghost autostart status

# Disable autostart
ghost autostart disable
```

### Headless Recommendations for Zorin OS:
1. In Zorin OS Settings -> Users, toggle **Automatic Login** to ON.
2. Connect the Zorin OS PC to your phone hotspot Wi-Fi once. It will auto-reconnect on every boot.
3. CPUGhost automatically waits for the hotspot Wi-Fi IP and initializes a headless virtual display if no physical monitor is connected.

---

## Auto-Update from GitHub

Keep CPUGhost updated directly from the GitHub repository (`pheonix14/cpughost`):

```bash
# Check if updates are available
ghost update --check

# Pull and apply latest updates (re-syncs code, venv, and binary launcher)
ghost update
```

---

## Command Reference Cheat Sheet

Display the full built-in reference card at any time:
```bash
ghost help
```

---

## Web Control Center

When host mode is active, the Web Control Center is available at:
```
http://<host-ip>:8888
```
Features drag-and-drop file transfers, file management, and live screen preview.
