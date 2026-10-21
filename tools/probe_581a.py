#!/usr/bin/env python3
"""
Goodix 27c6:581a Hardware Probe & Diagnostics
Based on Neodyme research (tlambertz) and goodix-fp-dump (GM168 family).
"""

import sys
import time
import struct
import usb.core
import usb.util

VENDOR_ID = 0x27C6
PRODUCT_ID = 0x581A

FLAGS_MESSAGE_PROTOCOL = 0xA0

def encode_message_protocol(payload: bytes, command: int, checksum: bool = True):
    data = bytearray()
    data.append(command)
    data.extend(struct.pack("<H", len(payload) + 1))
    data.extend(payload)
    if checksum:
        chk = (0xAA - sum(data)) & 0xFF
    else:
        chk = 0x88
    data.append(chk)

    # Wrap in 0xA0 header
    wrapper = bytearray()
    wrapper.append(FLAGS_MESSAGE_PROTOCOL)
    wrapper.extend(struct.pack("<H", len(data)))
    wrapper.append(sum(wrapper) & 0xFF)
    wrapper.extend(data)

    # Pad to multiple of 64 bytes
    if len(wrapper) % 64 != 0:
        wrapper.extend(b"\x00" * (64 - (len(wrapper) % 64)))

    return bytes(wrapper)

def send_and_recv(ep_out, ep_in, cmd_bytes, desc="Command", timeout_ms=3000):
    print(f"\n[*] Sending {desc} ({len(cmd_bytes)} bytes): {cmd_bytes[:16].hex()}...")
    try:
        ep_out.write(cmd_bytes, timeout=timeout_ms)
        print(f"    [+] Write successful. Waiting for response...")
        resp = ep_in.read(2048, timeout=timeout_ms)
        print(f"    [+] Response ({len(resp)} bytes): {bytes(resp).hex()}")
        return bytes(resp)
    except usb.core.USBTimeoutError:
        print(f"    [-] Timeout waiting for reply.")
        return None
    except Exception as e:
        print(f"    [-] Error: {e}")
        return None

def main():
    print("=" * 60)
    print(" Goodix 27c6:581a (Xiaomi GM168) Protocol Probe")
    print("=" * 60)

    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        print(f"[-] Device {VENDOR_ID:04x}:{PRODUCT_ID:04x} not found on USB bus.")
        sys.exit(1)

    print(f"[+] Device found on Bus {dev.bus:03d} Device {dev.address:03d}")

    # Detach kernel driver if active
    try:
        if dev.is_kernel_driver_active(0):
            print("[*] Detaching kernel driver from interface 0...")
            dev.detach_kernel_driver(0)
    except Exception as e:
        pass

    try:
        dev.set_configuration()
        cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]
        ep_out = intf[0]  # EP 1 OUT
        ep_in = intf[1]   # EP 3 IN
        print(f"[+] Interface claimed. EP OUT: {hex(ep_out.bEndpointAddress)}, EP IN: {hex(ep_in.bEndpointAddress)}")
    except Exception as e:
        print(f"[-] Failed to claim interface: {e}")
        print("    Tip: Run with 'sudo' or install the udev rule.")
        sys.exit(1)

    # Test 1: NOP (Command 0x00)
    nop_cmd = encode_message_protocol(bytes.fromhex("00000000"), command=0x00)
    send_and_recv(ep_out, ep_in, nop_cmd, desc="NOP (0x00)")

    # Test 2: Firmware Version Query (Command 0xA8)
    fw_cmd = encode_message_protocol(bytes.fromhex("0000"), command=0xA8)
    resp = send_and_recv(ep_out, ep_in, fw_cmd, desc="Get FW Version (0xA8)")
    if resp and len(resp) > 7:
        try:
            fw_str = resp[7:].split(b"\x00")[0].decode("ascii", errors="replace")
            print(f"    [***] Firmware Identified: '{fw_str}'")
        except Exception:
            pass

    # Test 3: Read IAP Version (Command 0xF6)
    iap_cmd = encode_message_protocol(bytes.fromhex("0000"), command=0xF6)
    send_and_recv(ep_out, ep_in, iap_cmd, desc="Get IAP Version (0xF6)")

    # Test 4: Query MCU State (Command 0xAE)
    state_cmd = encode_message_protocol(bytes.fromhex("0000"), command=0xAE)
    send_and_recv(ep_out, ep_in, state_cmd, desc="Query MCU State (0xAE)")

    # Test 5: Read PSK Status (Command 0xE4)
    # 0xbb020001 (GM168 flag)
    psk_read_cmd = encode_message_protocol(bytes.fromhex("010002bb20000000"), command=0xE4)
    send_and_recv(ep_out, ep_in, psk_read_cmd, desc="PresetPskRead (0xE4)")

    print("\nProbe completed.")

if __name__ == "__main__":
    main()
