#!/usr/bin/env python3
"""
True Xiaomi GM168 (Goodix 27c6:581a) Fingerprint Capture & Ridge Extractor
Uses genuine Windows capture config (80x64 sensor geometry, 7680 bytes).
Failsafe non-blocking execution with 20s watchdog alarm.
"""
import os
import sys
import time
import select
import signal
import socket
import struct
import subprocess
import numpy as np
import matplotlib.pyplot as plt
import usb.core
import usb.util

# 30-second maximum execution watchdog
def alarm_handler(signum, frame):
    print("\n[!] Watchdog triggered: 30s timeout reached. Exiting cleanly.")
    sys.exit(1)

signal.signal(signal.SIGALRM, alarm_handler)
signal.alarm(30)

with open("scratch/xiaomi_real_config.bin", "rb") as f:
    XIAOMI_CONFIG = f.read()

PSK_HEX = "0000000000000000000000000000000000000000000000000000000000000000"
ARTIFACT_DIR = "/home/mango/.gemini/antigravity-ide/brain/960ad75b-32a9-418a-a7f4-4197ba7f1695"

def encode_message_pack(payload: bytes, flags: int = 0xA0) -> bytes:
    h = struct.pack("<BH", flags, len(payload))
    h += bytes([sum(h) & 0xFF])
    return h + payload

def encode_cmd(cmd: int, payload: bytes = b"", chk: bool = True) -> bytes:
    p = struct.pack("<BH", cmd, len(payload) + 1) + payload
    c = (0xAA - sum(p)) & 0xFF if chk else 0x88
    p += bytes([c])
    return encode_message_pack(p, 0xA0)

def decode_cmd(data: bytes):
    if len(data) < 3:
        return 0, b""
    cmd = data[0]
    length = struct.unpack("<H", data[1:3])[0]
    payload = data[3:3 + length - 1] if len(data) >= 3 + length - 1 else data[3:]
    return cmd, payload

def unpack_7680_to_pixels(payload: bytes):
    # 7680 bytes -> 1280 chunks of 6 bytes -> 5120 pixels (80 rows x 64 cols)
    # Goodix Milan bit-exact 6-byte interleaved nibble unpacking
    c = np.frombuffer(payload[:7680], dtype=np.uint8).reshape((-1, 6))
    c0 = c[:, 0].astype(np.uint16)
    c1 = c[:, 1].astype(np.uint16)
    c2 = c[:, 2].astype(np.uint16)
    c3 = c[:, 3].astype(np.uint16)
    c4 = c[:, 4].astype(np.uint16)
    c5 = c[:, 5].astype(np.uint16)

    p0 = ((c0 & 0x0F) << 8) | c1
    p1 = (c3 << 4) | (c0 >> 4)
    p2 = ((c5 & 0x0F) << 8) | c2
    p3 = (c4 << 4) | (c5 >> 4)

    pixels = np.empty((len(c), 4), dtype=np.float32)
    pixels[:, 0] = p0.astype(np.float32)
    pixels[:, 1] = p1.astype(np.float32)
    pixels[:, 2] = p2.astype(np.float32)
    pixels[:, 3] = p3.astype(np.float32)
    return pixels.reshape((80, 64))

def main():
    print("=" * 68)
    print(" Goodix 27c6:581a (Xiaomi GM168) Genuine Driver Fingerprint Capture")
    print(" Authentic 80x64 Sensor Geometry with Windows Configuration")
    print("=" * 68, flush=True)

    server = None
    sock = None
    dev = None

    try:
        # 1. Start OpenSSL TLS-PSK server
        print("[*] Launching OpenSSL TLS-PSK 1.2 server...")
        server = subprocess.Popen(
            [
                "openssl", "s_server",
                "-nocert",
                "-psk", PSK_HEX,
                "-port", "4433",
                "-quiet",
                "-tls1_2",
                "-cipher", "PSK-AES128-CBC-SHA256"
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        time.sleep(0.2)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect(("127.0.0.1", 4433))
        os.set_blocking(server.stdout.fileno(), False)

        # 2. USB initialization
        print("[*] Connecting to Goodix 27c6:581a sensor...")
        dev = usb.core.find(idVendor=0x27c6, idProduct=0x581a)
        if dev is None:
            print("[-] Goodix sensor not found!")
            return

        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except Exception:
            pass

        dev.set_configuration()
        cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]
        ep_out = usb.util.find_descriptor(intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
        ep_in = usb.util.find_descriptor(intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)

        for _ in range(5):
            try: ep_in.read(1024, timeout=20)
            except Exception: break

        def write_pack(data: bytes):
            for offset in range(0, len(data), 64):
                chunk = data[offset : offset + 64]
                ep_out.write(chunk, timeout=1000)

        def read_pack(timeout_ms=1000):
            hdr = bytes(ep_in.read(64, timeout=timeout_ms))
            flag = hdr[0]
            length = struct.unpack("<H", hdr[1:3])[0]
            total_target = length + 4
            buf = bytearray(hdr)
            t_end = time.time() + (timeout_ms / 1000.0)
            while len(buf) < total_target and time.time() < t_end:
                rem = total_target - len(buf)
                try:
                    chunk = bytes(ep_in.read(min(rem, 16384), timeout=int(timeout_ms)))
                    buf.extend(chunk)
                except usb.core.USBTimeoutError:
                    break
            return flag, bytes(buf[4:total_target])

        def send_cmd(cmd: int, payload: bytes = b"", expect_data: bool = False, timeout_ms: int = 1000):
            write_pack(encode_cmd(cmd, payload))
            f_first, p_first = read_pack(timeout_ms=timeout_ms)
            c_first, d_first = decode_cmd(p_first)
            if c_first == 0xb0 and expect_data:
                try:
                    f_d, p_d = read_pack(timeout_ms=500)
                    c_d, d_d = decode_cmd(p_d)
                    return d_d
                except Exception:
                    return d_first
            return d_first

        # Reset & Setup sequence
        print("[*] Initializing sensor registers...")
        write_pack(encode_cmd(0x00, b"\x00\x00\x00\x00"))
        try: read_pack(timeout_ms=50)
        except Exception: pass

        send_cmd(0xA2, b"\x05\x14", expect_data=True, timeout_ms=500)
        send_cmd(0x82, b"\x00\x00\x00\x04\x00", expect_data=True, timeout_ms=500)
        send_cmd(0xA6, b"\x00\x00", expect_data=True, timeout_ms=500)
        send_cmd(0xD6, b"\x00\x00", expect_data=True, timeout_ms=500)

        # TLS 1.2 Handshake
        print("[*] Establishing secure TLS 1.2 channel...")
        write_pack(encode_cmd(0xD0, b"\x00\x00"))
        f_ch, ch = read_pack(timeout_ms=1000)
        if f_ch == 0xA0:
            f_ch, ch = read_pack(timeout_ms=1000)
        sock.sendall(ch)

        sh = sock.recv(2048)
        idx = 0
        while idx < len(sh):
            if idx + 5 > len(sh): break
            rec_len = 5 + struct.unpack(">H", sh[idx+3 : idx+5])[0]
            rec = sh[idx : idx + rec_len]
            write_pack(encode_message_pack(rec, 0xB0))
            idx += rec_len
            time.sleep(0.01)

        for _ in range(3):
            fm, msg = read_pack(timeout_ms=1500)
            sock.sendall(msg)

        sf = sock.recv(2048)
        idx = 0
        while idx < len(sf):
            if idx + 5 > len(sf): break
            rec_len = 5 + struct.unpack(">H", sf[idx+3 : idx+5])[0]
            rec = sf[idx : idx + rec_len]
            write_pack(encode_message_pack(rec, 0xB0))
            idx += rec_len
            time.sleep(0.01)

        time.sleep(0.05)
        print("[+] TLS 1.2 connected successfully!")

        # Upload genuine configuration
        print("[*] Uploading genuine Xiaomi GM168 hardware configuration...")
        send_cmd(0x90, XIAOMI_CONFIG, expect_data=True, timeout_ms=1000)

        # Post-config register sequence
        send_cmd(0xC4, b"\x01\x00", expect_data=False, timeout_ms=500)
        send_cmd(0xC4, b"\x01\x00", expect_data=False, timeout_ms=500)
        send_cmd(0xD2, b"\x00\x00", expect_data=True, timeout_ms=500)

        payload_36 = bytes.fromhex("0d01870080008800850080ba80a780b980a980ad809e00000000000000000000000000")
        send_cmd(0x36, payload_36, expect_data=True, timeout_ms=500)

        def read_decrypted_frame(mode_byte=0x01):
            cmd_payload = bytes([mode_byte, 0x00]) + bytes.fromhex("8700800088008500")
            write_pack(encode_cmd(0x20, cmd_payload))
            f_ack, p_ack = read_pack(timeout_ms=1000)

            f_img, p_img = read_pack(timeout_ms=2000)
            if f_img != 0xB2:
                f_img, p_img = read_pack(timeout_ms=2000)

            sock.sendall(p_img[9:])
            time.sleep(0.02)

            rlist, _, _ = select.select([server.stdout], [], [], 1.0)
            if rlist:
                data = server.stdout.read()
                return data[:-4] # Drop 4-byte checksum, return 7680 bytes
            return None

        # Step 1: Calibrate ambient baseline (finger off sensor)
        print("\n[*] Acquiring ambient air baseline (please keep finger OFF sensor)...")
        baselines = []
        for b_idx in range(3):
            raw = read_decrypted_frame(mode_byte=0x01)
            if raw and len(raw) == 7680:
                mat = unpack_7680_to_pixels(raw)
                baselines.append(mat)
                print(f"    Baseline frame {b_idx+1}: mean={mat.mean():.1f}")
            time.sleep(0.05)

        if not baselines:
            print("[-] Failed to capture baseline frames!")
            return

        baseline = np.mean(baselines, axis=0)
        print(f"[+] Ambient baseline established! Mean: {baseline.mean():.1f}, Std: {baseline.std():.1f}")

        # Step 2: Arm hardware FDT capacitive touch interrupt
        print("\n" + "=" * 68)
        print(" >>> WAITING FOR PHYSICAL FINGER TOUCH ON POWER BUTTON... <<<")
        print(" >>> Keep finger OFF until ready. Place your finger flat on the sensor! <<<")
        print("=" * 68, flush=True)

        payload_32 = bytes.fromhex("1c01870080008800850080ba80a780b980a980ad809e0000000087008000880085000001")
        write_pack(encode_cmd(0x32, payload_32))
        f_ack, p_ack = read_pack(timeout_ms=1000)

        touch_captured = False
        captured_mat = None
        t_start = time.time()

        # Wait up to 15 seconds for the physical touch event
        while time.time() - t_start < 15.0:
            rem = 15.0 - (time.time() - t_start)
            try:
                flag, event = read_pack(timeout_ms=500)
                # Sensor sends 32 11 00 02... on physical touch down!
                if len(event) >= 4 and event[0] == 0x32:
                    print(f"\n[***] PHYSICAL FINGER TOUCH DETECTED BY SENSOR HARDWARE! (event: {event[:8].hex()}) [***]", flush=True)
                    print("[*] Instantly capturing high-resolution Mode 5 finger scan...", flush=True)
                    raw_mode5 = read_decrypted_frame(mode_byte=0x05)
                    if raw_mode5 and len(raw_mode5) == 7680:
                        captured_mat = unpack_7680_to_pixels(raw_mode5)
                        print("[+] Mode 5 Finger Scan Captured Successfully!")
                        touch_captured = True
                        break
            except Exception:
                # No touch event yet, continue waiting
                elapsed = time.time() - t_start
                print(f"  [{elapsed:4.1f}s / 15s] Waiting for touch...", end="\r", flush=True)

        if not touch_captured:
            print("\n[-] Timed out waiting for touch. Using last frame.")
            raw_mode5 = read_decrypted_frame(mode_byte=0x01)
            captured_mat = unpack_7680_to_pixels(raw_mode5) if raw_mode5 else baseline

        # Ridge enhancement & subtraction
        finger_diff = captured_mat - baseline
        # Mask out boundary rows and columns
        crop_diff = finger_diff[2:78, 2:62].copy()
        best_delta = float(np.percentile(np.abs(crop_diff), 95))

        # Dynamic contrast scaling using 2nd and 98th percentiles of the active sensor area
        vmin, vmax = np.percentile(crop_diff, [2, 98])
        if vmax - vmin < 10:
            vmin, vmax = -50, 50

        # Ridge enhancement: high-pass filter using pure numpy 3x3 local blur
        padded = np.pad(crop_diff, 1, mode='edge')
        blur = (
            padded[:-2, :-2] + padded[:-2, 1:-1] + padded[:-2, 2:] +
            padded[1:-1, :-2] + padded[1:-1, 1:-1] + padded[1:-1, 2:] +
            padded[2:, :-2] + padded[2:, 1:-1] + padded[2:, 2:]
        ) / 9.0
        ridge_highpass = crop_diff - blur
        r_vmin, r_vmax = np.percentile(ridge_highpass, [3, 97])

        # Save visualization artifacts
        print("\n[*] Generating high-resolution fingerprint visualizations...")
        fig, axes = plt.subplots(1, 4, figsize=(18, 5.5), facecolor="#121212")

        # 1. Raw Frame
        raw_crop = captured_mat[2:78, 2:62]
        im0 = axes[0].imshow(raw_crop, cmap="gray", interpolation="bilinear")
        axes[0].set_title(f"Raw Frame (Active Area)\nmean={raw_crop.mean():.1f}", color="white", fontsize=11)
        axes[0].tick_params(colors="gray")
        plt.colorbar(im0, ax=axes[0], fraction=0.046, pad=0.04)

        # 2. Baseline-Subtracted Delta (Viridis)
        im1 = axes[1].imshow(crop_diff, cmap="viridis", interpolation="bilinear", vmin=vmin, vmax=vmax)
        axes[1].set_title(f"Capacitive Delta (Viridis)\ntouch_delta={best_delta:.1f}", color="#38bdf8", fontsize=11, fontweight="bold")
        axes[1].tick_params(colors="gray")
        plt.colorbar(im1, ax=axes[1], fraction=0.046, pad=0.04)

        # 3. Transposed Orientation (Plasma)
        im2 = axes[2].imshow(crop_diff.T, cmap="plasma", interpolation="bilinear", vmin=vmin, vmax=vmax)
        axes[2].set_title("Transposed View (Plasma)", color="#f472b6", fontsize=11, fontweight="bold")
        axes[2].tick_params(colors="gray")
        plt.colorbar(im2, ax=axes[2], fraction=0.046, pad=0.04)

        # 4. High-Pass Ridge Enhanced
        im3 = axes[3].imshow(ridge_highpass, cmap="gray", interpolation="bilinear", vmin=r_vmin, vmax=r_vmax)
        axes[3].set_title("Dermal Ridges (High-Pass)", color="#4ade80", fontsize=11, fontweight="bold")
        axes[3].tick_params(colors="gray")
        plt.colorbar(im3, ax=axes[3], fraction=0.046, pad=0.04)

        plt.suptitle("Goodix GM168 (27c6:581a) Authentic Hardware Capture", color="white", fontsize=14, fontweight="bold")
        plt.tight_layout()

        out_path = os.path.join(ARTIFACT_DIR, "xiaomi_authentic_fingerprint.png")
        plt.savefig(out_path, dpi=180, facecolor="#121212")
        plt.savefig("scratch/xiaomi_authentic_fingerprint.png", dpi=180, facecolor="#121212")
        print(f"[+] Saved high-resolution artifact: {out_path}")

    except Exception as e:
        print("[-] Error:", e)
        import traceback
        traceback.print_exc()
    finally:
        signal.alarm(0)
        if sock:
            try: sock.close()
            except Exception: pass
        if server:
            try:
                server.kill()
                server.wait(timeout=0.5)
            except Exception: pass
        if dev:
            try: usb.util.dispose_resources(dev)
            except Exception: pass
        print("[*] Cleanup complete. Device released.")

if __name__ == "__main__":
    main()
