"""EQ response curve.

Same approach as the FiiO Control web app: RBJ biquad coefficients per band,
evaluated at 48 kHz over log-spaced points, summed in dB. Rendered as text so it
costs nothing to draw in a terminal.
"""

import cmath
import math

FS = 48000.0
F_MIN, F_MAX = 20.0, 20000.0


def _coeffs(kind, f0, gain_db, q):
    """RBJ biquad coefficients as (b0, b1, b2, a0, a1, a2)."""
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * max(1.0, f0) / FS
    cos_w0, sin_w0 = math.cos(w0), math.sin(w0)
    q = max(0.01, q)
    alpha = sin_w0 / (2.0 * q)
    sqrt_a = math.sqrt(a) if a > 0 else 0.0

    if kind == "peak":
        return (1 + alpha * a, -2 * cos_w0, 1 - alpha * a,
                1 + alpha / a, -2 * cos_w0, 1 - alpha / a)
    if kind == "low-shelf":
        return (a * ((a + 1) - (a - 1) * cos_w0 + 2 * sqrt_a * alpha),
                2 * a * ((a - 1) - (a + 1) * cos_w0),
                a * ((a + 1) - (a - 1) * cos_w0 - 2 * sqrt_a * alpha),
                (a + 1) + (a - 1) * cos_w0 + 2 * sqrt_a * alpha,
                -2 * ((a - 1) + (a + 1) * cos_w0),
                (a + 1) + (a - 1) * cos_w0 - 2 * sqrt_a * alpha)
    if kind == "high-shelf":
        return (a * ((a + 1) + (a - 1) * cos_w0 + 2 * sqrt_a * alpha),
                -2 * a * ((a - 1) + (a + 1) * cos_w0),
                a * ((a + 1) + (a - 1) * cos_w0 - 2 * sqrt_a * alpha),
                (a + 1) - (a - 1) * cos_w0 + 2 * sqrt_a * alpha,
                2 * ((a - 1) - (a + 1) * cos_w0),
                (a + 1) - (a - 1) * cos_w0 - 2 * sqrt_a * alpha)
    if kind == "low-pass":
        return ((1 - cos_w0) / 2, 1 - cos_w0, (1 - cos_w0) / 2,
                1 + alpha, -2 * cos_w0, 1 - alpha)
    if kind == "high-pass":
        return ((1 + cos_w0) / 2, -(1 + cos_w0), (1 + cos_w0) / 2,
                1 + alpha, -2 * cos_w0, 1 - alpha)
    if kind == "band-pass":
        return (alpha, 0.0, -alpha, 1 + alpha, -2 * cos_w0, 1 - alpha)
    if kind == "all-pass":
        return (1 - alpha, -2 * cos_w0, 1 + alpha, 1 + alpha, -2 * cos_w0, 1 - alpha)
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)  # unknown type: flat


def _magnitude_db(coeffs, freq):
    b0, b1, b2, a0, a1, a2 = coeffs
    if a0 == 0:
        return 0.0
    z = cmath.exp(-2j * math.pi * freq / FS)
    num = b0 + b1 * z + b2 * z * z
    den = a0 + a1 * z + a2 * z * z
    if den == 0 or num == 0:
        return 0.0
    return 20.0 * math.log10(abs(num / den))


def frequencies(points=96):
    step = math.log10(F_MAX / F_MIN) / (points - 1)
    return [F_MIN * 10 ** (step * i) for i in range(points)]


def response(bands, preamp=0.0, points=96):
    """Summed response in dB at log-spaced frequencies."""
    freqs = frequencies(points)
    out = [preamp] * points
    for band in bands:
        if not band or not band.get("gain") and band.get("filter") == "peak":
            continue  # a 0 dB peak contributes nothing; skip the math
        coeffs = _coeffs(band.get("filter", "peak"), band.get("freq", 1000),
                         band.get("gain", 0.0), band.get("q", 0.7))
        for i, f in enumerate(freqs):
            out[i] += _magnitude_db(coeffs, f)
    return freqs, out


def plot(bands, preamp=0.0, width=46, height=11, limit=None):
    """Render the response as lines of text, with an axis and a 0 dB baseline."""
    freqs, db = response(bands, preamp, points=width)
    span = limit or max(6.0, math.ceil(max(abs(v) for v in db) / 3.0) * 3.0)
    rows = []
    label_w = 4

    def row_for(value):
        norm = (value + span) / (2 * span)  # 0..1, bottom..top
        return int(round((1 - norm) * (height - 1)))

    cells = [[" "] * width for _ in range(height)]
    zero = row_for(0.0)
    for x in range(width):
        cells[zero][x] = "─"  # baseline
    for x, value in enumerate(db):
        y = max(0, min(height - 1, row_for(value)))
        cells[y][x] = "━"
        if x:  # connect to the previous point so the curve reads as continuous
            prev = max(0, min(height - 1, row_for(db[x - 1])))
            step = 1 if y > prev else -1
            for fill in range(prev + step, y, step):
                cells[fill][x] = "│"
    for y, line in enumerate(cells):
        if y == 0:
            label = f"{span:+.0f}".rjust(label_w)
        elif y == height - 1:
            label = f"{-span:+.0f}".rjust(label_w)
        elif y == zero:
            label = "0".rjust(label_w)
        else:
            label = " " * label_w
        edge = "┼" if y == zero else "┤"
        rows.append(f"{label} {edge}{''.join(line)}")

    ticks = [" " * (label_w + 2)]
    for mark in ("20", "100", "1k", "10k"):
        target = {"20": 20.0, "100": 100.0, "1k": 1000.0, "10k": 10000.0}[mark]
        pos = min(range(width), key=lambda i: abs(freqs[i] - target))
        pad = pos - (len("".join(ticks)) - (label_w + 2))
        if pad >= 0:
            ticks.append(" " * pad + mark)
    rows.append("".join(ticks))
    return rows
