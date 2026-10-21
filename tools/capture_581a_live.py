#!/usr/bin/env python3
"""
Goodix 27c6:581a (Xiaomi GM168) Live Fingerprint Scanner
100% Neodyme Reversing Pipeline & Exact Vendor Image Geometry

Sensor Specs:
- Sensor Resolution: 56 x 176 pixels (9,856 pixels total)
- Packed 12-bit Raw Payload: 14,784 bytes (+ 4 checksum bytes = 14,788 bytes)
- 6-byte interleaved bit unpacking (ImageRestructMilanG / Neodyme)
- Live capacitive touch detection with robust float32 delta & interactive keypress fallback
"""

import sys
import os
import subprocess
import socket
import time
import struct
import select
import numpy as np
from PIL import Image
import usb.core
import usb.util

VENDOR_ID = 0x27C6
PRODUCT_ID = 0x581A

FLAGS_MESSAGE = 0xA0
FLAGS_TLS = 0xB0
FLAGS_TLS_DATA = 0xB2

CMD_NOP = 0x00
CMD_MCU_GET_IMAGE = 0x20
CMD_MCU_SWITCH_FDT_MODE = 0x36
CMD_READ_SENSOR_REG = 0x82
CMD_UPLOAD_CONFIG_MCU = 0x90
CMD_RESET = 0xA2
CMD_READ_OTP = 0xA6
CMD_ACK = 0xB0
CMD_SET_DRV_STATE = 0xC4
CMD_REQUEST_TLS_CONNECTION = 0xD0
CMD_MCU_GET_POV_IMAGE = 0xD2
CMD_POV_IMAGE_CHECK = 0xD6

PSK_HEX = "0000000000000000000000000000000000000000000000000000000000000000"

# Genuine 256-byte Xiaomi GM168 RAM config from live Windows USB traces
REAL_CONFIG = bytes.fromhex(
    "301160712c9d2cc91ce518fd00fd00fd03ba000080ca0006008400beb28600c5"
    "b98800b5ad8a009d958c0000be8e0000c5900000b59200009d940000af960000"
    "bf980000b69a0000a7d2000000d4000000d6000000d800000012000304d00000"
    "00700000007200785674003412200010402a0102002200012024003200800001"
    "045c000001560030485800020032000802660000027c000038820080152a0182"
    "032200012024001400800001045c00000156000c245800050032000802660000"
    "027c000038820080162a0108005c008000540000016200380464001000660000"
    "027c0001382a0108005c0000015200080054000001660000027c00013800e858"
)

# Neodyme / Milan G True Sensor Dimensions
SENSOR_WIDTH = 56
SENSOR_HEIGHT = 176
IMAGE_RAW_SIZE = 14788  # 14784 image + 4 checksum

def encode_message_pack(payload: bytes, flags: int = FLAGS_MESSAGE) -> bytes:
    h = struct.pack("<BH", flags, len(payload))
    h += bytes([sum(h) & 0xFF])
    return h + payload

def encode_cmd(cmd: int, payload: bytes = b"", chk: bool = True) -> bytes:
    p = struct.pack("<BH", cmd, len(payload) + 1) + payload
    c = (0xAA - sum(p)) & 0xFF if chk else 0x88
    p += bytes([c])
    return encode_message_pack(p, FLAGS_MESSAGE)

def decode_cmd(data: bytes):
    cmd = data[0]
    length = struct.unpack("<H", data[1:3])[0]
    payload = data[3:3 + length - 1]
    return cmd, payload

def unpack_neodyme_milan(data: bytes) -> np.ndarray:
    """
    Exact Neodyme & Wbdi.dll 6-byte interleaved bit unpacking:
    Every 6 bytes unpacks to 4 12-bit pixels (0..4095).
    14,784 bytes -> 9,856 pixels.
    Reshaped to (SENSOR_HEIGHT, SENSOR_WIDTH) and transposed per Neodyme.
    """
    assert len(data) >= 14784
    raw = data[:14784]
    unpacked = []
    for i in range(0, 14784, 6):
        c = raw[i:i+6]
        p0 = ((c[0] & 0x0F) << 8) | c[1]
        p1 = (c[3] << 4) | (c[0] >> 4)
        p2 = ((c[5] & 0x0F) << 8) | c[2]
        p3 = (c[4] << 4) | (c[5] >> 4)
        unpacked.extend([p0, p1, p2, p3])

    # In Neodyme: array reshaped to (176, 56), transposed and flipped vertically
    arr = np.array(unpacked, dtype=np.float32).reshape((SENSOR_HEIGHT, SENSOR_WIDTH))
    mat = np.flipud(arr.transpose())
    return mat

def save_images(mat: np.ndarray, basename: str = "fingerprint_live"):
    h, w = mat.shape

    # 1. Raw PGM (12-bit)
    pgm_path = f"{basename}.pgm"
    with open(pgm_path, "w") as f:
        f.write(f"P2\n{w} {h}\n4095\n")
        for row in mat:
            f.write(" ".join(f"{int(v)}" for v in row) + "\n")

    # 2. Contrast Normalized PNG (1% - 99% Percentile Stretch)
    p1, p99 = np.percentile(mat, 1), np.percentile(mat, 99)
    if p99 > p1:
        norm = np.clip((mat - p1) * 255.0 / (p99 - p1), 0, 255).astype(np.uint8)
    else:
        norm = np.zeros_like(mat, dtype=np.uint8)

    png_path = f"{basename}.png"
    Image.fromarray(norm).save(png_path)

    # 3. High-Contrast Ridge Map (Inverted, identical to Neodyme blog presentation)
    ridges_path = f"{basename}_ridges.png"
    Image.fromarray(255 - norm).save(ridges_path)

    print(f"\n[+] Successfully saved:")
    print(f"    - PNG Grayscale: {os.path.abspath(png_path)} ({w}x{h})")
    print(f"    - PNG Ridges:    {os.path.abspath(ridges_path)} ({w}x{h})")
    print(f"    - Raw 12-bit:    {os.path.abspath(pgm_path)}")

def check_user_input():
    """Non-blocking check if user pressed ENTER in terminal"""
    if select.select([sys.stdin], [], [], 0)[0]:
        sys.stdin.readline()
        return True
    return False

def main():
    subprocess.run(["pkill", "-f", "openssl s_server -nocert"], stderr=subprocess.DEVNULL)
    time.sleep(0.1)

    print("=" * 65)
    print(" Goodix 27c6:581a (Xiaomi GM168) Live Fingerprint Scanner")
    print(" Neodyme Pipeline (56x176 Full Resolution)")
    print("=" * 65)
    print("[*] Starting local OpenSSL TLS-PSK server on 127.0.0.1:4433...", flush=True)
    server = subprocess.Popen([
        "openssl", "s_server", "-nocert", "-psk", PSK_HEX, "-port", "4433", "-quiet"
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    time.sleep(0.3)

    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if not dev:
        print("[-] Error: Goodix 27c6:581a device not found.")
        server.terminate()
        return

    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
    except Exception:
        pass
    try:
        dev.set_configuration()
    except Exception:
        pass
    usb.util.claim_interface(dev, 0)
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    ep_out = intf[0]
    ep_in = intf[1]

    def write_pack(data: bytes):
        pad_len = (64 - (len(data) % 64)) % 64
        padded = data + b"\x00" * pad_len
        ep_out.write(padded, timeout=2000)

    def read_pack(timeout_ms: int = 2000):
        raw = bytes(ep_in.read(20480, timeout=timeout_ms))
        flag = raw[0]
        length = struct.unpack("<H", raw[1:3])[0]
        payload = raw[4:4 + length]
        return flag, payload

    def send_cmd(cmd: int, payload: bytes = b"", expect_data: bool = False, timeout_ms: int = 2000):
        write_pack(encode_cmd(cmd, payload))
        f_first, p_first = read_pack(timeout_ms=timeout_ms)
        c_first, d_first = decode_cmd(p_first)
        if c_first == CMD_ACK and expect_data:
            try:
                f_d, p_d = read_pack(timeout_ms=500)
                c_d, d_d = decode_cmd(p_d)
                return d_d
            except usb.core.USBTimeoutError:
                return d_first
        return d_first

    try:
        # 1. Analog Reset & Registers
        print("[*] Initializing sensor array & RAM registers...", flush=True)
        write_pack(encode_cmd(CMD_NOP, b"\x00\x00\x00\x00"))
        try: read_pack(timeout_ms=100)
        except Exception: pass

        send_cmd(CMD_RESET, b"\x05\x14", expect_data=True)
        send_cmd(CMD_READ_SENSOR_REG, b"\x00\x00\x00\x04\x00", expect_data=True)
        send_cmd(CMD_READ_OTP, b"\x00\x00", expect_data=True)
        send_cmd(CMD_POV_IMAGE_CHECK, b"\x00\x00", expect_data=True)
        send_cmd(CMD_UPLOAD_CONFIG_MCU, REAL_CONFIG, expect_data=True)

        # 2. TLS-PSK Handshake
        print("[*] Performing TLS-PSK 1.2 handshake...", flush=True)
        write_pack(encode_cmd(CMD_REQUEST_TLS_CONNECTION, b"\x00\x00"))
        f_ch, ch = read_pack(timeout_ms=2000)
        if f_ch == FLAGS_MESSAGE:
            f_ch, ch = read_pack(timeout_ms=2000)

        sock = socket.socket()
        sock.connect(("127.0.0.1", 4433))
        sock.sendall(ch)
        sh = sock.recv(2048)
        write_pack(encode_message_pack(sh, FLAGS_TLS))
        for _ in range(3):
            fm, msg = read_pack(timeout_ms=2000)
            sock.sendall(msg)
        sf = sock.recv(2048)
        write_pack(encode_message_pack(sf, FLAGS_TLS))
        time.sleep(0.02)
        print("[+] Secure TLS 1.2 channel ready!", flush=True)

        # 3. Post-TLS Configuration
        send_cmd(CMD_UPLOAD_CONFIG_MCU, REAL_CONFIG, expect_data=True)
        send_cmd(CMD_SET_DRV_STATE, b"\x01\x00", expect_data=False)
        send_cmd(CMD_SET_DRV_STATE, b"\x01\x00", expect_data=False)
        send_cmd(CMD_MCU_GET_POV_IMAGE, b"\x00\x00", expect_data=True)

        payload_36 = bytes.fromhex("0d0180b880b280b480ae80b380ac80b180a780aa80a4")
        send_cmd(CMD_MCU_SWITCH_FDT_MODE, payload_36, expect_data=True)

        # 4. Background Baseline Frame
        print("[*] Calibrating empty background baseline...", flush=True)
        write_pack(encode_cmd(CMD_MCU_GET_IMAGE, b"\x01\x00"))
        read_pack(timeout_ms=2000)
        f_cal, p_cal = read_pack(timeout_ms=5000)
        sock.sendall(p_cal[9:])
        cal_raw = server.stdout.read(IMAGE_RAW_SIZE)[:-4]
        cal_mat = unpack_neodyme_milan(cal_raw)
        cal_mean = float(np.mean(cal_mat))
        print(f"[+] Ambient background baseline calibrated (mean={cal_mean:.1f})")

        # 5. Live Streaming & Capacitive Touch Detection
        print("\n" + "=" * 65)
        print(" >>> LIVE SENSOR SCANNER ACTIVE (56x176 Full Resolution) <<<")
        print(" >>> PLACE YOUR FINGER FLATLY AND FIRMLY ON THE SENSOR! <<<")
        print(" >>> (Auto-triggers on finger touch, or press ENTER anytime) <<<")
        print("=" * 65 + "\n", flush=True)

        start_time = time.time()
        timeout_seconds = 30
        captured = False
        best_frame = None
        best_delta = 0.0

        for frame_idx in range(1, 200):
            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                print("\n[-] Scanner timed out after 30s.")
                break

            # Request frame
            send_cmd(CMD_MCU_SWITCH_FDT_MODE, payload_36, expect_data=True)
            write_pack(encode_cmd(CMD_MCU_GET_IMAGE, b"\x01\x00"))
            read_pack(timeout_ms=2000)
            f_img, p_img = read_pack(timeout_ms=5000)
            sock.sendall(p_img[9:])
            frame_raw = server.stdout.read(IMAGE_RAW_SIZE)[:-4]
            frame_mat = unpack_neodyme_milan(frame_raw)

            # Robust float32 difference analysis (No uint16 underflow!)
            diff = frame_mat - cal_mat
            # Capacitive change in top 20% most active pixels (contact zone)
            top_active_delta = float(np.percentile(np.abs(diff), 90))
            frame_mean = float(np.mean(frame_mat))

            # Visual touch meter
            bars = min(10, int(top_active_delta / 80.0))
            meter = "#" * bars + "-" * (10 - bars)

            remaining = int(timeout_seconds - elapsed)
            print(f"  [{elapsed:4.1f}s] Touch Level: [{meter}] delta={top_active_delta:5.0f} (mean={frame_mean:4.0f})", end="\r", flush=True)

            if top_active_delta > best_delta:
                best_delta = top_active_delta
                best_frame = frame_mat

            # Check for user pressing ENTER
            user_pressed_enter = check_user_input()

            # Trigger condition: finger contact causes localized capacitance delta > 300
            if top_active_delta > 300.0 or user_pressed_enter:
                reason = "FINGER TOUCH DETECTED" if not user_pressed_enter else "USER ENTER PRESSED"
                print(f"\n\n[***] {reason}! (delta={top_active_delta:.0f}) [***]")
                print("[*] Processing and saving high-resolution fingerprint...")
                save_images(frame_mat, "fingerprint_live")
                captured = True
                break

            time.sleep(0.04)

        if not captured and best_frame is not None:
            print(f"\n[*] Saving highest-contact frame (max delta={best_delta:.0f})...")
            save_images(best_frame, "fingerprint_live")

        sock.close()
    except Exception as e:
        print(f"\n[-] Error: {e}", flush=True)
        import traceback
        traceback.print_exc()
    finally:
        server.terminate()

if __name__ == "__main__":
    main()
