# Image Decoding & Preprocessing Pipeline

## 1. Raw Frame Structure & 12-bit Nibble Unpacking

The decrypted buffer returned by the sensor die measures exactly **7,684 bytes**.

### 1.1 Buffer Geometry Breakdown

```
+--------------------+-------------------------------------------+--------------------+
| Header (8 bytes)   | Active Pixel Data (7,680 bytes)           | Footer (4 bytes)   |
+--------------------+-------------------------------------------+--------------------+
| 0x00..0x07         | 6-byte packed chunks                      | Checksum / Status  |
+--------------------+-------------------------------------------+--------------------+
```

The active pixel area contains:
$$\frac{7680\text{ bytes}}{6\text{ bytes/chunk}} = 1280\text{ chunks}$$
Each 6-byte chunk encodes **4 pixels** with 12-bit resolution ($4 \times 12\text{ bits} = 48\text{ bits} = 6\text{ bytes}$):
$$1280 \times 4\text{ pixels} = 5120\text{ pixels} = 80\text{ columns} \times 64\text{ rows} = 80\text{ width} \times 64\text{ height}$$

### 1.2 Stream Packing & Physical Layout Transposition

Given a 6-byte chunk $[C_0, C_1, C_2, C_3, C_4, C_5]$, the 12-bit pixel values $P_0, P_1, P_2, P_3$ are unpacked sequentially:

$$\begin{aligned}
P_0 &= ((C_0 \ \& \ 0\text{x}0f) \ll 8) \ | \ C_1 \\
P_1 &= (C_3 \ll 4) \ | \ (C_0 \gg 4) \\
P_2 &= ((C_5 \ \& \ 0\text{x}0f) \ll 8) \ | \ C_2 \\
P_3 &= (C_4 \ll 4) \ | \ (C_5 \gg 4)
\end{aligned}$$

The 581a hardware transmits samples in **column-major order** (64 rows per column, 80 columns). Mapping this stream row-major results in a transposed image. The driver transposes stream indices to true physical $(x, y)$ coordinates:

$$x = \lfloor i / 64 \rfloor, \quad y = i \pmod{64}, \quad \text{idx} = y \times 80 + x$$

#### C Implementation (`goodix_581a_decode_frame`):
```c
void goodix_581a_decode_frame (GoodixTls5xxPix *frame, guint32 frame_size,
                               const guint8 *raw_frame, guint16 header_len, guint16 footer_len)
{
  GoodixTls5xxPix stream[GOODIX_581A_FRAME_SIZE];

  goodixtls5xx_decode_frame (stream, frame_size, raw_frame, header_len, footer_len);

  for (guint i = 0; i < GOODIX_581A_FRAME_SIZE; i++)
    frame[goodix_581a_stream_to_image_index (i)] = stream[i];
}
```

---

## 2. Mode 1 vs Mode 5 Scan Modes

The GM168 sensor supports two operational scan modes:

1. **Mode 1 (`goodix_581a_get_image_mode1`)**:
   - **Purpose**: Ambient air baseline calibration.
   - **Acquisition**: Captured before scanning when no finger is present; refreshed after device deactivation.
   - **Properties**: Captures static fixed-pattern noise (FPN), analog drift, and temperature-dependent capacitive offsets across individual capacitive cells.

2. **Mode 5 (`goodix_581a_get_image_mode5`)**:
   - **Purpose**: Live touch fingerprint scan.
   - **Acquisition**: Executed immediately after hardware FDT touch interrupt triggers.
   - **Properties**: Captures high-contrast ridge/valley patterns modulated onto the baseline capacitive dome.

---

## 3. Least-Squares Linear Flat-Fielding

Raw capacitive readings have low-frequency DC variations and thermal gain drift. Rather than using an arbitrary one-sided subtraction (`max(baseline - scan, 0)`) that clips valleys to zero, or a heavy 7-tap blur that attenuates ~69% of the 9–10px ridge signal, the driver performs **least-squares linear flat-fielding**:

$$S(x, y) \approx g \cdot B(x, y) + o$$

where $B$ is the untouched ambient baseline, $S$ is the active touch scan, $g$ is the fitted gain, and $o$ is the DC offset:

$$g = \frac{N \sum (B \cdot S) - \sum B \sum S}{N \sum B^2 - (\sum B)^2}, \quad o = \frac{\sum S - g \sum B}{N}$$

The flat-fielded residual is:
$$\text{Residual}(x, y) = S(x, y) - \left(g \cdot B(x, y) + o\right)$$

This keeps the residual **signed** (preserving both ridge crests and valleys), absorbs baseline DC shifts, and eliminates fixed-pattern cell noise without blurring ridge details.

---

## 4. Dynamic Percentile Contrast Stretching

To ensure invariant dynamic range across skin types (dry vs moist), the driver computes the **5th percentile ($p_5$)** and **95th percentile ($p_{95}$)** of active non-boundary pixels:

```c
qsort (active_vals, n_active, sizeof (gint32), compare_ints);
p5 = active_vals[n_active * 5 / 100];
p95 = active_vals[n_active * 95 / 100];
has_usable_contrast = (p95 - p5 >= 20);
```

When $p_{95} - p_5 \ge 20$, the residual is stretched onto 8-bit grayscale:

$$\text{Pixel}_{\text{out}}(x, y) = 255 - \text{clamp}\left( \frac{\text{Residual}(x, y) - p_5}{p_{95} - p_5} \times 255,\ 0,\ 255 \right)$$

Edges within 2 pixels of the perimeter are masked to 255 (white) to eliminate boundary edge artifacts.
