# Build & Installation Guide

This guide details how to build, install, configure, and use the **Goodix 27c6:581a (Xiaomi GM168)** fingerprint driver on modern Linux distributions.

---

## 1. Prerequisites & Dependencies

### 1.1 Fedora / RHEL

```bash
sudo dnf install -y \
    gcc gcc-c++ meson ninja-build git \
    libusb1-devel gusb-devel glib2-devel pixman-devel \
    openssl-devel opencv-devel nss-devel \
    polkit-devel systemd-devel gobject-introspection-devel \
    fprintd fprintd-pam policycoreutils-python-utils
```

### 1.2 Ubuntu / Debian (22.04 LTS / 24.04 LTS)

```bash
sudo apt update && sudo apt install -y \
    build-essential meson ninja-build git \
    libusb-1.0-0-dev libgusb-dev libglib2.0-dev libpixman-1-dev \
    libssl-dev libopencv-dev libnss3-dev \
    libpolkit-gobject-1-dev libsystemd-dev libgirepository1.0-dev \
    fprintd libpam-fprintd
```

### 1.3 Arch Linux

```bash
sudo pacman -S --needed \
    base-devel meson ninja git \
    libusb libgusb glib2 pixman \
    openssl opencv nss \
    polkit systemd gobject-introspection \
    fprintd
```

---

## 2. Compiling & Installing the Driver

### Option A: Using the Automated Install Script

```bash
chmod +x install.sh
./install.sh
```

### Option B: Building from Source Manually

```bash
# 1. Clone the repository with the goodix581a branch
git clone -b goodix581a https://github.com/Space-Dev0/libfprint.git
cd libfprint

# 2. Configure the Meson build
meson setup build \
    --prefix=/usr \
    --sysconfdir=/etc \
    -Ddrivers=goodixtls581a \
    -Dgtk-examples=false \
    -Ddoc=false

# 3. Compile with Ninja
ninja -C build

# 4. Install into system library path
sudo ninja -C build install
sudo ldconfig
```

---

## 3. Hardware Permission & Service Setup

### 3.1 Install the udev Rule

The udev rule grants system biometric services access to the USB device:

```bash
sudo cp udev/99-goodix-581a.rules /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger
```

### 3.2 Configure the fprintd Systemd Drop-In

```bash
sudo mkdir -p /etc/systemd/system/fprintd.service.d
sudo tee /etc/systemd/system/fprintd.service.d/sigfm.conf << 'EOF'
[Service]
Environment="OPENCV_NUM_THREADS=1" "TBB_MALLOC_USE_HUGE_PAGES=0"
EOF

sudo systemctl daemon-reload
sudo systemctl restart fprintd
```

### 3.3 SELinux policy module (Fedora / RHEL)

The installer loads the repository's `fprintd_hugepages` policy module when
SELinux is Enforcing. To install it manually:

```bash
sudo semodule -i selinux/fprintd_hugepages.pp
```

The module allows `fprintd_t` to read files labeled `sysctl_vm_t`, which
includes the TBB `nr_hugepages` probe and other VM sysctl files. Review
`selinux/fprintd_hugepages.te` before installing if that access is too broad
for your system's policy requirements. Avoid generating a module from every
recent `fprintd` AVC, since that may authorize unrelated denials.

---

## 4. Enrollment & Verification Walkthrough

### 4.1 Delete Existing Fingerprints

If you have previously enrolled prints with the stock driver or an older threshold:

```bash
fprintd-delete $USER
```

### 4.2 Enroll Your Finger (12 Touches)

```bash
fprintd-enroll
```

> **Enrollment Technique Tips**:
> - Touch 1–4: Place the center of your finger pad flat and firm on the power button.
> - Touch 5–8: Place the left edge and right edge of your finger at a slight tilt (30°–45°).
> - Touch 9–12: Place the tip of your finger, lower knuckle, and slight rotational angles.
> - **Lift your finger between each prompt** until the terminal indicates `Enroll result: enroll-stage-passed`.

### 4.3 Test Verification

```bash
fprintd-verify
```
Touch the sensor with your enrolled finger. You should see:
```
Using device /net/reactivated/Fprint/Device/0
Listing enrolled fingers:
 - #0: right-index-finger
Verify result: verify-match (done)
```

Test with a **different (unregistered) finger** to confirm that it is rejected:
```
Verify result: verify-no-match (done)
```

---

## 5. Enabling System Authentication (PAM)

### 5.1 Fedora / RHEL
```bash
sudo authselect enable-feature with-fingerprint
sudo authselect apply-changes
```

### 5.2 Ubuntu / Debian
```bash
sudo pam-auth-update --enable fingerprint
```

### 5.3 Arch Linux / Manual PAM
Add the following line to `/etc/pam.d/system-auth` and `/etc/pam.d/sudo` directly above the `pam_unix.so` auth line:
```pam
auth        sufficient    pam_fprintd.so
```

---

## 6. Troubleshooting & Diagnostics

### 6.1 View Live Driver Logs
To monitor keypoints, raw match scores, and hardware interrupts in real-time:
```bash
journalctl -u fprintd -f
```

### 6.2 Check if Device is Detected
```bash
lsusb -d 27c6:581a
fprintd-list $USER
```

### 6.3 Restart the Daemon
`fprintd` exits automatically after 30 seconds of inactivity. To force a clean reload:
```bash
sudo systemctl restart fprintd
```
