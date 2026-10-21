#!/usr/bin/env python3
"""
Interactive Fingerprint Capture Tool for Goodix 27c6:581a (GM168)
Waits for physical finger touch and captures a real fingerprint scan.
"""

import sys
import time
import socket
import struct
import subprocess
import usb.core
import usb.util

VENDOR_ID = 0x27C6
PRODUCT_ID = 0x581A

FLAGS_MESSAGE = 0xA0
FLAGS_TLS = 0xB0
FLAGS_TLS_DATA = 0xB2

CMD_NOP = 0x00
CMD_MCU_GET_IMAGE = 0x20
CMD_MCU_SWITCH_FDT_DOWN = 0x32
CMD_MCU_SWITCH_FDT_MODE = 0x36
CMD_WRITE_SENSOR_REG = 0x80
CMD_UPLOAD_CONFIG_MCU = 0x90
CMD_SET_DRV_STATE = 0xC4
CMD_REQUEST_TLS_CONNECTION = 0xD0
CMD_MCU_GET_POV_IMAGE = 0xD2

PSK_HEX = "0000000000000000000000000000000000000000000000000000000000000000"

OEM_CONFIG = bytes.fromhex(
    "701160712c9d2cc91ce518fd00fd00fd03ba000180ca0008008400bec38600b1"
    "b68800baba8a00b3b38c00bcbc8e00b1b19000bbbb9200b1b194000000960000"
    "00980000009a000000d2000000d4000000d6000000d800000050000105d00000"
    "00700000007200785674003412200010402a0102042200012024003200800001"
    "005c000101560024205800010232000402660000027c00005882007f082a0182"
    "072200012024001400800001405c000001560006145800040232000c02660000"
    "027c00005882007f082a0108005c000101540000016200080464001000660000"
    "027c0000582a0108005c00fb005200080054000001660000027c0000582002fc"
)

SENSOR_WIDTH = 80
SENSOR_HEIGHT = 64

def pad64(data: bytes) -> bytes:
    if len(data) % 64:
        return data + b"\x00" * (64 - (len(data) % 64))
    return data

def encode_cmd(cmd: int, data: bytes = b"", checksum: bool = True) -> bytes:
    payload = bytearray([cmd])
    payload.extend(struct.pack("<H", len(data) + 1))
    payload.extend(data)
    if checksum:
        chk = (0xAA - sum(payload)) & 0xFF
    else:
        chk = 0x88
    payload.append(chk)

    hdr = bytearray([FLAGS_MESSAGE])
    hdr.extend(struct.pack("<H", len(payload)))
    hdr.append(sum(hdr) & 0xFF)
    return pad64(bytes(hdr + payload))

def encode_tls(payload: bytes) -> bytes:
    hdr = bytearray([FLAGS_TLS])
    hdr.extend(struct.pack("<H", len(payload)))
    hdr.append(sum(hdr) & 0xFF)
    return pad64(bytes(hdr + payload))

def send_and_get_replies(ep_out, ep_in, cmd_bytes, expected_replies=2, timeout_ms=3000):
    for i in range(0, len(cmd_bytes), 64):
        ep_out.write(cmd_bytes[i:i+64], timeout=timeout_ms)

    replies = []
    for _ in range(expected_replies):
        try:
            r = bytes(ep_in.read(20480, timeout=timeout_ms))
            replies.append(r)
        except usb.core.USBTimeoutError:
            break
    return replies

def decode_12bit_pixels(data: bytes) -> list[int]:
    pixels = []
    for i in range(0, len(data), 6):
        chunk = data[i:i+6]
        if len(chunk) < 6:
            break
        p1 = ((chunk[0] & 0x0F) << 8) | chunk[1]
        p2 = (chunk[3] << 4) | (chunk[0] >> 4)
        p3 = ((chunk[5] & 0x0F) << 8) | chunk[2]
        p4 = (chunk[4] << 4) | (chunk[5] >> 4)
        pixels.extend([p1, p2, p3, p4])
    return pixels

def save_pgm(pixels: list[int], width: int, height: int, filename: str):
    with open(filename, "w") as f:
        f.write(f"P2\n{width} {height}\n4095\n")
        line = []
        for i, p in enumerate(pixels):
            line.append(f"{p:4d}")
            if len(line) >= 16:
                f.write(" ".join(line) + "\n")
                line = []
        if line:
            f.write(" ".join(line) + "\n")
    print(f"\n[***] SUCCESS! Fingerprint scan saved to: {filename} [***]")

def main():
    print("=" * 60)
    print(" Goodix 27c6:581a Live Fingerprint Capture")
    print("=" * 60)

    # 1. Start OpenSSL TLS-PSK server
    server = subprocess.Popen([
        "openssl", "s_server",
        "-nocert",
        "-psk", PSK_HEX,
        "-port", "4433",
        "-quiet"
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    time.sleep(0.4)

    try:
        # 2. Connect to USB
        dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        if not dev:
            print("[-] USB device not found.")
            return

        dev.set_configuration()
        cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]
        ep_out = intf[0]
        ep_in = intf[1]

        # Flush buffer
        try:
            while True:
                ep_in.read(2048, timeout=50)
        except usb.core.USBTimeoutError:
            pass

        # NOP
        ep_out.write(encode_cmd(CMD_NOP, bytes.fromhex("00000000")))
        try:
            ep_in.read(2048, timeout=200)
        except usb.core.USBTimeoutError:
            pass

        # 3. Perform TLS Handshake
        print("[*] Establishing encrypted TLS-PSK channel...")
        ep_out.write(encode_cmd(CMD_REQUEST_TLS_CONNECTION, b"\x00\x00"))
        
        ep_in.read(2048, timeout=1000)
        client_hello_raw = bytes(ep_in.read(2048, timeout=2000))

        sock = socket.socket()
        sock.connect(("localhost", 4433))
        sock.sendall(client_hello_raw[4:])
        server_hello = sock.recv(2048)

        tls_pkt = encode_tls(server_hello)
        for i in range(0, len(tls_pkt), 64):
            ep_out.write(tls_pkt[i:i+64])

        for _ in range(3):
            msg = bytes(ep_in.read(2048, timeout=2000))
            sock.sendall(msg[4:])

        server_finish = sock.recv(2048)
        finish_pkt = encode_tls(server_finish)
        for i in range(0, len(finish_pkt), 64):
            ep_out.write(finish_pkt[i:i+64])

        print("[+] Encrypted channel ready.")

        # 4. Upload OEM Config & Set Active
        print("[*] Uploading RAM sensor parameters...")
        send_and_get_replies(ep_out, ep_in, encode_cmd(CMD_UPLOAD_CONFIG_MCU, OEM_CONFIG), expected_replies=2)
        send_and_get_replies(ep_out, ep_in, encode_cmd(CMD_SET_DRV_STATE, bytes.fromhex("0100")), expected_replies=2)

        # 5. Arm AFE Registers
        send_and_get_replies(ep_out, ep_in, encode_cmd(CMD_MCU_GET_POV_IMAGE, b"\x00\x00"), expected_replies=2)
        fdt_payload = bytes.fromhex("0d0127012101270123010000000000000000000000000000000000")
        send_and_get_replies(ep_out, ep_in, encode_cmd(CMD_MCU_SWITCH_FDT_MODE, fdt_payload), expected_replies=2)

        reg_payload = b"\x00" + struct.pack("<H", 0x022C) + b"\x0a\x03"
        send_and_get_replies(ep_out, ep_in, encode_cmd(CMD_WRITE_SENSOR_REG, reg_payload), expected_replies=2)

        # 6. Prompt user to touch sensor
        print("\n" + "#" * 60)
        print(" >>> PLACE AND HOLD YOUR FINGER ON THE POWER BUTTON/SENSOR NOW <<<")
        print("#" * 60 + "\n")

        # Request image frame
        img_payload = bytes.fromhex("01032701210127012301")
        ep_out.write(encode_cmd(CMD_MCU_GET_IMAGE, img_payload))

        # Poll for image frame up to 10 seconds while user places finger
        img_packet = None
        for attempt in range(10):
            try:
                pkt = bytes(ep_in.read(20480, timeout=1000))
                if pkt[0] == FLAGS_TLS_DATA:
                    print(f"[+] Finger detected! Received encrypted frame: {len(pkt)} bytes")
                    img_packet = pkt
                    break
                else:
                    print(f"    Waiting for touch... (received status 0x{pkt[0]:02x})")
            except usb.core.USBTimeoutError:
                print(f"    Waiting for touch... ({10 - attempt}s remaining)")

        if img_packet:
            tls_data = img_packet[13:]
            sock.sendall(tls_data)
            decrypted = server.stdout.read(7684)

            if len(decrypted) >= 7680:
                raw_pixels = decrypted[:7680]
                pixels = decode_12bit_pixels(raw_pixels)
                save_pgm(pixels, SENSOR_WIDTH, SENSOR_HEIGHT, "fingerprint_touch.pgm")
            else:
                print(f"[-] Decrypted data incomplete: {len(decrypted)} bytes")
        else:
            print("\n[-] Timed out waiting for touch. (No image packet returned)")

        sock.close()

    except Exception as e:
        print(f"[-] Error: {e}")
    finally:
        server.terminate()

if __name__ == "__main__":
    main()
