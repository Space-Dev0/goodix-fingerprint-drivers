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
$$1280 \times 4\text{ pixels} = 5120\text{ pixels} = 64\text{ width} \times 80\text{ height}$$

### 1.2 Nibble Packing Bit-Layout

Given a 6-byte chunk $[C_0, C_1, C_2, C_3, C_4, C_5]$, the 12-bit pixel values $P_0, P_1, P_2, P_3$ are reconstructed as:

$$\begin{aligned}
P_0 &= ((C_0 \ \& \ 0\text{x}0f) \ll 8) \ | \ C_1 \\
P_1 &= (C_3 \ll 4) \ | \ (C_0 \gg 4) \\
P_2 &= ((C_5 \ \& \ 0\text{x}0f) \ll 8) \ | \ C_2 \\
P_3 &= (C_4 \ll 4) \ | \ (C_5 \gg 4)
\end{aligned}$$

#### C Implementation (`goodixtls5xx_decode_frame`):
```c
void goodixtls5xx_decode_frame (GoodixTls5xxPix *frame, guint32 frame_size,
                                const guint8 *raw_frame, guint16 header_len, guint16 footer_len)
{
  GoodixTls5xxPix *pix = frame;

  for (int i = header_len; i <= (int) frame_size - footer_len - 6; i += 6)
    {
      const guint8 *chunk = raw_frame + i;
      *pix++ = ((chunk[0] & 0x0f) << 8) | chunk[1];
      *pix++ = (chunk[3] << 4) | (chunk[0] >> 4);
      *pix++ = ((chunk[5] & 0x0f) << 8) | chunk[2];
      *pix++ = (chunk[4] << 4) | (chunk[5] >> 4);
    }
}
```

---

## 2. Mode 1 vs Mode 5 Scan Modes

The GM168 sensor supports two operational scan modes:

1. **Mode 1 (`goodix_581a_get_image_mode1`)**:
   - **Purpose**: Ambient air baseline calibration.
   - **Acquisition**: Captured once during device activation when no finger is present.
   - **Properties**: Captures static fixed-pattern noise (FPN), analog drift, and temperature-dependent capacitive offsets across individual capacitive cells.

2. **Mode 5 (`goodix_581a_get_image_mode5`)**:
   - **Purpose**: Live touch fingerprint scan.
   - **Acquisition**: Executed immediately after hardware FDT touch interrupt triggers.
   - **Properties**: Captures high-contrast ridge/valley patterns modulated onto the baseline capacitive dome.

---

## 3. High-Pass Spatial Filtering & Background Removal

Raw capacitive sensor readings cannot simply undergo direct thresholding because the natural spherical curvature of the human fingertip introduces a massive low-frequency DC dome (center pixels saturate while edge pixels have weak capacitive coupling):

```
Capacitive Signal Profile across Sensor:
  ADC Value
      ^
      |                 ***** Finger Ridge Signal *****
      |              **                               **
      |           **                                     **   <-- Low-Frequency
      |        **                                           **    Fingertip Dome
      |      *                                                 *
      +------------------------------------------------------------> Pixel Position (X)
```

To eliminate the dome while preserving 100% of the fingerprint ridge frequency, we apply a **7-tap separable binomial Gaussian low-pass filter**:

$$\text{Kernel: } K = \frac{1}{64} \begin{bmatrix} 1 & 6 & 15 & 20 & 15 & 6 & 1 \end{bmatrix}$$

### 3.1 Two-Dimensional Separable Convolution

1. **Horizontal Pass**:
   $$T(x, y) = \frac{1}{64} \sum_{k=-3}^{3} K(k) \cdot \text{Raw}(\text{clamp}(x+k), y)$$

2. **Vertical Pass**:
   $$\text{BG}(x, y) = \frac{1}{64} \sum_{k=-3}^{3} K(k) \cdot T(x, \text{clamp}(y+k))$$

3. **High-Pass Residual**:
   $$\text{HP}(x, y) = \text{Raw}(x, y) - \text{BG}(x, y)$$

Because the kernel width ($\pm 3$ pixels) matches the spatial wavelength of fingerprint ridges at 500 DPI (ridges are $2 \text{ to } 3$ pixels wide), the filter completely strips the finger dome and slow spatial gradients, leaving an isolated, zero-centered sinusoidal ridge profile.

---

## 4. Dynamic Percentile Contrast Stretching

Different users have vastly different skin moisture levels:
- Dry skin: weak signal ($\Delta \approx \pm 20$ ADC counts).
- Moist skin: strong signal ($\Delta \approx \pm 150$ ADC counts).

To ensure invariant dynamic range across all skin types, we compute the **5th percentile ($p_5$)** and **95th percentile ($p_{95}$)** of the high-pass residual:

```c
qsort (active_vals, n_active, sizeof (gint32), compare_ints);
p5 = active_vals[n_active * 5 / 100];
p95 = active_vals[n_active * 95 / 100];
if (p95 - p5 < 40)
  {
    p5 = -20;
    p95 = 20;
  }
```

Each pixel is then dynamically mapped to 8-bit grayscale:

$$\text{Pixel}_{\text{out}}(x, y) = 255 - \text{clamp}\left( \frac{\text{HP}(x, y) - p_5}{p_{95} - p_5} \times 255,\ 0,\ 255 \right)$$

Edges within 2 pixels of the physical sensor perimeter are masked to 255 (white) to eliminate boundary reflections.
