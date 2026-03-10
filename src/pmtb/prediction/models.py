"""
Prediction output type contracts for Phase 4.

PredictionResult — typed output consumed by Phase 5 (execution engine).
Compatible with the ModelOutput DB schema in db/models.py.

Design decisions:
- p_model, confidence_low, confidence_high are all validated [0,1].
- signal_weights is optional metadata for explainability / debugging.
- is_shadow=True marks predictions made before min_training_samples is reached —
  they are logged but not executed.
- used_llm tracks Claude API invocations for cost monitoring.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class PredictionResult(BaseModel):
    """
    Output contract from the XGBoost prediction layer.

    Consumed by Phase 5 (execution engine) and persisted to model_outputs table.

    Fields:
        ticker:           Kalshi market ticker (e.g. "PRES-2024-DEM-YES")
        cycle_id:         Scan cycle identifier for cross-referencing signals
        p_model:          Model-predicted probability of YES outcome [0, 1]
        confidence_low:   Lower bound of confidence interval [0, 1]
        confidence_high:  Upper bound of confidence interval [0, 1]
        signal_weights:   Optional per-source weight dict for explainability
        model_version:    String identifier of the model that produced this result
        used_llm:         Whether Claude API was invoked in this prediction
        is_shadow:        If True, prediction is logged but not executed
    """

    ticker: str
    cycle_id: str
    p_model: float = Field(ge=0.0, le=1.0, description="Model-predicted probability [0, 1]")
    confidence_low: float = Field(ge=0.0, le=1.0, description="CI lower bound [0, 1]")
    confidence_high: float = Field(ge=0.0, le=1.0, description="CI upper bound [0, 1]")
    signal_weights: dict[str, float] | None = Field(
        default=None,
        description="Optional per-source weights for explainability",
    )
    model_version: str = Field(description="Identifier of the model version used")
    used_llm: bool = Field(default=False, description="Whether Claude API was invoked")
    is_shadow: bool = Field(default=False, description="Shadow mode — logged but not executed")
