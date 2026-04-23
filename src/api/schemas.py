from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=12_000)


class PipelineStep(BaseModel):
    label: str = ""
    detail: str = ""


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)
    prompt_only: bool = True
    use_rag: bool = True
    index_metrics_to_rag: bool = False
    period: str | None = Field(default=None, max_length=20)
    prompt_size: Literal["small", "medium", "large"] = "large"
    conversation_summary: str = Field(default="", max_length=20_000)
    recent_messages: list[ChatMessage] = Field(default_factory=list, max_length=8)
    prior_message_count: int | None = None


class AskResponse(BaseModel):
    mode: Literal["prompt_export", "local_answer"]
    answer: str
    symbols_used: list[str] = Field(default_factory=list)
    context_json: str = "{}"
    rag_context: str = ""
    rag_hits: list[dict[str, Any]] = Field(default_factory=list)
    rag_error: str | None = None
    indexed_chunks: int = 0
    error: str | None = None
    timings: dict[str, float] = Field(default_factory=dict)
    export_messages: list[ChatMessage] | None = None
    steps: list[PipelineStep] = Field(default_factory=list)


class HealthResponse(BaseModel):
    ok: bool = True


class StatusResponse(BaseModel):
    api_ok: bool = True
    ollama_reachable: bool
    ollama_base_url: str


class AskJobCreateResponse(BaseModel):
    job_id: str
    status: Literal["queued"]


class AskJobStatusResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "done", "error"]
    result: AskResponse | None = None
    error: str | None = None

