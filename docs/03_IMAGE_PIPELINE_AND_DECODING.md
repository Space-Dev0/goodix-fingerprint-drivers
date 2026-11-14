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

## 3. Baseline Subtraction and Ridge High-Pass

Raw capacitive readings have low-frequency DC variations and thermal drift. The driver subtracts the untouched ambient baseline directly:

$$\text{Residual}(x, y) = S(x, y) - B(x, y)$$

where $B$ is the ambient reference and $S$ is the active touch scan. Keeping the residual signed preserves both ridge crests and valleys; a one-sided `max(B - S, 0)` would clip one polarity.

A least-squares gain and offset are also fitted over pixels that are non-zero in at least one frame, which excludes inactive columns. They are reported in the journal for diagnostics only: applying them would absorb part of the finger's capacitive modulation and suppress ridge contrast.

A 3×3 box high-pass then removes the remaining slow baseline drift and illumination gradient, which would otherwise dominate the normalization range:

$$\text{HighPass}(x, y) = \text{Residual}(x, y) - \frac{1}{9}\sum_{dy=-1}^{1}\sum_{dx=-1}^{1}\text{Residual}(x+dx, y+dy)$$

Only one Mode 1 ambient frame and one Mode 5 touch frame are used per capture. The first Mode 5 frame after the FDT touch event contains the finger; asking this device for further Mode 5 frames can return idle data and average the ridge signal away. On this hardware, multi-frame averaging produced a noise-only image even though single frames showed clear ridges.

This is the image path that produced the validated captures, with roughly 140–195 SIFT keypoints per press. The journal also reports the lag-1 spatial autocorrelation (`ac_x`, `ac_y`) as a quick coherence check: coherent ridges give positive values, while noise stays near zero.

---

## 4. Dynamic Percentile Contrast Stretching

To ensure invariant dynamic range across skin types (dry vs moist), the driver computes the **3rd percentile ($p_3$)** and **97th percentile ($p_{97}$)** of the high-passed active non-boundary pixels:

```c
qsort (active_vals, n_active, sizeof (gint32), compare_ints);
p3 = active_vals[n_active * 3 / 100];
p97 = active_vals[n_active * 97 / 100];
has_usable_contrast = (p97 - p3 >= 12);
```

When $p_{97} - p_3 \ge 12$, the high-passed residual is stretched onto 8-bit grayscale:

$$\text{Pixel}_{\text{out}}(x, y) = 255 - \text{clamp}\left( \frac{\text{HighPass}(x, y) - p_3}{p_{97} - p_3} \times 255,\ 0,\ 255 \right)$$

The cutoff of 12 counts is provisional: it separates air from a finger on the captures used for tuning, but it has not been measured across a large device population.

Edges within 2 pixels of the perimeter are masked to 255 (white) to eliminate boundary edge artifacts.
