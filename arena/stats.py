"""Small statistics helpers for results: win rates with Wilson intervals, and
means with normal-approximation confidence intervals (e.g. bb/100)."""

import math

Z95 = 1.959963984540054


def wilson(successes: int, n: int, z: float = Z95) -> tuple:
    """Wilson score interval for a proportion. Returns (rate, low, high)."""
    if n == 0:
        return 0.0, 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, max(0.0, centre - half), min(1.0, centre + half)


def mean_ci(values, z: float = Z95) -> tuple:
    """Mean and 95% CI half-width (normal approximation). Returns (mean, half, n)."""
    n = len(values)
    if n == 0:
        return 0.0, 0.0, 0
    m = sum(values) / n
    if n == 1:
        return m, float("inf"), 1
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    return m, z * math.sqrt(var / n), n


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"
