# Hardware Architecture & Reverse Engineering: Goodix 27c6:581a

## 1. Device Overview

The **Goodix 27c6:581a** is a capacitive fingerprint sensor found in modern Xiaomi laptops, notably the **Xiaomi Book Pro 14 (2022)**, **Xiaomi Book Pro 16 (2022)**, and several **RedmiBook Pro** models.

```
USB Identification:
  Vendor ID:       0x27c6 (Goodix)
  Product ID:      0x581a
  Sensor Model:    GM168 (Xiaomi Custom)
  Manufacturer:    HTIX
  Product:         Goodix FingerPrint Device
```

### 1.1 Physical Characteristics & Sensor Specifications

| Metric | Value | Technical Context |
| :--- | :--- | :--- |
| **Native Pixel Grid** | $64 \times 80$ pixels | 5,120 active capacitive sensing pixels |
| **Physical Dimensions** | $\approx 3.25\text{ mm} \times 4.06\text{ mm}$ | Compact power-button / side integrated form factor |
| **Resolution** | 500 DPI ($19.685\text{ pixels/mm}$) | Approximately $50.8\ \mu\text{m}$ per pixel pitch |
| **Pixel Bit Depth** | 12-bit ADC | Dynamic range: $0$ to $4095$ counts per pixel |
| **Frame Packaging** | 7,684 bytes encrypted | 6-byte chunks encoding pairs of 12-bit pixels |

---

## 2. Chipset & USB Architecture

The 581a employs a **Match-on-Host (MOH)** architecture with an embedded microcontroller (MCU) serving as an SPI-to-USB bridge:

```
+-------------------------------------------------------------------+
|                           Xiaomi Laptop                           |
|                                                                   |
|   +-----------------------+              +--------------------+   |
|   |                       |  USB 2.0 FS  |   Goodix Bridge    |   |
|   |  Host Operating System|<============>|      MCU (MOC)     |   |
|   |   (libfprint-goodix)  |  Endpoints   |                    |   |
|   +-----------------------+  1/OUT, 3/IN +---------+----------+   |
|                                                    |              |
|                                                    | SPI Bus      |
|                                                    v              |
|                                          +--------------------+   |
|                                          |   GM168 Sensor Die |   |
|                                          |  (64x80 Capacitive)|   |
|                                          +--------------------+   |
+-------------------------------------------------------------------+
```

### 2.1 USB Endpoints & Interfaces

The sensor exposes a single vendor-specific USB interface (`bInterfaceClass = 0xff`, `bInterfaceSubClass = 0x00`, `bInterfaceProtocol = 0x00`):

- **Control Endpoint `0x00`**: Standard USB control transfers (GET_DESCRIPTOR, SET_CONFIGURATION).
- **Bulk OUT Endpoint `0x01`**: Transmits commands, hardware configuration tables, FDT thresholds, and TLS handshake packets from host to MCU.
- **Bulk IN Endpoint `0x83`**: Receives command replies, ACK tokens, encrypted TLS frames, and fingerprint raw scan buffers from MCU to host.
- **Interrupt IN Endpoint `0x82`**: Used by the firmware for asynchronous hardware interrupt notifications (e.g. finger touch/release triggers).

---

## 3. Reverse Engineering the Windows Driver

### 3.1 Source Material & Extraction

Reverse engineering was carried out using official OEM Windows driver packages extracted from the Xiaomi driver repository (`FingerPrint.zip` and `drivers.iso`):

1. **`wbdi.sys`**: Windows Biometric Driver Interface kernel-mode driver.
2. **`GoodixFpDriver.dll` / `goodixfp.dll`**: User-mode cryptographic engine and image acquisition module.
3. **`windows_goodix_capture.pcapng`**: Live USB traffic captures captured using Wireshark + USBPcap during Windows Hello enrollment and unlock sequences.

### 3.2 Protocol Framing

All communications follow the Goodix message protocol format:

```
+---------------+---------------+-------------------+-----------------------+
|  Flags (1B)   | Command (1B)  | Length (2B, LE)   |     Payload (NB)      |
+---------------+---------------+-------------------+-----------------------+
| 0xa0, b0, b2  |  0x00 - 0xef  |  uint16_t length  | Command-specific data |
+---------------+---------------+-------------------+-----------------------+
```

#### Protocol Flags:
- **`0xa0` (GOODIX_FLAGS_MSG_PROTOCOL)**: Plaintext management and setup commands (NOP, MCU State Query, Hardware Reset, Switch Mode).
- **`0xb0` (GOODIX_FLAGS_TLS)**: Cryptographic TLS handshake records and control protocol frames.
- **`0xb2` (GOODIX_FLAGS_TLS_DATA)**: Encrypted application data records containing encrypted fingerprint image frames.

### 3.3 Authentic Hardware Configuration Table (256 bytes)

During sensor activation, the host must program the 256-byte analog front-end (AFE) register configuration into the GM168 sensor die. If the configuration is missing or mismatched, the sensor produces flat-line noise or refuses to trigger FDT interrupts:

```c
static const guint8 goodix_581a_config[256] = {
  0x58, 0x11, 0x60, 0x71, 0x2c, 0x9d, 0x2c, 0xc9, 0x1c, 0xe5, 0x18, 0xfd, 0x00, 0xfd, 0x00, 0xfd,
  0x03, 0xba, 0x00, 0x01, 0x80, 0xca, 0x00, 0x04, 0x00, 0x84, 0x00, 0xc0, 0xb3, 0x86, 0x00, 0xbb,
  0xc4, 0x88, 0x00, 0xba, 0xba, 0x8a, 0x00, 0xb2, 0xb2, 0x8c, 0x00, 0xaa, 0xaa, 0x8e, 0x00, 0xc1,
  0xc1, 0x90, 0x00, 0xbb, 0xbb, 0x92, 0x00, 0xb1, 0xb1, 0x94, 0x00, 0x00, 0xa8, 0x96, 0x00, 0x00,
  0xb6, 0x98, 0x00, 0x00, 0x00, 0x9a, 0x00, 0x00, 0x00, 0xd2, 0x00, 0x00, 0x00, 0xd4, 0x00, 0x00,
  0x00, 0xd6, 0x00, 0x00, 0x00, 0xd8, 0x00, 0x00, 0x00, 0x50, 0x00, 0x01, 0x05, 0xd0, 0x00, 0x00,
  0x00, 0x70, 0x00, 0x00, 0x00, 0x72, 0x00, 0x78, 0x56, 0x74, 0x00, 0x34, 0x12, 0x20, 0x00, 0x10,
  0x40, 0x2a, 0x01, 0x02, 0x04, 0x22, 0x00, 0x01, 0x20, 0x24, 0x00, 0x32, 0x00, 0x80, 0x00, 0x01,
  0x00, 0x5c, 0x00, 0x80, 0x00, 0x56, 0x00, 0x24, 0x20, 0x58, 0x00, 0x03, 0x00, 0x32, 0x00, 0x0c,
  0x02, 0x66, 0x00, 0x00, 0x02, 0x7c, 0x00, 0x00, 0x58, 0x82, 0x00, 0x80, 0x15, 0x2a, 0x01, 0x82,
  0x03, 0x22, 0x00, 0x01, 0x20, 0x24, 0x00, 0x14, 0x00, 0x80, 0x00, 0x01, 0x00, 0x5c, 0x00, 0x00,
  0x01, 0x56, 0x00, 0x04, 0x20, 0x58, 0x00, 0x03, 0x00, 0x32, 0x00, 0x0c, 0x02, 0x66, 0x00, 0x00,
  0x02, 0x7c, 0x00, 0x00, 0x58, 0x82, 0x00, 0x80, 0x19, 0x2a, 0x01, 0x08, 0x00, 0x5c, 0x00, 0x00,
  0x01, 0x54, 0x00, 0x00, 0x01, 0x62, 0x00, 0x08, 0x04, 0x64, 0x00, 0x10, 0x00, 0x66, 0x00, 0x00,
  0x02, 0x7c, 0x00, 0x00, 0x58, 0x2a, 0x01, 0x08, 0x00, 0x5c, 0x00, 0x00, 0x01, 0x52, 0x00, 0x08,
  0x00, 0x54, 0x00, 0x00, 0x01, 0x66, 0x00, 0x00, 0x02, 0x7c, 0x00, 0x00, 0x58, 0x00, 0xa1, 0x74
};
```

---

## 4. Hardware Finger Detection (FDT)

The Goodix 581a does not poll continuous video frames. Doing so would consume excessive power and heat the sensor. Instead, it utilizes **Finger Detection Trigger (FDT)** mode:

```
+-------------------------------------------------------------------------+
|                         FDT State Transition Loop                       |
|                                                                         |
|        [Host Arm FDT_DOWN]                                              |
|                 |                                                       |
|                 v                                                       |
|       +-------------------+        Capacitive Shift                     |
|       | Low-Power Sleep   | ----------------------------+               |
|       |  (Wait for Touch) |                             |               |
|       +-------------------+                             v               |
|                 ^                              [Interrupt Triggered]    |
|                 |                                       |               |
|        [Host Arm FDT_UP]                                v               |
|                 |                             [Mode 5 Scan Executed]    |
|                 |                                       |               |
|                 +---------------------------------------+               |
+-------------------------------------------------------------------------+
```

### 4.1 FDT Threshold Registers

The FDT thresholds define the analog delta needed to switch states:
- **`goodix_581a_fdt_down`**: Configured before sleeping. Triggered when capacitive loading exceeds the floor threshold (finger makes physical contact).
- **`goodix_581a_fdt_up`**: Configured immediately after reading a frame. Triggered when capacitive loading returns to baseline (finger physically lifts).
