"""
Probability combining strategies for XGBoost + Claude estimates.

Three combining modes:
  - log_odds: Bayesian combination via weighted log-odds (logit space)
  - weighted_average: Simple weighted linear blend

combine_estimates() is the main entry point and handles single-estimator cases:
  - If only p_xgb: cold-start mode (XGBoost before Claude is gated)
  - If only p_claude: Claude-only mode
  - If both: dispatches to chosen strategy
  - If neither: raises ValueError
"""
from __future__ import annotations

import math


def combine_log_odds(
    p_xgb: float,
    p_claude: float,
    weight_xgb: float = 0.6,
    weight_claude: float = 0.4,
) -> float:
    """
    Combine two probability estimates using weighted log-odds (logit space).

    Inputs are clipped to [eps, 1-eps] to avoid log(0). The result is guaranteed
    to be in the open interval (0, 1) and will differ from both inputs when both
    are present and distinct.

    Parameters
    ----------
    p_xgb : float
        XGBoost probability estimate.
    p_claude : float
        Claude probability estimate.
    weight_xgb : float
        Weight for XGBoost logit. Default 0.6.
    weight_claude : float
        Weight for Claude logit. Default 0.4.

    Returns
    -------
    float
        Combined probability in (0, 1).
    """
    eps = 1e-6
    p_xgb = max(eps, min(1.0 - eps, p_xgb))
    p_claude = max(eps, min(1.0 - eps, p_claude))

    logit_xgb = math.log(p_xgb / (1.0 - p_xgb))
    logit_claude = math.log(p_claude / (1.0 - p_claude))

    combined = weight_xgb * logit_xgb + weight_claude * logit_claude
    return 1.0 / (1.0 + math.exp(-combined))


def combine_weighted_average(
    p_xgb: float,
    p_claude: float,
    weight_xgb: float = 0.6,
    weight_claude: float = 0.4,
) -> float:
    """
    Combine two probability estimates using a weighted arithmetic average.

    Result is clamped to [0, 1].

    Parameters
    ----------
    p_xgb : float
        XGBoost probability estimate.
    p_claude : float
        Claude probability estimate.
    weight_xgb : float
        Weight for XGBoost estimate. Default 0.6.
    weight_claude : float
        Weight for Claude estimate. Default 0.4.

    Returns
    -------
    float
        Weighted average clamped to [0, 1].
    """
    raw = weight_xgb * p_xgb + weight_claude * p_claude
    return max(0.0, min(1.0, raw))


def combine_estimates(
    p_xgb: float | None,
    p_claude: float | None,
    method: str = "log_odds",
    weight_xgb: float = 0.6,
    weight_claude: float = 0.4,
) -> float:
    """
    Combine XGBoost and Claude probability estimates with the chosen strategy.

    Handles single-estimator cases:
    - p_xgb only: returns p_xgb directly (Claude was not called)
    - p_claude only: returns p_claude directly (cold-start, no XGBoost model yet)
    - Both: dispatches to ``method``
    - Neither: raises ValueError

    Parameters
    ----------
    p_xgb : float | None
        XGBoost probability estimate, or None if unavailable.
    p_claude : float | None
        Claude probability estimate, or None if unavailable.
    method : str
        Combining strategy: "log_odds" or "weighted_average".
    weight_xgb : float
        Weight for XGBoost in combining strategies.
    weight_claude : float
        Weight for Claude in combining strategies.

    Returns
    -------
    float
        Combined probability in [0, 1].

    Raises
    ------
    ValueError
        If both p_xgb and p_claude are None.
    ValueError
        If an unknown method is specified.
    """
    if p_xgb is None and p_claude is None:
        raise ValueError(
            "At least one of p_xgb or p_claude must be provided. Both are None."
        )

    # Single-estimator pass-through
    if p_xgb is None:
        return float(p_claude)  # type: ignore[arg-type]
    if p_claude is None:
        return float(p_xgb)

    # Both available — dispatch to strategy
    if method == "log_odds":
        return combine_log_odds(p_xgb, p_claude, weight_xgb, weight_claude)
    elif method == "weighted_average":
        return combine_weighted_average(p_xgb, p_claude, weight_xgb, weight_claude)
    else:
        raise ValueError(
            f"Unknown method '{method}'. Supported methods: 'log_odds', 'weighted_average'."
        )
