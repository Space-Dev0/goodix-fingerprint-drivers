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

The 581a driver uses the full-width `0x0010` capture mode and averages eight
frames for both the ambient baseline and each scan. This count follows the
initial capture observations shared for this sensor; it increases capture
latency and still needs validation across more devices.

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
|  [128-D Descriptors]    (25 to 45 high-quality ridge keypoints)        |
|          |                                                              |
|          v                                                              |
|  [k-NN Descriptor Match] (Lowe's ratio test threshold = 0.90)           |
|          |                                                              |
|          v                                                              |
|  [Geometric Verification](Pairwise relative vectors: len=12%, ang=12%)  |
|          |                                                              |
|          v                                                              |
|  [Match Decision]        (stage >= 20; two stages or one stage >= 45)  |
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
(`0.12` radians).

Every pair of consistent angles increments the geometric score. The score is
therefore a small count of agreeing vector pairs, not a percentage. The
[reference SIGFM matcher](https://github.com/goodix-fp-linux-dev/sigfm/blob/master/cpp/match.cpp)
accepts at five agreeing pairs. The earlier 45/70 gates used in this
integration were on the wrong scale. The 581a port uses a two-part policy: a
stage counts when it reaches `SIGFM_MATCH_STAGE_SCORE_THRESHOLD` (20), and a
match succeeds with either `SIGFM_MATCH_MIN_STAGES` (2) such stages or one
stage reaching `SIGFM_MATCH_STRONG_SCORE_THRESHOLD` (45). These cutoffs are
provisional and have not been calibrated against a set of genuine and impostor
captures for the 581a.

---

## 4. Final Match Criteria & 12-Stage Enrollment

```c
/* Two corroborating stages, or one strongly scoring stage. */
if (matched_stages >= SIGFM_MATCH_MIN_STAGES ||
    max_score >= SIGFM_MATCH_STRONG_SCORE_THRESHOLD)
    result = FPI_MATCH_SUCCESS;
else
    result = FPI_MATCH_FAIL;
```

### 4.1 12-Stage Multi-Angle Enrollment

The sensor sees only a small part of the finger. For the 12 enrollment touches,
keep the pad centered and vary placement only slightly; lift fully between
touches. Large shifts, edge-only presses, and deliberate rotations reduce
overlap between probe and template. The verifier now needs two separate stages
to match, so repeatable coverage matters more than touring the whole fingertip.

## 5. Threshold validation

The per-stage score cutoff of `20` is a provisional acceptance threshold, not
a calibrated security boundary. A single stage reaching `45` may also accept a
match, so a false accept only needs one unusually strong impostor comparison.
Before relying on this policy, collect repeated genuine comparisons across
separate sessions and impostor comparisons across distinct fingers. Report
false match and false non-match rates; include the sensor, enrollment
procedure, image preprocessing, and number of comparisons. A tiny sample cannot
establish a low false-accept probability.
