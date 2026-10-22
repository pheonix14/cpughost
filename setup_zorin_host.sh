#!/usr/bin/env bash
# ==============================================================================
# CPUGhost - Zorin OS Headless Host 1-Click Setup Script
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"

echo "=================================================================="
echo "   ____ ____  _   _  ____ _   _  ___  ____ _____ "
echo "  / ___|  _ \| | | |/ ___| | | |/ _ \/ ___|_   _| "
echo " | |   | |_) | | | | |  _| |_| | | | \___ \ | |   "
echo " | |___|  __/| |_| | |_| |  _  | |_| |___) || |   "
echo "  \____|_|    \___/ \____|_| |_|\___/|____/ |_|   "
echo "=================================================================="
echo "   ZORIN OS HEADLESS HOST AUTOMATED INSTALLER"
echo "=================================================================="
echo ""

# 1. System Package Checks
echo "[STEP 1/5] Checking system prerequisites..."
if command -v apt-get >/dev/null 2>&1; then
    echo "[INFO] Debian/Ubuntu/Zorin OS detected."
    # Install Xvfb if sudo available (optional for virtual display fallback)
    if sudo -n true 2>/dev/null; then
        echo "[INFO] Installing xvfb for headless display support..."
        sudo apt-get update -qq && sudo apt-get install -y -qq xvfb python3-venv python3-pip || true
    else
        echo "[INFO] Sudo not non-interactive. Ensure 'xvfb' is installed for virtual displays."
    fi
fi

# 2. Virtual Environment Setup
echo ""
echo "[STEP 2/5] Initializing Python virtual environment..."
VENV_DIR="${SCRIPT_DIR}/.venv"

if command -v uv >/dev/null 2>&1; then
    echo "[INFO] Using uv package manager..."
    uv venv "${VENV_DIR}"
    uv pip install --python "${VENV_DIR}/bin/python" \
        fastapi uvicorn websockets mss pillow pynput requests rich click pygame-ce python-multipart httpx
else
    echo "[INFO] Using python3 venv..."
    python3 -m venv "${VENV_DIR}"
    "${VENV_DIR}/bin/pip" install --upgrade pip
    "${VENV_DIR}/bin/pip" install \
        fastapi uvicorn websockets mss pillow pynput requests rich click pygame-ce python-multipart httpx
fi

echo "[OK] Dependencies installed successfully."

# 3. CLI Command Symlink
echo ""
echo "[STEP 3/5] Installing 'ghost' command launcher..."
mkdir -p "${BIN_DIR}"
chmod +x "${SCRIPT_DIR}/bin/ghost"
ln -sf "${SCRIPT_DIR}/bin/ghost" "${BIN_DIR}/ghost"

# Ensure ~/.local/bin is in PATH for bashrc
if ! echo "$PATH" | grep -q "${BIN_DIR}"; then
    if [ -f "${HOME}/.bashrc" ]; then
        echo "export PATH=\"${BIN_DIR}:\$PATH\"" >> "${HOME}/.bashrc"
        echo "[INFO] Added ${BIN_DIR} to ~/.bashrc"
    fi
fi

echo "[OK] 'ghost' command installed to ${BIN_DIR}/ghost."

# 4. Enable Startup on Boot
echo ""
echo "[STEP 4/5] Enabling automated startup on boot..."
"${BIN_DIR}/ghost" autostart enable
echo "[OK] CPUGhost autostart enabled."

# 5. Zorin OS Automatic Login Guide
echo ""
echo "[STEP 5/5] Verification & Headless Configuration"
echo "------------------------------------------------------------------"
echo "[IMPORTANT] For complete headless operation without a monitor:"
echo " 1. Enable Automatic Login on Zorin OS:"
echo "    - Open Zorin Menu -> Settings -> Users"
echo "    - Click 'Unlock' (enter password)"
echo "    - Toggle 'Automatic Login' to ON"
echo ""
echo " 2. Connect this PC to your phone hotspot Wi-Fi once."
echo "    Zorin OS will automatically reconnect on every reboot."
echo ""
echo " 3. Test running the host now:"
echo "    ghost host"
echo "    Or view status with:"
echo "    ghost status"
echo "------------------------------------------------------------------"
echo "[OK] Setup completed. Whenever this PC boots, CPUGhost will start!"
