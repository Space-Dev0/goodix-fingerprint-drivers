#!/usr/bin/env python3
"""
Test TLS-PSK Handshake on Goodix 27c6:581a
Establishes an encrypted session using the provisioned all-zero PSK.
"""

import sys
import time
import socket
import subprocess
sys.path.append("goodix-fp-dump")
import goodix
import protocol
import tool

PSK = bytes.fromhex("0000000000000000000000000000000000000000000000000000000000000000")

print("[*] Starting local OpenSSL TLS-PSK server on port 4433...")
tls_server = subprocess.Popen([
    "openssl", "s_server", "-nocert", "-psk", PSK.hex(), "-port", "4433", "-quiet"
], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

time.sleep(0.5)

try:
    print("[*] Connecting to Goodix 27c6:581a...")
    dev = goodix.Device(0x581a, protocol.USBProtocol)
    dev.nop()

    print("[*] Connecting socket to local OpenSSL...")
    tls_client = socket.socket()
    tls_client.connect(("localhost", 4433))

    try:
        print("[*] Performing TLS-PSK handshake over USB...")
        tool.connect_device(dev, tls_client)
        print("\n" + "=" * 60)
        print(" [***] TLS-PSK HANDSHAKE COMPLETED SUCCESSFULLY! [***]")
        print("=" * 60)
    finally:
        tls_client.close()
finally:
    tls_server.terminate()
