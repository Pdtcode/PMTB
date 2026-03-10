"""
Confidence interval computation for probability model outputs.

The default method uses a simple configurable half-width with [0, 1] boundary clamping.
More sophisticated methods (bootstrap, beta distribution) can be swapped in later —
the key invariant is that the returned interval is always within [0, 1].
"""
from __future__ import annotations


def compute_confidence_interval(
    p_model: float,
    half_width: float = 0.1,
) -> tuple[float, float]:
    """
    Compute a confidence interval around a probability estimate.

    The interval is [p_model - half_width, p_model + half_width], clamped to [0, 1].

    Parameters
    ----------
    p_model : float
        Central probability estimate in [0, 1].
    half_width : float
        Half-width of the interval. Default 0.1 (±10 percentage points).

    Returns
    -------
    tuple[float, float]
        (low, high) both clamped to [0, 1].
    """
    low = max(0.0, p_model - half_width)
    high = min(1.0, p_model + half_width)
    return (low, high)
