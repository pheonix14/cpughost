#!/usr/bin/env bash
# ==============================================================================
# CPUGhost Universal Setup Launcher
# Works across all filesystems (ext4, FAT32, exFAT, NTFS) and extracted archives.
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "${SCRIPT_DIR}/setup_zorin_host.sh" ]; then
    chmod +x "${SCRIPT_DIR}/setup_zorin_host.sh"
    exec bash "${SCRIPT_DIR}/setup_zorin_host.sh" "$@"
else
    echo "[ERR] setup_zorin_host.sh not found in ${SCRIPT_DIR}"
    exit 1
fi
