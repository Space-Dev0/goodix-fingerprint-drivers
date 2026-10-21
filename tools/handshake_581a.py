#!/usr/bin/env python3
"""
TLS Handshake Runner for Goodix 27c6:581a
Properly handles the ACK before ClientHello.
"""

import sys
import time
import socket
import struct
import subprocess
sys.path.append("goodix-fp-dump")
import goodix
import protocol

PSK_HEX = "0000000000000000000000000000000000000000000000000000000000000000"

def main():
    print("=" * 60)
    print(" Goodix 27c6:581a TLS Handshake Engine")
    print("=" * 60)

    # 1. Start OpenSSL TLS-PSK server
    print("[*] Spawning OpenSSL TLS-PSK server on port 4433...")
    server = subprocess.Popen([
        "openssl", "s_server",
        "-nocert",
        "-psk", PSK_HEX,
        "-port", "4433",
        "-quiet"
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    time.sleep(0.5)

    try:
        # 2. Connect to device
        print("[*] Initializing device...")
        dev = goodix.Device(0x581a, protocol.USBProtocol)
        dev.nop()

        # 3. Request TLS Connection (0xD0)
        print("[*] Sending Command 0xD0 (Request TLS)...")
        dev.protocol.write(
            goodix.encode_message_pack(
                goodix.encode_message_protocol(b"\x00\x00", goodix.COMMAND_REQUEST_TLS_CONNECTION)
            )
        )

        ack = dev.protocol.read()
        print(f"[+] Received ACK for 0xD0 ({len(ack)} bytes): {ack.hex()}")

        client_hello_raw = dev.protocol.read()
        print(f"[+] Received TLS ClientHello ({len(client_hello_raw)} bytes): {client_hello_raw.hex()[:32]}...")

        # Strip 4-byte 0xB0 header (e.g. b0 34 00 e4)
        client_hello = client_hello_raw[4:]

        # 4. Proxy to OpenSSL
        print("[*] Forwarding ClientHello to OpenSSL server...")
        sock = socket.socket()
        sock.connect(("localhost", 4433))
        sock.sendall(client_hello)

        server_hello = sock.recv(2048)
        print(f"[+] OpenSSL generated ServerHello ({len(server_hello)} bytes): {server_hello.hex()[:32]}...")

        # 5. Send ServerHello to sensor
        print("[*] Forwarding ServerHello to sensor...")
        dev.protocol.write(
            goodix.encode_message_pack(server_hello, goodix.FLAGS_TRANSPORT_LAYER_SECURITY)
        )

        # 6. Sensor replies with 3 TLS messages
        print("[*] Reading sensor key exchange and handshake messages...")
        for i in range(3):
            msg = dev.protocol.read()
            print(f"    Message {i+1} ({len(msg)} bytes): {msg.hex()[:24]}...")
            sock.sendall(msg[4:])

        # 7. OpenSSL sends Finished
        server_finish = sock.recv(2048)
        print(f"[+] OpenSSL Handshake Finished ({len(server_finish)} bytes)")

        # Forward Finished to sensor
        dev.protocol.write(
            goodix.encode_message_pack(server_finish, goodix.FLAGS_TRANSPORT_LAYER_SECURITY)
        )

        print("\n" + "=" * 60)
        print(" [***] SUCCESS: TLS-PSK HANDSHAKE ESTABLISHED WITH SENSOR! [***]")
        print("=" * 60)

        sock.close()

    finally:
        server.terminate()

if __name__ == "__main__":
    main()
