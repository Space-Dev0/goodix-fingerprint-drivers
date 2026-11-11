#!/usr/bin/env bash
set -euo pipefail

echo "=================================================================="
echo " Goodix 27c6:581a (Xiaomi GM168) Driver Installer"
echo "=================================================================="

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "[-] Please run this script as your regular user, not with sudo directly."
    echo "    The script will prompt for sudo when installing system files."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${SCRIPT_DIR}/.build_cache"
LIBFPRINT_DIR="${WORK_DIR}/libfprint-goodix581a"
PATCH_FILE="${SCRIPT_DIR}/driver/goodix581a-sigfm.patch"
LIBFPRINT_URL="https://github.com/goodix-fp-linux-dev/libfprint.git"
LIBFPRINT_BRANCH="goodixtls"
LIBFPRINT_BASE="c343b69"

if [ ! -f "${PATCH_FILE}" ]; then
    echo "[-] Missing patch series: ${PATCH_FILE}"
    exit 1
fi

echo "[*] Installing udev rules..."
sudo cp "${SCRIPT_DIR}/udev/99-goodix-581a.rules" /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger

echo "[*] Setting up fprintd service drop-in configuration..."
sudo mkdir -p /etc/systemd/system/fprintd.service.d
sudo tee /etc/systemd/system/fprintd.service.d/sigfm.conf > /dev/null << 'EOF'
[Service]
Environment="OPENCV_NUM_THREADS=1" "TBB_MALLOC_USE_HUGE_PAGES=0"
EOF
sudo systemctl daemon-reload

echo "[*] Preparing build directory..."
mkdir -p "${WORK_DIR}"

if [ -d "${LIBFPRINT_DIR}/.git" ] &&
   [ "$(git -C "${LIBFPRINT_DIR}" remote get-url origin 2>/dev/null || true)" != "${LIBFPRINT_URL}" ]; then
    echo "[*] Replacing an existing clone from a different remote..."
    rm -rf "${LIBFPRINT_DIR}"
fi

if [ ! -d "${LIBFPRINT_DIR}/.git" ]; then
    echo "[*] Cloning goodix-fp-linux-dev/libfprint (branch: ${LIBFPRINT_BRANCH})..."
    git clone --branch "${LIBFPRINT_BRANCH}" "${LIBFPRINT_URL}" "${LIBFPRINT_DIR}"
else
    echo "[*] Refreshing existing libfprint clone..."
    if [ "$(git -C "${LIBFPRINT_DIR}" rev-parse --is-shallow-repository)" = "true" ]; then
        git -C "${LIBFPRINT_DIR}" fetch --unshallow origin
    else
        git -C "${LIBFPRINT_DIR}" fetch --prune origin
    fi
    git -C "${LIBFPRINT_DIR}" fetch origin "${LIBFPRINT_BRANCH}"
fi

cd "${LIBFPRINT_DIR}"

# Build from the known-good GoodixTLS community base plus the local patch
# series. This avoids depending on a pull-request branch and keeps repeated
# installer runs reproducible.
echo "[*] Resetting libfprint to ${LIBFPRINT_BASE}..."
git checkout --detach "${LIBFPRINT_BASE}"
git reset --hard "${LIBFPRINT_BASE}"
git clean -fdx

echo "[*] Applying Goodix 581a patch series..."
git am "${PATCH_FILE}"

echo "[*] Configuring Meson build..."
rm -rf build
meson setup build \
    --prefix=/usr \
    --sysconfdir=/etc \
    -Ddrivers=goodixtls581a \
    -Dgtk-examples=false \
    -Ddoc=false

echo "[*] Compiling driver..."
ninja -C build

echo "[*] Installing libfprint to system library path..."
sudo ninja -C build install
sudo ldconfig

# SELinux check on Fedora / RHEL
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" = "Enforcing" ]; then
    echo "[*] SELinux is active in Enforcing mode."
    echo "    Installing fprintd_hugepages policy module (read access to sysctl_vm_t files)..."
    sudo semodule -i "${SCRIPT_DIR}/selinux/fprintd_hugepages.pp"
fi

echo "[*] Restarting fprintd..."
sudo systemctl restart fprintd

echo "=================================================================="
echo " Installation Complete!"
echo " Next steps:"
echo "   Existing prints from older 581a builds must be deleted and re-enrolled:"
echo "   fprintd-delete \$USER"
echo "   1. Enroll your finger:  fprintd-enroll"
echo "   2. Verify matching:     fprintd-verify"
echo "   3. View live logs:      journalctl -u fprintd -f"
echo "=================================================================="
