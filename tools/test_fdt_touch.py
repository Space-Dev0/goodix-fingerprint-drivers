#!/usr/bin/env python3
"""
Test Finger Down Detection (FDT) on Goodix 27c6:581a
Verifies that the sensor detects physical finger touch.
"""

import sys
import struct
import usb.core
import usb.util

VENDOR_ID = 0x27C6
PRODUCT_ID = 0x581A

FLAGS_MESSAGE = 0xA0
CMD_NOP = 0x00
CMD_UPLOAD_CONFIG_MCU = 0x90
CMD_SET_DRV_STATE = 0xC4
CMD_MCU_GET_POV_IMAGE = 0xD2
CMD_MCU_SWITCH_FDT_MODE = 0x36
CMD_MCU_SWITCH_FDT_DOWN = 0x32

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

def send_and_ack(ep_out, ep_in, cmd, data=b"", read_data=True, timeout_ms=2000):
    cmd_bytes = encode_cmd(cmd, data)
    for i in range(0, len(cmd_bytes), 64):
        ep_out.write(cmd_bytes[i:i+64], timeout=timeout_ms)
    # 1. Read ACK
    ack = bytes(ep_in.read(2048, timeout=timeout_ms))
    # 2. Read Data if expected
    data_resp = None
    if read_data:
        data_resp = bytes(ep_in.read(2048, timeout=timeout_ms))
    return ack, data_resp

def main():
    print("=" * 60)
    print(" Goodix 27c6:581a Finger Touch Detection Test")
    print("=" * 60)

    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if not dev:
        print("[-] Device not found")
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

    print("[*] Sending NOP...")
    try:
        send_and_ack(ep_out, ep_in, CMD_NOP, bytes.fromhex("00000000"), read_data=False, timeout_ms=300)
    except usb.core.USBTimeoutError:
        pass

    print("[*] Uploading RAM config...")
    send_and_ack(ep_out, ep_in, CMD_UPLOAD_CONFIG_MCU, OEM_CONFIG, read_data=True)

    print("[*] Setting driver active (0xC4)...")
    send_and_ack(ep_out, ep_in, CMD_SET_DRV_STATE, bytes.fromhex("0100"), read_data=False)

    print("[*] Initializing POV mode (0xD2)...")
    send_and_ack(ep_out, ep_in, CMD_MCU_GET_POV_IMAGE, b"\x00\x00", read_data=True)

    print("[*] Configuring FDT baseline (0x36)...")
    fdt_init_payload = bytes.fromhex("0d0127012101270123010000000000000000000000000000000000")
    send_and_ack(ep_out, ep_in, CMD_MCU_SWITCH_FDT_MODE, fdt_init_payload, read_data=False)

    # Arm finger down detection
    print("[*] Arming capacitive touch detection (0x32)...")
    fdt_down_payload = bytes.fromhex("9c0127012101270123018d8d868697978f8f9b9b929296968c8c01000503a700a100a700a30000")
    
    # Send 0x32 command
    cmd_bytes = encode_cmd(CMD_MCU_SWITCH_FDT_DOWN, fdt_down_payload)
    for i in range(0, len(cmd_bytes), 64):
        ep_out.write(cmd_bytes[i:i+64], timeout=1000)
    
    # Read ACK for 0x32
    ack = bytes(ep_in.read(2048, timeout=2000))
    print(f"[+] Sensor armed! ACK: {ack.hex()}")

    print("\n" + "#" * 60)
    print(" >>> TOUCH SENSOR WITH YOUR FINGER NOW (Waiting up to 15s) <<<")
    print("#" * 60 + "\n")

    # Read with 15 second timeout waiting for finger down interrupt!
    try:
        touch_event = bytes(ep_in.read(2048, timeout=15000))
        print(f"\n[***] TOUCH DETECTED! Response ({len(touch_event)} bytes): {touch_event.hex()} [***]")
        if len(touch_event) >= 4 and touch_event[0] == FLAGS_MESSAGE:
            print("    Capacitive touch event successfully recognized by hardware!")
    except usb.core.USBTimeoutError:
        print("\n[-] No touch detected within 15 seconds.")

if __name__ == "__main__":
    main()
