# Biometric Matching Engine: SIGFM & SIFT Tuning

## 1. Why Traditional Minutiae (NBIS / Bozorth3) Failed

The standard biometric engine in `libfprint` relies on **NIST Biometric Image Software (NBIS)**:
- **`mindtct`**: Extracts minutiae points (ridge endings and bifurcations).
- **`bozorth3`**: Performs graph-based minutiae constellation matching.

### 1.1 The Physical Area Problem

NBIS was developed for FBI law-enforcement standard 500 DPI rolled prints measuring **$1.5\text{ inches} \times 1.5\text{ inches}$ ($38.1\text{ mm} \times 38.1\text{ mm}$)**, containing **50 to 120 minutiae**.

On the Goodix 581a:
$$\text{Sensor Size: } 3.25\text{ mm} \times 4.06\text{ mm} \quad (\approx 13\text{ mm}^2)$$
The area is **less than 1%** of a standard rolled fingerprint card. A typical touch captures only **3 to 7 true minutiae**.

When Bozorth3 attempts to match two 5-minutiae sets:
1. Slight fingertip skin elasticity (pressing harder or softer) shifts minutiae coordinates by $1\text{ to }2$ pixels.
2. In a 5-point set, losing 2 minutiae drops the match count below the statistical significance threshold, causing **false rejection of registered fingers**.
3. Artificially lowering the threshold causes random minutiae constellations between different fingers to collide, causing **false acceptance of wrong fingers**.

---

## 2. SIGFM: SIFT-Based Geometric Fingerprint Matching

To solve the small-area limitation, we integrated **SIGFM** (Scale-Invariant Feature Transform Fingerprint Matcher).

Instead of reducing the entire image to sparse minutiae dots, SIFT treats the ridge flow as continuous local gradient texture. Every ridge bifurcation, curvature transition, frequency change, and ridge termination generates high-dimensional (128-byte) gradient orientation histograms.

```
+-------------------------------------------------------------------------+
|                        SIGFM Processing Pipeline                        |
|                                                                         |
|  [64x80 Native Image]                                                   |
|          |                                                              |
|          v                                                              |
|  [CLAHE Local Contrast]  (clipLimit=2.0, tileGridSize=8x8)              |
|          |                                                              |
|          v                                                              |
|  [SIFT Feature Detector] (sigma=1.2, contrastThreshold=0.04)           |
|          |                                                              |
|          v                                                              |
|  [128-D Descriptors]    (25 to 45 high-quality ridge keypoints)        |
|          |                                                              |
|          v                                                              |
|  [k-NN Descriptor Match] (Lowe's ratio test threshold = 0.75)           |
|          |                                                              |
|          v                                                              |
|  [Geometric Verification](Pairwise relative vectors: len=12%, ang=12%)  |
|          |                                                              |
|          v                                                              |
|  [Match Decision]        (max_score >= 70 || score>=45 & stages>=2)     |
+-------------------------------------------------------------------------+
```

---

## 3. Mathematical Analysis & Critical Parameter Tuning

### 3.1 Eliminating Fixed-Pattern Noise: CLAHE Tuning

In low-contrast or partially touched frames, unpressed sensor cells produce low-amplitude thermal noise.

- **Initial Setting (`clipLimit = 4.0`, `tileGridSize = 4x4`)**:
  - Divided the 64×80 image into 16 tiny $16 \times 20$ tiles.
  - In background tiles, flat noise was amplified by $4.0\times$, generating **98 to 152 false keypoints** per frame.
  - Because fixed-pattern noise is physically identical across different touches, false keypoints collided between different fingers, scoring **57 to 66** and slipping past threshold 50.
- **Tuned Setting (`clipLimit = 2.0`, `tileGridSize = 8x8`)**:
  - Divides the image into $8 \times 10$ tiles.
  - Mild 2.0 clip limit provides smooth local dynamic range without over-amplifying sensor cell noise.

### 3.2 Preserving 500 DPI Ridges: SIFT Gaussian Sigma

In OpenCV SIFT, `sigma` governs the initial Gaussian blur applied before computing octave difference-of-Gaussians (DoG):

$$\text{Gaussian Blur: } G(x, y, \sigma) = \frac{1}{2\pi\sigma^2} e^{-\frac{x^2+y^2}{2\sigma^2}}$$

- At the documented 500 DPI, one pixel is about 51 micrometres; ridge width and local contrast vary across fingers and sensor captures.
- The configured **`sigma = 1.2`** is a heuristic intended to retain local ridge texture. This repository does not include raw images or a repeatable benchmark supporting prior numeric shift or impostor-score claims, so those values should not be used to characterize performance.

### 3.3 Rejecting Cross-Finger Collisions: Lowe's Ratio Test

When matching probe descriptors against gallery descriptors using k-Nearest Neighbors ($k=2$):

$$\text{Lowe's Condition: } \frac{\|\mathbf{D}_{\text{probe}} - \mathbf{D}_{\text{best}}\|_2}{\|\mathbf{D}_{\text{probe}} - \mathbf{D}_{\text{second}}\|_2} < \text{distance\_match}$$

- The configured **`0.75`** ratio removes some ambiguous nearest-neighbor matches. Its effect depends on the captured images and is not a guarantee of a particular false-match or false-accept rate.

### 3.4 Skin Elasticity Compensation: Pairwise Vector Invariance

For every pair of matched keypoints $(m_1, m_2)$ where $m_1 = (p_1, q_1)$ and $m_2 = (p_2, q_2)$:

$$\begin{aligned}
\mathbf{v}_1 &= p_1 - p_2, \quad &L_1 &= \|\mathbf{v}_1\| \\
\mathbf{v}_2 &= q_1 - q_2, \quad &L_2 &= \|\mathbf{v}_2\|
\end{aligned}$$

Relative distance must satisfy skin deformation tolerance:
$$1 - \frac{\min(L_1, L_2)}{\max(L_1, L_2)} \le \text{length\_match} \quad (0.12)$$

For each pair of correspondences, the matcher estimates the rotation from the probe vector to the enrolled vector using `atan2(cross, dot)`. Pair hypotheses agree when their wrapped angular difference is at most `angle_match` (0.12 radians).

Every pair of consistent vectors increments the geometric histogram. Because genuine matches have $20\text{ to }30$ mutually consistent keypoints, their score scales quadratically ($\binom{25}{2} = 300$ to $\binom{40}{2} = 780$). Accidental single-point collisions have no mutually consistent neighbors and score 0.

---

## 4. Final Match Criteria & 12-Stage Enrollment

```c
/* Two-Tier Evaluation in fpi-image-device.c */
if (max_score >= 70 || (max_score >= 45 && matched_stages >= 2))
    result = FPI_MATCH_SUCCESS;
else
    result = FPI_MATCH_FAIL;
```

### 4.1 12-Stage Multi-Angle Enrollment

With a tiny $3.25\text{ mm} \times 4.06\text{ mm}$ sensor, 8 touches left unmapped gaps on the fingertip perimeter. Setting `dev_class->nr_enroll_stages = 12` prompts the user through:
- 4 touches: Center pad (flat, firm, light)
- 4 touches: Lateral edges (left 45°, right 45°)
- 4 touches: Distal tip, knuckle, and rotational tilts

Additional stages can cover more of the finger pad, but do not guarantee overlap or a particular false-accept rate. Enrollment prompts should encourage different placements while retaining enough repeated center touches to capture the same region consistently.

## 5. Threshold validation

The configured score thresholds (`70` for a single stage, or `45` with at least two stages scoring `40`) are implementation defaults, not calibrated security thresholds. Before changing them for a deployment, collect repeated genuine comparisons across separate sessions and impostor comparisons across distinct fingers. Report false match and false non-match rates at the selected threshold; include the sensor, enrollment procedure, image preprocessing, and number of comparisons. A tiny sample cannot establish a low false-accept probability.
