#!/usr/bin/env python3
"""
=============================================================================
 Goodix 27c6:581a (Xiaomi GM168) Real-Time Live Fingerprint Streaming Viewer
 Genuine Xiaomi Hardware Configuration + Bit-Exact 6-Byte Milan Decoder
=============================================================================
Interactive Controls:
  [C] - Cycle Colormaps (Viridis, Plasma, Inferno, Turbo, Cividis, Magma, Gray)
  [B] - Re-calibrate Ambient Baseline (Cancel background noise)
  [S] - Save High-Resolution Snapshot
  [R] - Start/Stop Recording Animated GIF
  [Q] / [Esc] - Quit & Save Session Summary
"""
import os
import sys
import time
import select
import signal
import socket
import struct
import argparse
import subprocess
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from PIL import Image
import usb.core
import usb.util

# Paths & Configuration
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch", "xiaomi_real_config.bin")
if not os.path.exists(CONFIG_PATH):
    CONFIG_PATH = "scratch/xiaomi_real_config.bin"
with open(CONFIG_PATH, "rb") as f:
    XIAOMI_CONFIG = f.read()

PSK_HEX = "0000000000000000000000000000000000000000000000000000000000000000"
ARTIFACT_DIR = "/home/mango/.gemini/antigravity-ide/brain/960ad75b-32a9-418a-a7f4-4197ba7f1695"

COLORMAPS = ["viridis", "plasma", "inferno", "turbo", "cividis", "magma", "gray"]

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

def unpack_7680_to_pixels(payload: bytes) -> np.ndarray:
    """Bit-exact 6-byte Goodix Milan interleaved nibble unpacking (80 rows x 64 cols)"""
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

def simple_blur(img: np.ndarray) -> np.ndarray:
    """Fast 3x3 box blur in pure NumPy for high-pass ridge enhancement"""
    p = np.pad(img, 1, mode='edge')
    return (
        p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:] +
        p[1:-1, :-2] + p[1:-1, 1:-1] + p[1:-1, 2:] +
        p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:]
    ) / 9.0

def main():
    parser = argparse.ArgumentParser(description="Live Real-Time Goodix GM168 Fingerprint Viewer")
    parser.add_argument("--cmap", default="viridis", help="Starting colormap")
    parser.add_argument("--fps-limit", type=float, default=25.0, help="Target max FPS")
    args = parser.parse_args()

    print("=" * 72)
    print(" Goodix 27c6:581a (Xiaomi GM168) Real-Time Fingerprint Streamer")
    print(" Authentic 80x64 Sensor Geometry | Milan 6-Byte Hardware Decoder")
    print("=" * 72, flush=True)

    server = None
    sock = None
    dev = None
    running = True

    def signal_handler(sig, frame):
        nonlocal running
        print("\n[*] Interrupted by user. Exiting cleanly...")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # 1. OpenSSL TLS-PSK Server
        print("[*] Launching local OpenSSL TLS-PSK 1.2 server...")
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

        # 2. USB Setup
        print("[*] Connecting to Goodix 27c6:581a sensor...")
        dev = usb.core.find(idVendor=0x27c6, idProduct=0x581a)
        if dev is None:
            print("[-] Error: Goodix sensor 27c6:581a not found!")
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

        # Hardware Reset & Setup
        print("[*] Initializing sensor registers...")
        write_pack(encode_cmd(0x00, b"\x00\x00\x00\x00"))
        try: read_pack(timeout_ms=50)
        except Exception: pass

        send_cmd(0xA2, b"\x05\x14", expect_data=True, timeout_ms=500)
        send_cmd(0x82, b"\x00\x00\x00\x04\x00", expect_data=True, timeout_ms=500)
        send_cmd(0xA6, b"\x00\x00", expect_data=True, timeout_ms=500)
        send_cmd(0xD6, b"\x00\x00", expect_data=True, timeout_ms=500)

        # TLS Handshake
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
            write_pack(encode_message_pack(sh[idx : idx + rec_len], 0xB0))
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
            write_pack(encode_message_pack(sf[idx : idx + rec_len], 0xB0))
            idx += rec_len
            time.sleep(0.01)

        time.sleep(0.05)
        print("[+] TLS 1.2 connected successfully!")

        # Upload Xiaomi Config
        print("[*] Uploading genuine Xiaomi GM168 hardware configuration...")
        send_cmd(0x90, XIAOMI_CONFIG, expect_data=True, timeout_ms=1000)

        # Windows post-config commands
        send_cmd(0xC4, b"\x01\x00", expect_data=False, timeout_ms=500)
        send_cmd(0xC4, b"\x01\x00", expect_data=False, timeout_ms=500)
        send_cmd(0xD2, b"\x00\x00", expect_data=True, timeout_ms=500)

        payload_36 = bytes.fromhex("0d01870080008800850080ba80a780b980a980ad809e00000000000000000000000000")
        send_cmd(0x36, payload_36, expect_data=True, timeout_ms=500)

        # Fast frame capture function
        cmd_payload_mode1 = bytes.fromhex("01008700800088008500")
        cmd_payload_mode5 = bytes.fromhex("05008700800088008500")

        def capture_raw_frame(mode_5=False):
            p = cmd_payload_mode5 if mode_5 else cmd_payload_mode1
            write_pack(encode_cmd(0x20, p))
            read_pack(timeout_ms=500) # ACK

            f_img, p_img = read_pack(timeout_ms=1000)
            if f_img != 0xB2:
                f_img, p_img = read_pack(timeout_ms=1000)

            sock.sendall(p_img[9:])
            # Flush stdout
            rlist, _, _ = select.select([server.stdout], [], [], 0.5)
            if rlist:
                data = server.stdout.read()
                if len(data) >= 7684:
                    return unpack_7680_to_pixels(data[:-4])
            return None

        # Calibrate initial air baseline
        print("\n[*] Acquiring initial ambient baseline (keep finger off sensor)...")
        baselines = []
        for _ in range(4):
            f = capture_raw_frame()
            if f is not None:
                baselines.append(f)
            time.sleep(0.04)

        if not baselines:
            print("[-] Error: Failed to acquire baseline frames!")
            return

        baseline = np.mean(baselines, axis=0)
        print(f"[+] Ambient baseline established! (mean={baseline.mean():.1f})", flush=True)

        # Setup Matplotlib Interactive UI
        plt.ion()
        fig, axes = plt.subplots(1, 3, figsize=(16, 5.5), facecolor="#121212")
        fig.canvas.manager.set_window_title("Goodix 27c6:581a Live Fingerprint Streamer")

        cmap_idx = COLORMAPS.index(args.cmap) if args.cmap in COLORMAPS else 0
        current_cmap = COLORMAPS[cmap_idx]

        init_crop = (capture_raw_frame() - baseline)[2:78, 2:62]

        im_delta = axes[0].imshow(init_crop, cmap=current_cmap, interpolation="bilinear", vmin=-150, vmax=150)
        axes[0].set_title("Capacitive Delta (Live)", color="#38bdf8", fontsize=11, fontweight="bold")
        axes[0].tick_params(colors="gray")
        cb0 = plt.colorbar(im_delta, ax=axes[0], fraction=0.046, pad=0.04)
        cb0.ax.tick_params(colors="gray")

        im_trans = axes[1].imshow(init_crop.T, cmap="plasma", interpolation="bilinear", vmin=-150, vmax=150)
        axes[1].set_title("Transposed View (Plasma)", color="#f472b6", fontsize=11, fontweight="bold")
        axes[1].tick_params(colors="gray")
        cb1 = plt.colorbar(im_trans, ax=axes[1], fraction=0.046, pad=0.04)
        cb1.ax.tick_params(colors="gray")

        # Ridge highpass
        blur_init = simple_blur(init_crop)
        im_ridge = axes[2].imshow(init_crop - blur_init, cmap="gray", interpolation="bilinear", vmin=-40, vmax=40)
        axes[2].set_title("Dermal Ridges (High-Pass)", color="#4ade80", fontsize=11, fontweight="bold")
        axes[2].tick_params(colors="gray")
        cb2 = plt.colorbar(im_ridge, ax=axes[2], fraction=0.046, pad=0.04)
        cb2.ax.tick_params(colors="gray")

        hud_text = fig.suptitle("Goodix GM168 Live  |  FPS: 0.0  |  Delta: 0  |  [C] Color  [B] Baseline  [S] Save  [Q] Quit", color="white", fontsize=12, fontweight="bold")
        plt.tight_layout()
        plt.show(block=False)
        fig.canvas.draw()

        # Keyboard event callbacks
        save_snapshot_flag = False
        recalibrate_flag = False
        recording_gif = False
        recorded_frames = []

        def on_key(event):
            nonlocal current_cmap, cmap_idx, save_snapshot_flag, recalibrate_flag, running, recording_gif
            if event.key in ['q', 'escape']:
                running = False
            elif event.key == 'c':
                cmap_idx = (cmap_idx + 1) % len(COLORMAPS)
                current_cmap = COLORMAPS[cmap_idx]
                im_delta.set_cmap(current_cmap)
                print(f"[+] Colormap: {current_cmap}")
            elif event.key == 'b':
                recalibrate_flag = True
                print("[*] Re-calibrating ambient baseline...")
            elif event.key == 's':
                save_snapshot_flag = True
            elif event.key == 'r':
                recording_gif = not recording_gif
                state_str = "STARTED" if recording_gif else "STOPPED"
                print(f"[*] GIF recording {state_str} (buffer: {len(recorded_frames)} frames)")

        def on_close(event):
            nonlocal running
            running = False

        fig.canvas.mpl_connect('key_press_event', on_key)
        fig.canvas.mpl_connect('close_event', on_close)

        print("\n" + "=" * 72)
        print(" >>> LIVE STREAM ACTIVE! <<<")
        print(" Move your finger on the circular power button to watch ridges in real-time!")
        print(" Controls:  [C] Colormap   [B] Baseline Zero   [S] Snapshot   [Q] Exit")
        print("=" * 72 + "\n", flush=True)

        frame_count = 0
        t_fps = time.time()
        fps = 0.0
        frame_interval = 1.0 / max(1.0, args.fps_limit)

        while running and plt.fignum_exists(fig.number):
            t_loop = time.time()

            # Dynamic mode selection: Mode 5 gives ultra crisp touch frames
            mat = capture_raw_frame(mode_5=True)
            if mat is None:
                mat = capture_raw_frame(mode_5=False)
            if mat is None:
                continue

            frame_count += 1
            if frame_count % 10 == 0:
                now = time.time()
                fps = 10.0 / (now - t_fps + 1e-6)
                t_fps = now

            if recalibrate_flag:
                recalibrate_flag = False
                baseline = mat.copy()
                print(f"[+] Baseline re-calibrated! (mean={baseline.mean():.1f})")

            # Active sensor region (rows 2..78, cols 2..62)
            diff = mat - baseline
            active_diff = diff[2:78, 2:62]

            # High-pass filter
            blur = simple_blur(active_diff)
            ridges = active_diff - blur

            touch_delta = float(np.percentile(np.abs(active_diff), 95))
            is_touching = touch_delta > 120.0

            # Dynamic contrast window
            if is_touching:
                vmin, vmax = np.percentile(active_diff, [3, 97])
                if vmax - vmin < 40:
                    vmin, vmax = -100, 100
                r_vmin, r_vmax = np.percentile(ridges, [4, 96])
            else:
                vmin, vmax = -60, 60
                r_vmin, r_vmax = -25, 25

            # Update Matplotlib image data
            im_delta.set_data(active_diff)
            im_delta.set_clim(vmin, vmax)

            im_trans.set_data(active_diff.T)
            im_trans.set_clim(vmin, vmax)

            im_ridge.set_data(ridges)
            im_ridge.set_clim(r_vmin, r_vmax)

            # Update HUD text
            status_tag = "[TOUCHING]" if is_touching else "[IDLE]"
            status_color = "#38bdf8" if is_touching else "white"
            hud_text.set_text(f"Goodix GM168 Live  |  FPS: {fps:4.1f}  |  Delta: {touch_delta:5.1f} {status_tag}  |  [C] Color [B] Zero [S] Save [Q] Quit")
            hud_text.set_color(status_color)

            # Redraw
            fig.canvas.flush_events()

            # Record frame for animated GIF if enabled
            if recording_gif or (is_touching and len(recorded_frames) < 120):
                # Convert active_diff to 8-bit image for GIF
                norm = np.clip((active_diff - vmin) / (vmax - vmin + 1e-5) * 255.0, 0, 255).astype(np.uint8)
                recorded_frames.append(Image.fromarray(norm))

            # Save snapshot
            if save_snapshot_flag:
                save_snapshot_flag = False
                ts = int(time.time())
                snap_path = os.path.join(ARTIFACT_DIR, f"snapshot_{ts}.png")
                plt.savefig(snap_path, dpi=180, facecolor="#121212")
                print(f"\n[+] Saved snapshot: {snap_path}")

            # Frame rate pacing
            elapsed = time.time() - t_loop
            if elapsed < frame_interval:
                time.sleep(frame_interval - elapsed)

        # Session completion: save animated GIF if frames recorded
        if recorded_frames:
            gif_path = os.path.join(ARTIFACT_DIR, "fingerprint_live_session.gif")
            print(f"\n[*] Saving {len(recorded_frames)} frames to animated GIF: {gif_path}...")
            recorded_frames[0].save(
                gif_path,
                save_all=True,
                append_images=recorded_frames[1:],
                duration=50,
                loop=0
            )
            print(f"[+] Animated GIF saved successfully: {gif_path}")

    except Exception as e:
        print("[-] Exception:", e)
        import traceback
        traceback.print_exc()
    finally:
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
