# Cryptography & Proprietary TLS Protocol

## 1. Background & Security Context

Modern Goodix fingerprint sensors communicate over an encrypted channel to prevent malicious USB devices or bus sniffers from intercepting raw biometric images (a vulnerability known as biometric replay attacks).

In late 2023, security researchers at **Neodyme** published research ([CVE-2023-45866](https://neodyme.io/blog/goodix/)) analyzing the Goodix Windows biometric driver stack. They discovered that:
1. The Windows driver and Goodix MCU negotiate standard **TLS 1.2** wrapped inside custom USB bulk packets.
2. The TLS handshake utilizes **TLS-PSK (Pre-Shared Key)** ciphersuites or custom ECDHE key exchanges.
3. On devices using Pre-Shared Keys, the PSK is shared across device series or derived deterministically, allowing open-source implementations to negotiate a secure channel with the stock firmware without flashing custom firmware.

---

## 2. TLS Architecture over USB

The Goodix protocol encapsulates TLS records directly inside Bulk-OUT and Bulk-IN transfers:

```
+-------------------------------------------------------------------------+
|                        Goodix USB Packet Structure                      |
|                                                                         |
|  Byte 0       Byte 1       Byte 2..3 (LE)   Byte 4..N                   |
| +-----------+------------+----------------+---------------------------+ |
| | Flag      | Command    | Length         | TLS Record Payload        | |
| | 0xb0/0xb2 | 0x00       | uint16         | (RFC 5246 TLS Record)     | |
| +-----------+------------+----------------+---------------------------+ |
+-------------------------------------------------------------------------+
```

### 2.1 Packet Encapsulation Formats

1. **Control / Handshake (`Flag = 0xb0`)**:
   Used for unencrypted or initial TLS handshake exchange:
   - ClientHello
   - ServerHello
   - ServerKeyExchange
   - ClientKeyExchange
   - ChangeCipherSpec / Finished

2. **Encrypted Application Data (`Flag = 0xb2`)**:
   Used once the TLS session is established:
   - Byte 0: `0xb2` (GOODIX_FLAGS_TLS_DATA)
   - Byte 1..8: Session counter / sequence / IV token
   - Byte 9..N: OpenSSL/GnuTLS encrypted payload

---

## 3. The TLS-PSK Handshake Sequence

The driver implements the client side of the TLS 1.2 handshake using OpenSSL:

```
Host (libfprint)                                MCU (Goodix 581a)
       |                                                |
       | ------ 1. ClientHello (TLS_PSK_WITH_AES) ----->|
       |                                                |
       | <----- 2. ServerHello -------------------------|
       | <----- 3. ServerKeyExchange (PSK Identity Hint)|
       | <----- 4. ServerHelloDone ---------------------|
       |                                                |
       | ------ 5. ClientKeyExchange (PSK Identity) --->|
       | ------ 6. ChangeCipherSpec ------------------->|
       | ------ 7. Finished (HMAC encrypted) ---------->|
       |                                                |
       | <----- 8. ChangeCipherSpec --------------------|
       | <----- 9. Finished (HMAC encrypted) -----------|
       |                                                |
================ Secure TLS Session Established ================
       |                                                |
       | ------ Encrypted Command (Mode 5 Scan) ------->|
       | <----- Encrypted 7,684-byte Image Data --------|
```

### 3.1 Pre-Shared Key (PSK) Matching

During `ClientKeyExchange`, the MCU requests authentication via a known Pre-Shared Key (PSK). For the 581a series, the key matches the standard Goodix 5xx PSK table:

```c
static const guint8 goodix_581a_psk[] = {
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xa0, 0xb0, 0xc0, 0xd0, 0xe0, 0xf0, 0x01, 0x02,
    0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0a
};
```

---

## 4. Encrypted Data Transmission & Decryption

Once the TLS tunnel is established, raw biometric image requests are issued through the TLS socket:

1. **Issue Command**: The host encrypts `goodix_581a_get_image_mode5` (or Mode 1 for calibration) using `SSL_write()`.
2. **Transfer**: The encrypted payload is wrapped with header byte `0xb2` and sent across Bulk OUT `0x01`.
3. **Receive**: The MCU captures the image from the sensor die into internal SRAM, encrypts it with AES-GCM / AES-CBC, and transmits it via Bulk IN `0x83` in multiple 4,096-byte USB chunks.
4. **Reassembly & Decryption**:
   - The driver strips the 9-byte transport header.
   - Feeds the bytes into `SSL_read()`.
   - Produces the exact 7,684-byte decrypted binary buffer containing raw pixel data.
