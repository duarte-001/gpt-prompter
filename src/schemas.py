"""Pydantic models for fetch plans (LLM planner in a later step)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FetchPlan(BaseModel):
    """Strict JSON shape for future LLM-generated fetch plans."""

    tickers: list[str] = Field(default_factory=list, max_length=12)
    modules: list[Literal["history", "info"]] = Field(
        default_factory=lambda: ["history"],
        description="Which yfinance modules to pull",
    )
    period: str = Field(default="2y", description="yfinance period string")

    model_config = {"extra": "forbid"}
