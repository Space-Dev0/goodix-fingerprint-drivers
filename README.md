# Goodix 27c6:581a (Xiaomi GM168) Linux Fingerprint Driver

[![License: LGPL 2.1](https://img.shields.io/badge/License-LGPL_v2.1-blue.svg)](https://www.gnu.org/licenses/lgpl-2.1.html)
[![libfprint compatible](https://img.shields.io/badge/libfprint-v2.0-green.svg)](https://gitlab.freedesktop.org/libfprint/libfprint)
[![Hardware: 27c6:581a](https://img.shields.io/badge/Hardware-27c6%3A581a-orange.svg)](https://linux-hardware.org/?id=usb:27c6-581a)
[![Matching: SIGFM SIFT](https://img.shields.io/badge/Matching-SIGFM_SIFT-purple.svg)](#biometric-matching-engine)

A reverse-engineered Linux driver for the **Goodix 27c6:581a (Xiaomi GM168)** fingerprint sensor found in some Xiaomi laptops (e.g. Xiaomi Book Pro 14/16, RedmiBook Pro).

This project integrates directly with `libfprint` and `fprintd` to deliver seamless biometric login, lockscreen unlocking, and `sudo` authentication on Linux (GDM, SDDM, GNOME, KDE, PAM).

---

## 🌟 Key Engineering Highlights

- **Proprietary TLS 1.2 Over USB Bulk**: Implements the proprietary Goodix TLS protocol negotiation over bulk endpoints, enabling encrypted command and biometric frame transmission with stock OEM firmware.
- **Native 64×80 12-bit ADC Decoding**: Decodes packed 6-byte chunks (7,684-byte encrypted transfer) directly into native 12-bit resolution sensor frames.
- **7-tap high-pass filter**: Subtracts a separable binomial low-pass estimate to reduce slowly varying background structure; it can also attenuate ridge detail.
- **SIGFM (SIFT-based Geometric Matcher)**: Replaces traditional minutiae (NBIS / Bozorth3) with a continuous gradient orientation matcher tailored for compact ($3.25\text{ mm} \times 4.06\text{ mm}$) sensors.
- **Feature matching**: Uses CLAHE, SIFT descriptors, reciprocal ratio-filtered matches, and pairwise geometry. These settings are heuristics; the repository does not include a representative genuine/impostor dataset to validate error rates.
- **Multi-touch enrollment**: Stores up to 12 enrollment samples and uses configured score thresholds. Those thresholds have not been statistically calibrated for this sensor and user population.
- **SELinux Hardening**: Fully documented analysis and remediation for Intel TBB sysctl AVC alerts under Fedora / RHEL.

---

---

## 🚀 Quick Start (Automated Installation)

Clone this repository and run the automated installer:

```bash
git clone https://github.com/Space-Dev0/goodix-fingerprint-drivers.git
cd goodix-fingerprint-drivers
chmod +x install.sh
./install.sh
```

Once installed, enroll your finger:
```bash
# 1. Clear any old templates
fprintd-delete $USER

# 2. Enroll (12 touches: center pad, edges, and tip)
fprintd-enroll

# 3. Test verification
fprintd-verify
```

---

## 📚 Deep-Dive Technical Documentation

For developers, security researchers, and package maintainers, the architecture is documented in extensive detail:

1. **[Hardware Architecture & Reverse Engineering](docs/01_HARDWARE_AND_REVERSE_ENGINEERING.md)**
   - Sensor specifications, physical dimensions, USB descriptors, endpoint mappings, and OEM Windows driver reversing.
2. **[Cryptography & Proprietary TLS Protocol](docs/02_CRYPTO_AND_TLS_PROTOCOL.md)**
   - Neodyme CVE-2023-45866 analysis, TLS 1.2 handshake over bulk endpoints, PSK derivation, and record encapsulation.
3. **[Image Decoding & Preprocessing Pipeline](docs/03_IMAGE_PIPELINE_AND_DECODING.md)**
   - 12-bit nibble unpacking math, Mode 1 baseline vs Mode 5 scan, 7-tap separable Gaussian high-pass filter, dynamic percentile normalization.
4. **[Biometric Matching Engine: SIGFM & SIFT Tuning](docs/04_MATCHING_ENGINE_SIGFM.md)**
   - Why NIST Bozorth3 failed on 64×80 sensors, CLAHE parameter tuning, SIFT initial blur $\sigma = 1.2$, Lowe's ratio test $0.75$, geometric angle histograms, and two-tier decision logic.
5. **[System Integration, Concurrency & SELinux Hardening](docs/05_SYSTEM_INTEGRATION_AND_SELINUX.md)**
   - Concurrency SSM state flow, Intel TBB sysctl AVC analysis, and the scope of the Fedora/RHEL policy workaround.
6. **[Build & Installation Guide](docs/06_BUILD_AND_INSTALLATION_GUIDE.md)**
   - Build instructions for Fedora, Ubuntu/Debian, and Arch Linux, PAM setup, SELinux policy installation, and diagnostics.

---

## 📁 Repository Structure

```
.
├── README.md                              # Project overview & documentation index
├── install.sh                             # Automated 1-click build & install script
├── docs/                                  # In-depth technical documentation series
│   ├── 01_HARDWARE_AND_REVERSE_ENGINEERING.md
│   ├── 02_CRYPTO_AND_TLS_PROTOCOL.md
│   ├── 03_IMAGE_PIPELINE_AND_DECODING.md
│   ├── 04_MATCHING_ENGINE_SIGFM.md
│   ├── 05_SYSTEM_INTEGRATION_AND_SELINUX.md
│   └── 06_BUILD_AND_INSTALLATION_GUIDE.md
├── driver/                                # Goodix 581a driver source & standalone patch
│   ├── goodix581a.c                       # C driver implementation (SSM, FDT, Mode 1/5)
│   ├── goodix581a.h                       # Device registers, 256B AFE config, FDT tables
│   ├── goodix581a-sigfm.patch             # Eight-commit git-am series for the GoodixTLS community base
│   └── sigfm/                             # SIGFM SIFT matcher module (C++)
│       ├── sigfm.cpp
│       ├── sigfm.hpp
│       ├── binary.hpp
│       └── img-info.hpp
├── tools/                                 # Reverse engineering & diagnostic Python scripts
│   ├── capture_finger_xiaomi.py           # Standalone USB Mode 5 frame capture
│   ├── live_view.py                       # Real-time visual sensor frame inspector
│   ├── interactive_capture_581a.py        # Interactive test harness
│   └── probe_581a.py                      # Device USB descriptor & protocol prober
└── udev/                                  # Hardware permission rules
    └── 99-goodix-581a.rules
```

---

## 🛠️ Standalone Upstream Fork

If you prefer building directly from the full `libfprint` repository:

```bash
git clone -b goodix581a https://github.com/Space-Dev0/libfprint.git
cd libfprint
meson setup build -Ddrivers=goodixtls581a
ninja -C build
sudo ninja -C build install
```

---

## ⚖️ License & Acknowledgments

- **Driver Code**: Licensed under the GNU Lesser General Public License v2.1 ([LGPL-2.1](https://www.gnu.org/licenses/lgpl-2.1.html)) in accordance with the `libfprint` project.
- **Protocol Research**: Special thanks to the researchers at [Neodyme](https://neodyme.io/) for their foundational security analysis of the Goodix TLS protocol.
- **Upstream Project**: Built upon [libfprint](https://gitlab.freedesktop.org/libfprint/libfprint) and the [goodix-fp-linux-dev](https://github.com/goodix-fp-linux-dev/libfprint) community work.
