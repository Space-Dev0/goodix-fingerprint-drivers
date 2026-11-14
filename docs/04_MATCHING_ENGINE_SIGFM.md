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

The 581a driver uses the authentic hardware configuration table (image tcode
`0x0100`) and captures one Mode 1 ambient frame and one Mode 5 touch frame per
press. This is the configuration and capture path that produced visible dermal
ridges in the userspace capture.

Instead of reducing the entire image to sparse minutiae dots, SIFT treats the ridge flow as continuous local gradient texture. Every ridge bifurcation, curvature transition, frequency change, and ridge termination generates high-dimensional (128-byte) gradient orientation histograms.

```
+-------------------------------------------------------------------------+
|                        SIGFM Processing Pipeline                        |
|                                                                         |
|  [80x64 Native Image]                                                   |
|          |                                                              |
|          v                                                              |
|  [CLAHE Local Contrast]  (clipLimit=2.0, tileGridSize=8x8)              |
|          |                                                              |
|          v                                                              |
|  [SIFT Feature Detector] (sigma=1.2, contrastThreshold=0.04)           |
|          |                                                              |
|          v                                                              |
|  [128-D Descriptors]    (typically dozens of ridge keypoints per press) |
|          |                                                              |
|          v                                                              |
|  [k-NN Descriptor Match] (Lowe's ratio test threshold = 0.90)           |
|          |                                                              |
|          v                                                              |
|  [Geometric Verification](common scale, rotation, then translation)     |
|          |                                                              |
|          v                                                              |
|  [Match Decision]        (inliers >= 8 and >= 20%; 3 stages or one >= 12)
+-------------------------------------------------------------------------+
```

---

## 3. Mathematical Analysis & Critical Parameter Tuning

### 3.1 Eliminating Fixed-Pattern Noise: CLAHE Tuning

In low-contrast or partially touched frames, unpressed sensor cells produce low-amplitude thermal noise.

- **Initial Setting (`clipLimit = 4.0`, `tileGridSize = 4x4`)**:
  - Divided the 80×64 image into 16 tiles of $20 \times 16$ pixels.
  - In background tiles, flat noise was amplified by $4.0\times$, generating **98 to 152 false keypoints** per frame.
  - Because fixed-pattern noise is physically identical across different touches, false keypoints collided between different fingers, scoring **57 to 66** and slipping past the acceptance threshold in use at the time.
- **Tuned Setting (`clipLimit = 2.0`, `tileGridSize = 8x8`)**:
  - Divides the image into $10 \times 8$ tiles.
  - Mild 2.0 clip limit provides smooth local dynamic range without over-amplifying sensor cell noise.

### 3.2 Preserving 500 DPI Ridges: SIFT Gaussian Sigma

In OpenCV SIFT, `sigma` governs the initial Gaussian blur applied before computing octave difference-of-Gaussians (DoG):

$$\text{Gaussian Blur: } G(x, y, \sigma) = \frac{1}{2\pi\sigma^2} e^{-\frac{x^2+y^2}{2\sigma^2}}$$

- At the documented 500 DPI, one pixel is about 51 micrometres; ridge width and local contrast vary across fingers and sensor captures.
- The configured **`sigma = 1.2`** is a heuristic intended to retain local ridge texture. This repository does not include raw images or a repeatable benchmark supporting prior numeric shift or impostor-score claims, so those values should not be used to characterize performance.

### 3.3 Rejecting Cross-Finger Collisions: Lowe's Ratio Test

When matching probe descriptors against gallery descriptors using k-Nearest Neighbors ($k=2$):

$$\text{Lowe's Condition: } \frac{\|\mathbf{D}_{\text{probe}} - \mathbf{D}_{\text{best}}\|_2}{\|\mathbf{D}_{\text{probe}} - \mathbf{D}_{\text{second}}\|_2} < \text{distance\_match}$$

- The configured **`0.90`** ratio retains more candidates for this 80×64 sensor. A test on one device showed the previous `0.75` threshold yielded fewer than four candidates for each enrolled stage, preventing geometric verification from running. Reciprocal matching and geometric consistency still filter candidates. This threshold is provisional and has not been validated against a multi-finger capture set, so it does not establish a false-accept rate.

### 3.4 Skin Elasticity Compensation: Pairwise Vector Invariance

For every pair of matched keypoints $(m_1, m_2)$ where $m_1 = (p_1, q_1)$ and $m_2 = (p_2, q_2)$:

$$\begin{aligned}
\mathbf{v}_1 &= p_1 - p_2, \quad &L_1 &= \|\mathbf{v}_1\| \\
\mathbf{v}_2 &= q_1 - q_2, \quad &L_2 &= \|\mathbf{v}_2\|
\end{aligned}$$

The matcher now estimates a common scale as the median of valid pair ratios
`L2 / L1`, bounded to `0.70..1.43`. Each pair must be within 20% of that
median. This allows a consistent global size change from a different press
while rejecting pairwise scales that disagree with the common transform.

For each pair of correspondences, the matcher estimates rotation from the
probe vector to the enrolled vector using `atan2(cross, dot)`. Pair hypotheses
agree when their wrapped angular difference is at most `angle_match`
(`0.12` radians). The matcher finds the dominant rotation and refines it from
the circular mean of its inliers.

Rotation alone is not enough. Unrelated presses can coincidentally share a
rotation cluster, and the original score counted every agreeing pair, so a
stage rich in mostly-spurious matches could pass on volume alone. After the
common scale and rotation are fixed, the matcher projects every participating
match through that transform:

$$\mathbf{t}_i = \mathbf{q}_i - s R(\theta)\,\mathbf{p}_i$$

Genuine correspondences all map to the same translation $\mathbf{t}$, while
chance matches scatter across the image. The stage score is the size of the
largest translation cluster, measured with a tolerance of four pixels (about
one ridge pitch on this sensor).

A stage only counts when at least `8` reciprocal matches agree on the
transform and those inliers are at least `20%` of all reciprocal matches.
Verification succeeds with `SIGFM_MIN_STAGES` (`3`) corroborating stages or one
stage reaching `SIGFM_STRONG_INLIERS` (`12`).

In a labelled test, three successful genuine presses scored 14, 16 and 24
inliers and were accepted; three presses of a different finger scored zero in
every stage. A poor genuine press also scored zero and required another press.

The cutoffs remain provisional: they come from a small labelled set on one
device and are not a measured false-accept rate.

---

## 4. Final Match Criteria & 12-Stage Enrollment

```c
/* Three corroborating stages, or one strongly supported stage. */
if (matched_stages >= SIGFM_MIN_STAGES ||
    max_inliers >= SIGFM_STRONG_INLIERS)
    result = FPI_MATCH_SUCCESS;
else
    result = FPI_MATCH_FAIL;
```

`SIGFM_STAGE_MIN_INLIERS` (`8`) is the per-stage gate; `SIGFM_STRONG_INLIERS`
(`12`) is the single-stage path; `SIGFM_MIN_STAGES` (`3`) is the corroboration
path.

### 4.1 12-Stage Multi-Angle Enrollment

The sensor sees only a small part of the finger. For the 12 enrollment touches,
keep the pad centered and vary placement only slightly; lift fully between
touches. Large shifts, edge-only presses, and deliberate rotations reduce
overlap between probe and template. The verifier now needs three separate
stages to corroborate a match, so repeatable coverage matters more than touring
the whole fingertip.

## 5. Threshold validation

The `8`-inlier per-stage cutoff, the 20% inlier-ratio floor, the `3`-stage
requirement, and the `12` strong-stage cutoff are provisional acceptance
thresholds, not a calibrated security boundary. A single stage reaching `12`
may also accept a match, so a false accept only needs one unusually strong
impostor comparison. Before relying on this policy, collect repeated genuine
comparisons across separate sessions and impostor comparisons across distinct
fingers. Report false match and false non-match rates; include the sensor,
enrollment procedure, image preprocessing, and number of comparisons. A tiny
sample cannot establish a low false-accept probability.
