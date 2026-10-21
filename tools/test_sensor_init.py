#!/usr/bin/env python3
"""
Test sensor initialization sequence:
1. Reset sensor array (0xA2 with 0x0514)
2. Read Chip ID via register 0x0000 (0x82)
3. Read OTP (0xA6)
"""

import struct
import usb.core
import usb.util
import time

VENDOR_ID = 0x27C6
PRODUCT_ID = 0x581A

FLAGS_MESSAGE = 0xA0
CMD_NOP = 0x00
CMD_RESET = 0xA2
CMD_READ_SENSOR_REG = 0x82
CMD_READ_OTP = 0xA6
CMD_ACK = 0xB0

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

def run():
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if not dev:
        print("[-] Device not found")
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
    ep_out = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
    )
    ep_in = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
    )

    def write_pack(data: bytes):
        pad_len = (64 - (len(data) % 64)) % 64
        padded = data + b"\x00" * pad_len
        ep_out.write(padded, timeout=2000)

    def read_pack(timeout_ms: int = 2000):
        raw = bytes(ep_in.read(16384, timeout=timeout_ms))
        flag = raw[0]
        length = struct.unpack("<H", raw[1:3])[0]
        payload = raw[4:4 + length]
        return flag, payload

    # 1. NOP
    print("[*] Sending NOP...")
    write_pack(encode_cmd(CMD_NOP, b"\x00\x00\x00\x00"))
    try:
        read_pack(timeout_ms=100)
    except Exception:
        pass

    # 2. Reset Sensor (0xA2 with 0x0514)
    print("[*] Sending CMD_RESET (0xA2) with payload 0514...")
    write_pack(encode_cmd(CMD_RESET, b"\x05\x14"))
    f_res, p_res = read_pack(timeout_ms=2000)
    c_res, d_res = decode_cmd(p_res)
    print(f"  [+] Reset response: cmd=0x{c_res:02x}, data={d_res.hex()}")

    # 3. Read Chip ID register (0x0000, 4 bytes)
    print("[*] Reading sensor register 0x0000 (Chip ID)...")
    reg_payload = b"\x00" + struct.pack("<H", 0x0000) + struct.pack("<B", 4)
    write_pack(encode_cmd(CMD_READ_SENSOR_REG, reg_payload))
    f_ack, p_ack = read_pack(timeout_ms=2000)
    c_ack, d_ack = decode_cmd(p_ack)
    print(f"  [+] Reg read ACK: cmd=0x{c_ack:02x}, data={d_ack.hex()}")
    f_reg, p_reg = read_pack(timeout_ms=2000)
    c_reg, d_reg = decode_cmd(p_reg)
    print(f"[***] Sensor Chip ID: {d_reg.hex()} [***]")

    # 4. Read OTP (0xA6 with 0x4000)
    print("[*] Reading OTP (0xA6)...")
    write_pack(encode_cmd(CMD_READ_OTP, b"\x40\x00"))
    f_ack, p_ack = read_pack(timeout_ms=2000)
    f_otp, p_otp = read_pack(timeout_ms=2000)
    c_otp, d_otp = decode_cmd(p_otp)
    print(f"[***] Sensor OTP ({len(d_otp)} bytes): {d_otp.hex()} [***]")

if __name__ == "__main__":
    run()
