#!/usr/bin/env python3
"""
Goodix 27c6:581a PSK Reader & Handshake Test
Validates firmware response and tests reading / negotiating TLS-PSK.
"""

import sys
import time
import struct
import usb.core
import usb.util

VENDOR_ID = 0x27C6
PRODUCT_ID = 0x581A

FLAGS_MESSAGE_PROTOCOL = 0xA0

def encode_packet(command: int, payload: bytes = b"", checksum: bool = True):
    data = bytearray()
    data.append(command)
    data.extend(struct.pack("<H", len(payload) + 1))
    data.extend(payload)
    if checksum:
        chk = (0xAA - sum(data)) & 0xFF
    else:
        chk = 0x88
    data.append(chk)

    # 0xA0 wrapper
    wrapper = bytearray()
    wrapper.append(FLAGS_MESSAGE_PROTOCOL)
    wrapper.extend(struct.pack("<H", len(data)))
    wrapper.append(sum(wrapper) & 0xFF)
    wrapper.extend(data)

    # Pad to 64 bytes
    if len(wrapper) % 64 != 0:
        wrapper.extend(b"\x00" * (64 - (len(wrapper) % 64)))

    return bytes(wrapper)

def read_packet(ep_in, timeout_ms=3000):
    try:
        raw = bytes(ep_in.read(2048, timeout=timeout_ms))
        if len(raw) < 4:
            return raw
        # Unwrap 0xA0 if present
        if raw[0] == 0xA0:
            payload_len = struct.unpack("<H", raw[1:3])[0]
            inner = raw[4:4+payload_len]
            return inner
        return raw
    except usb.core.USBTimeoutError:
        return None

def send_cmd(ep_out, ep_in, cmd, payload=b"", reply_count=2, desc="Cmd", timeout_ms=3000):
    pkt = encode_packet(cmd, payload)
    ep_out.write(pkt, timeout=timeout_ms)

    replies = []
    for i in range(reply_count):
        r = read_packet(ep_in, timeout_ms)
        if r is not None:
            replies.append(r)
        else:
            break
    return replies

def main():
    print("=" * 60)
    print(" Goodix 27c6:581a PSK & Firmware Verification")
    print("=" * 60)

    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        print("[-] Device not found.")
        sys.exit(1)

    dev.set_configuration()
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    ep_out = intf[0]
    ep_in = intf[1]

    # Flush any lingering packets from previous sessions
    print("[*] Flushing input buffer...")
    while True:
        try:
            flushed = ep_in.read(2048, timeout=100)
            print(f"    Flushed {len(flushed)} bytes")
        except usb.core.USBTimeoutError:
            break

    # 1. Send NOP (0x00) -> expects 1 reply (ACK)
    print("\n[*] Sending NOP (0x00)...")
    res = send_cmd(ep_out, ep_in, 0x00, bytes.fromhex("00000000"), reply_count=1, desc="NOP")
    for r in res:
        print(f"    Reply: {r.hex()}")

    # 2. Query FW Version (0xA8) -> expects 2 replies (ACK + Data)
    print("\n[*] Querying Firmware Version (0xA8)...")
    res = send_cmd(ep_out, ep_in, 0xA8, bytes.fromhex("0000"), reply_count=2, desc="FW Version")
    for idx, r in enumerate(res):
        print(f"    Reply {idx+1}: {r.hex()}")
        if len(r) > 4 and r[0] == 0xA8:
            fw_string = r[3:].split(b"\x00")[0].decode("ascii", errors="replace")
            print(f"    ==> FIRMWARE: {fw_string}")

    # 3. Read PSK (0xE4)
    # Using GM168 flags: 0xbb020001, length=32 (0x20), offset=0
    print("\n[*] Reading PSK Status (0xE4)...")
    psk_payload = struct.pack("<IHH", 0xBB020001, 32, 0)
    res = send_cmd(ep_out, ep_in, 0xE4, psk_payload, reply_count=2, desc="Read PSK")
    for idx, r in enumerate(res):
        print(f"    Reply {idx+1}: {r.hex()}")
        if len(r) > 10 and r[0] == 0xE4:
            pmk_hash = r[9:9+32]
            print(f"    ==> Current Sensor PMK Hash ({len(pmk_hash)} bytes): {pmk_hash.hex()}")

    print("\n[+] Verification complete!")

if __name__ == "__main__":
    main()
