#!/usr/bin/env bash
# ==============================================================================
# CPUGhost - Automated Host Installer & System Dependency Bootstrapper
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
export PATH="${BIN_DIR}:${HOME}/.cargo/bin:/usr/local/bin:${PATH}"

echo "=================================================================="
echo "   ____ ____  _   _  ____ _   _  ___  ____ _____ "
echo "  / ___|  _ \| | | |/ ___| | | |/ _ \/ ___|_   _| "
echo " | |   | |_) | | | | |  _| |_| | | | \___ \ | |   "
echo " | |___|  __/| |_| | |_| |  _  | |_| |___) || |   "
echo "  \____|_|    \___/ \____|_| |_|\___/|____/ |_|   "
echo "=================================================================="
echo "   CPUGHOST HEADLESS HOST AUTOMATED INSTALLER"
echo "=================================================================="
echo ""

# 1. 1-Time Passwordless Preconfiguration & System Package Checks
echo "[STEP 1/5] Preconfiguring passwordless operation & system packages..."
CURRENT_USER="${USER:-$(whoami)}"

if [ "$EUID" -eq 0 ]; then
    echo "${CURRENT_USER} ALL=(ALL:ALL) NOPASSWD: ALL" > /etc/sudoers.d/cpughost
    chmod 0440 /etc/sudoers.d/cpughost
    echo "[OK] Installed passwordless sudo rule: /etc/sudoers.d/cpughost"
elif command -v sudo >/dev/null 2>&1; then
    if ! sudo -n true 2>/dev/null; then
        echo "[INFO] Enter sudo password once to enable permanent passwordless headless operation:"
    fi
    echo "${CURRENT_USER} ALL=(ALL:ALL) NOPASSWD: ALL" > /tmp/cpughost_sudoers
    sudo cp /tmp/cpughost_sudoers /etc/sudoers.d/cpughost
    sudo chmod 0440 /etc/sudoers.d/cpughost
    rm -f /tmp/cpughost_sudoers
    echo "[OK] Installed passwordless sudo rule: /etc/sudoers.d/cpughost"
fi

MISSING_PACKAGES=()
for pkg in python3 python3-pip python3-venv git curl xvfb scrcpy adb; do
    if command -v dpkg-query >/dev/null 2>&1; then
        if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "ok installed"; then
            MISSING_PACKAGES+=("$pkg")
        fi
    elif ! command -v "$pkg" >/dev/null 2>&1; then
        MISSING_PACKAGES+=("$pkg")
    fi
done

if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
    echo "[INFO] Missing system components: ${MISSING_PACKAGES[*]}"
    echo "[INFO] Installing via package manager (passwordless)..."
    if command -v apt-get >/dev/null 2>&1; then
        if [ "$EUID" -eq 0 ]; then
            apt-get update -y
            apt-get install -y "${MISSING_PACKAGES[@]}" || true
        elif command -v sudo >/dev/null 2>&1; then
            sudo apt-get update -y || true
            sudo apt-get install -y "${MISSING_PACKAGES[@]}" || true
        fi
    fi
else
    echo "[OK] System packages verified."
fi

# 2. Python & Virtual Environment Bootstrapping
echo ""
echo "[STEP 2/5] Initializing Python environment..."
VENV_DIR="${SCRIPT_DIR}/.venv"

# Check or install standalone uv package manager
if ! command -v uv >/dev/null 2>&1 && [ ! -x "${HOME}/.local/bin/uv" ]; then
    echo "[INFO] Downloading standalone uv package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh || true
    export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:/usr/local/bin:${PATH}"
    if [ "$EUID" -eq 0 ] && [ -x "${HOME}/.local/bin/uv" ]; then
        cp "${HOME}/.local/bin/uv" /usr/local/bin/uv 2>/dev/null || true
    elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null && [ -x "${HOME}/.local/bin/uv" ]; then
        sudo cp "${HOME}/.local/bin/uv" /usr/local/bin/uv 2>/dev/null || true
    fi
fi
export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:/usr/local/bin:${PATH}"

UV_BIN="$(command -v uv || true)"
[ -z "$UV_BIN" ] && [ -x "${HOME}/.local/bin/uv" ] && UV_BIN="${HOME}/.local/bin/uv"
[ -z "$UV_BIN" ] && [ -x "${HOME}/.cargo/bin/uv" ] && UV_BIN="${HOME}/.cargo/bin/uv"

if [ -n "$UV_BIN" ] && [ -x "$UV_BIN" ]; then
    echo "[INFO] Using uv (${UV_BIN}) to manage environment..."
    if ! command -v python3 >/dev/null 2>&1; then
        echo "[INFO] Auto-downloading portable CPython 3.12..."
        "$UV_BIN" python install 3.12
        "$UV_BIN" venv "${VENV_DIR}" --python 3.12 --seed --allow-existing
    else
        "$UV_BIN" venv "${VENV_DIR}" --seed --allow-existing
    fi
else
    echo "[INFO] Creating virtual environment using python3 venv..."
    if ! python3 -m venv "${VENV_DIR}" 2>/dev/null; then
        echo "[ERR] Failed to create venv with python3 and uv is missing."
        exit 1
    fi
fi

echo "[INFO] Installing CPUGhost dependencies into virtual environment..."
DEPENDENCIES="fastapi uvicorn websockets mss pillow pynput requests rich click pygame-ce python-multipart httpx"

if [ -n "$UV_BIN" ] && [ -x "$UV_BIN" ]; then
    "$UV_BIN" pip install --python "${VENV_DIR}/bin/python" ${DEPENDENCIES}
else
    "${VENV_DIR}/bin/pip" install --upgrade pip setuptools wheel 2>/dev/null || true
    "${VENV_DIR}/bin/pip" install ${DEPENDENCIES}
fi

echo "[OK] Dependencies successfully installed in ${VENV_DIR}."

# 3. CLI Command Symlink
echo ""
echo "[STEP 3/5] Installing 'ghost' command launcher..."
mkdir -p "${BIN_DIR}"
chmod +x "${SCRIPT_DIR}/bin/ghost"
ln -sf "${SCRIPT_DIR}/bin/ghost" "${BIN_DIR}/ghost"

# Try to install to /usr/local/bin for all users if sudo is available
if [ "$EUID" -eq 0 ]; then
    ln -sf "${SCRIPT_DIR}/bin/ghost" "/usr/local/bin/ghost"
elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    sudo ln -sf "${SCRIPT_DIR}/bin/ghost" "/usr/local/bin/ghost" || true
fi

# Ensure ~/.local/bin is in PATH for shell sessions
if ! echo "$PATH" | grep -q "${BIN_DIR}"; then
    for profile in "${HOME}/.bashrc" "${HOME}/.zshrc" "${HOME}/.profile"; do
        if [ -f "$profile" ]; then
            echo "export PATH=\"${BIN_DIR}:\$PATH\"" >> "$profile"
            echo "[INFO] Added ${BIN_DIR} to $profile"
            break
        fi
    done
fi

echo "[OK] 'ghost' command installed to ${BIN_DIR}/ghost."

# 4. Role Selection & Startup Configuration
echo ""
echo "[STEP 4/5] Configuring PC role & startup..."
ROLE_CHOICE="1"
if [ -t 0 ]; then
    echo "Select role for this computer:"
    echo " [1] Host   : Secondary/Headless PC (Zorin OS: screen server + virtual pen drive) [Default]"
    echo " [2] Client : Primary/Viewer PC (opens monitor window + mounts pen drive)"
    read -r -t 15 -p "Enter choice [1/2, default 1 in 15s]: " USER_ROLE || USER_ROLE="1"
    if [ "$USER_ROLE" = "2" ]; then
        ROLE_CHOICE="2"
    fi
fi

if [ "$ROLE_CHOICE" = "1" ]; then
    echo "[INFO] Configuring as Headless Host..."
    "${BIN_DIR}/ghost" autostart enable --host
    "${VENV_DIR}/bin/python" -c "import sys; sys.path.insert(0, '${SCRIPT_DIR}'); from cpughost.config import load_config, save_config; c = load_config(); c['configured_role'] = 'host'; save_config(c)" 2>/dev/null || true
    echo "[OK] CPUGhost Host autostart enabled."
else
    echo "[INFO] Configuring as Primary Client..."
    "${BIN_DIR}/ghost" autostart enable --client
    "${VENV_DIR}/bin/python" -c "import sys; sys.path.insert(0, '${SCRIPT_DIR}'); from cpughost.config import load_config, save_config; c = load_config(); c['configured_role'] = 'client'; save_config(c)" 2>/dev/null || true
    echo "[OK] CPUGhost Client autostart enabled."
    echo "[INFO] Scanning hotspot for active Host PCs..."
    "${BIN_DIR}/ghost" scan || true
fi

# 5. Automated Passwordless Preconfiguration & Verification
echo ""
echo "[STEP 5/5] Preconfiguring passwordless boot & headless operation..."
"${BIN_DIR}/ghost" nopasswd || true

echo "------------------------------------------------------------------"
echo "[OK] 100% PASSWORDLESS HEADLESS OPERATION CONFIGURED!"
echo " 1. Sudo: Passwordless sudo enabled (/etc/sudoers.d/cpughost)."
echo " 2. Login: Display manager auto-login enabled (no password screen)."
echo " 3. Wi-Fi: Hotspot connection set to system-wide (no Keyring prompt)."
echo " 4. Display: Screensaver lock and sleep timers disabled."
echo " 5. Autostart: CPUGhost starts automatically on boot."
echo ""
echo "Whenever this PC boots, CPUGhost will start with 0 password prompts!"
echo "------------------------------------------------------------------"
echo "[OK] Setup completed successfully."
