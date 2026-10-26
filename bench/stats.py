"""Statistical Utilities for Benchmark Metrics and Confidence Intervals.

Computes the 95% Wilson score confidence interval for binomial proportions
(e.g., pass rates, accuracy rates) ensuring robust accuracy assertions.
"""

import math
from typing import Tuple


def wilson_interval(successes: int, total: int, confidence: float = 0.95) -> Tuple[float, float, float]:
    """
    Computes the Wilson score interval for binomial proportions.
    Returns (point_estimate, lower_bound, upper_bound).
    """
    if total <= 0:
        return 0.0, 0.0, 0.0

    p = float(successes) / float(total)
    # Z-scores for common confidence levels
    z_map = {0.90: 1.64485, 0.95: 1.95996, 0.99: 2.57583}
    z = z_map.get(confidence, 1.95996)

    z2 = z * z
    denom = 1.0 + z2 / total
    center = (p + z2 / (2.0 * total)) / denom
    spread = (z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * total)) / total)) / denom

    lower = max(0.0, center - spread)
    upper = min(1.0, center + spread)
    return round(p, 4), round(lower, 4), round(upper, 4)
