# CPUGhost

Headless Dual-PC Hotspot Screen & Virtual Pen Drive System.

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

## Screen Viewer Window Controls

When the screen viewer window is open:
- **Ctrl + G**: Grab or release mouse & keyboard focus.
- **F11**: Toggle Fullscreen.
- **Esc** or **Ctrl + Q**: Close viewer window (when focus is released).
- All mouse clicks (left, middle, right), mouse wheel scrolls, and keyboard strokes pass directly through to the secondary PC.

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

## Web Control Center

When host mode is active, the Web Control Center is available at:
```
http://<host-ip>:8888
```
Features drag-and-drop file transfers, file management, and live screen preview.
