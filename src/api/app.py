from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.parse import quote

import httpx
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi import status as http_status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src import config
from fastapi import Depends

from src.api.auth import ApiKeyIdentity, require_api_key
from src.api.body_limit import MaxBodySizeMiddleware, body_limit_from_env
from src.api.concurrency import acquire_or_503
from src.api.executor import EXECUTOR
from src.api.jobs import create_job, get_job, run_job
from src.api.limiter import rate_limit
from src.api.schemas import (
    AskJobCreateResponse,
    AskJobStatusResponse,
    AskRequest,
    AskResponse,
    HealthResponse,
    StatusResponse,
)
from src.ollama_runtime import ollama_reachable
from src.pipeline import answer_question
from src.config import load_ticker_mapping


def _safe_symbol_slug(symbol: str) -> str:
    s = (symbol or "").strip()
    if not s:
        return "unknown"
    return "".join(ch if ch.isalnum() or ch in ("_", "-", ".") else "_" for ch in s)[:40]


def _resolve_logo_url(symbol: str, *, mapping: dict[str, tuple[str, str]] | None = None) -> str | None:
    """
    Best-effort logo URL resolver.

    Priority:
    1) yfinance info.logo_url (when available)
    2) Wikipedia thumbnail via opensearch + page summary (fallback)
    """
    # 1) Yahoo/yfinance logo_url (often available for equities/ETFs)
    try:
        import yfinance as yf  # local import: keeps API startup light

        info = yf.Ticker(symbol).info or {}
        url = (info.get("logo_url") or "").strip()
        if url:
            return url
        query = (info.get("shortName") or info.get("longName") or "").strip()
    except Exception:  # noqa: BLE001
        query = ""

    # 2) Wikipedia fallback: search by label/short name
    if not query and mapping and symbol in mapping:
        query = (mapping[symbol][0] or "").strip()
    if not query:
        return None

    try:
        # opensearch gives best-effort title
        r = httpx.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "opensearch",
                "search": query,
                "limit": 1,
                "namespace": 0,
                "format": "json",
            },
            timeout=6.0,
            headers={"User-Agent": "StockAssistant/1.0 (logo fetch)"},
        )
        data = r.json()
        titles = data[1] if isinstance(data, list) and len(data) > 1 else []
        if not titles:
            return None
        title = titles[0]
        s = httpx.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(str(title), safe='')}",
            timeout=6.0,
            headers={"User-Agent": "StockAssistant/1.0 (logo fetch)"},
        )
        summ = s.json()
        thumb = (summ.get("thumbnail") or {}).get("source") or ""
        return thumb.strip() or None
    except Exception:  # noqa: BLE001
        return None


_LOGO_ALLOWED_HOSTS = {
    # Wikipedia thumbnails
    "upload.wikimedia.org",
    # Common wiki endpoints used above
    "en.wikipedia.org",
    # Yahoo logo_url varies; we still enforce https and a max size.
    "s.yimg.com",
    "s.yimg.jp",
    "logo.clearbit.com",  # optional fallback if you later add it
}

_LOGO_MAX_BYTES = 1_500_000
_LOGO_ALLOWED_CT = ("image/png", "image/jpeg", "image/webp")


def _logo_url_allowed(url: str) -> bool:
    try:
        u = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    if u.scheme not in ("https",):
        return False
    host = (u.hostname or "").lower()
    if not host:
        return False
    if host in _LOGO_ALLOWED_HOSTS:
        return True
    # allow subdomains of allowed hosts
    return any(host.endswith("." + h) for h in _LOGO_ALLOWED_HOSTS)


def _ext_for_ct(ctype: str) -> str | None:
    c = (ctype or "").split(";")[0].strip().lower()
    if c == "image/png":
        return "png"
    if c in ("image/jpeg", "image/jpg"):
        return "jpg"
    if c == "image/webp":
        return "webp"
    return None


def create_app() -> FastAPI:
    app = FastAPI(title="Stock Assistant API", version="1.0")

    app.add_middleware(MaxBodySizeMiddleware, max_bytes=body_limit_from_env())

    root = Path(__file__).resolve().parents[2]  # .../src/api -> project root

    # Dev convenience: allow local React dev server.
    allow_origins = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _request_id_middleware(request: Request, call_next):
        # Lightweight request id for logs and client debugging.
        rid = (request.headers.get("X-Request-Id") or "").strip()
        if not rid:
            import uuid

            rid = uuid.uuid4().hex
        request.state.request_id = rid
        resp: Response = await call_next(request)
        resp.headers["X-Request-Id"] = rid
        # If rate limit dependency populated state, expose it as standard-ish headers.
        rl = getattr(request.state, "rate_limit", None)
        if isinstance(rl, dict):
            try:
                resp.headers["X-RateLimit-Limit"] = str(int(rl.get("limit", 0)))
                resp.headers["X-RateLimit-Remaining"] = str(int(rl.get("remaining", 0)))
                resp.headers["X-RateLimit-Reset"] = str(int(float(rl.get("reset_in_s", 0))))
            except Exception:  # noqa: BLE001
                pass
        return resp

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(ok=True)

    @app.get("/api/status", response_model=StatusResponse)
    def status(
        ident: ApiKeyIdentity = Depends(require_api_key),
        _rl=rate_limit("status", default_rpm=120),
    ) -> StatusResponse:
        ollama_base_url = os.environ.get("OLLAMA_BASE_URL", config.OLLAMA_BASE_URL).strip()
        ok = ollama_reachable(ollama_base_url, timeout_s=1.2)
        return StatusResponse(api_ok=True, ollama_reachable=ok, ollama_base_url=ollama_base_url)

    @app.get("/api/logo/{symbol}")
    def logo(
        symbol: str,
        ident: ApiKeyIdentity = Depends(require_api_key),
        _rl=rate_limit("logo", default_rpm=60),
    ):
        slug = _safe_symbol_slug(symbol)
        logos_dir = config.DATA_DIR / "logos"
        logos_dir.mkdir(parents=True, exist_ok=True)

        # Return cached first
        for ext in ("png", "jpg", "jpeg", "webp"):
            p = logos_dir / f"{slug}.{ext}"
            if p.exists():
                return FileResponse(str(p))

        mapping = load_ticker_mapping()
        url = _resolve_logo_url(symbol, mapping=mapping)
        if not url:
            raise HTTPException(status_code=404, detail="Logo not found")

        if not _logo_url_allowed(url):
            raise HTTPException(status_code=404, detail="Logo not available")

        try:
            with httpx.Client(timeout=10.0, follow_redirects=False, headers={"User-Agent": "StockAssistant/1.0"}) as client:
                r = client.get(url)
                # Handle a single redirect explicitly and re-check allowlist.
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = (r.headers.get("location") or "").strip()
                    if not loc or not _logo_url_allowed(loc):
                        raise HTTPException(status_code=404, detail="Logo fetch blocked")
                    r = client.get(loc)

            if r.status_code >= 400:
                raise HTTPException(status_code=404, detail="Logo fetch failed")

            ctype = (r.headers.get("content-type") or "").lower()
            ct_main = ctype.split(";")[0].strip()
            if ct_main not in _LOGO_ALLOWED_CT:
                raise HTTPException(status_code=404, detail="Logo content-type not allowed")

            raw = r.content or b""
            if len(raw) > _LOGO_MAX_BYTES:
                raise HTTPException(status_code=404, detail="Logo too large")

            ext = _ext_for_ct(ct_main) or "png"
            out = logos_dir / f"{slug}.{ext}"
            out.write_bytes(raw)
            return FileResponse(str(out))
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e))

    def _run_ask_blocking(req: AskRequest) -> dict:
        ollama_base_url = os.environ.get("OLLAMA_BASE_URL", config.OLLAMA_BASE_URL).strip()
        model = os.environ.get("OLLAMA_MODEL", config.OLLAMA_MODEL).strip()
        embed_model = os.environ.get("OLLAMA_EMBED_MODEL", config.OLLAMA_EMBED_MODEL).strip()

        steps: list[dict[str, str]] = []

        def on_step(label: str, detail: str) -> None:
            steps.append({"label": label or "", "detail": detail or ""})

        with acquire_or_503("ask"):
            res = answer_question(
                req.question,
                period=req.period,
                model=model,
                ollama_base_url=ollama_base_url,
                embedding_model=embed_model,
                use_rag=req.use_rag,
                index_metrics_to_rag=req.index_metrics_to_rag,
                conversation_summary=req.conversation_summary,
                recent_messages=[m.model_dump() for m in req.recent_messages] if req.recent_messages else None,
                prior_message_count=req.prior_message_count,
                step_callback=on_step,
                skip_llm=req.prompt_only,
                prompt_size=req.prompt_size,
            )

        export = None
        if res.export_messages is not None:
            export = [
                {"role": m["role"], "content": m["content"]}
                for m in res.export_messages
                if isinstance(m, dict) and "role" in m and "content" in m
            ]

        out = AskResponse(
            mode="prompt_export" if req.prompt_only else "local_answer",
            answer=res.answer,
            symbols_used=list(res.symbols_used or []),
            context_json=res.context_json or "{}",
            rag_context=res.rag_context or "",
            rag_hits=list(res.rag_hits or []),
            rag_error=res.rag_error,
            indexed_chunks=int(res.indexed_chunks or 0),
            error=res.error,
            timings=dict(res.timings or {}),
            export_messages=export,
            steps=steps,
        )
        return out.model_dump()

    @app.post("/api/ask", response_model=AskJobCreateResponse, status_code=http_status.HTTP_202_ACCEPTED)
    async def ask(
        req: AskRequest,
        ident: ApiKeyIdentity = Depends(require_api_key),
        _rl=rate_limit("ask", default_rpm=12),
    ) -> AskJobCreateResponse:
        jid = create_job()
        # fire-and-forget background task
        import asyncio

        asyncio.create_task(run_job(jid, _run_ask_blocking, req))
        return AskJobCreateResponse(job_id=jid, status="queued")

    @app.get("/api/ask/{job_id}", response_model=AskJobStatusResponse)
    def ask_status(
        job_id: str,
        ident: ApiKeyIdentity = Depends(require_api_key),
        _rl=rate_limit("ask_status", default_rpm=240),
    ) -> AskJobStatusResponse:
        rec = get_job(job_id)
        result = AskResponse.model_validate(rec.result) if rec.result else None
        return AskJobStatusResponse(job_id=job_id, status=rec.state, result=result, error=rec.error)

    @app.post("/api/ask_sync", response_model=AskResponse)
    def ask_sync(
        req: AskRequest,
        ident: ApiKeyIdentity = Depends(require_api_key),
        _rl=rate_limit("ask_sync", default_rpm=6),
    ) -> AskResponse:
        return AskResponse.model_validate(_run_ask_blocking(req))

    @app.get("/metrics")
    def metrics(ident: ApiKeyIdentity = Depends(require_api_key)):
        # Minimal Prometheus endpoint (no extra deps).
        # These metrics are per-process; in a multi-worker deployment you'd aggregate externally.
        lines: list[str] = []
        lines.append("# HELP stockassistant_build_info Build and runtime info")
        lines.append("# TYPE stockassistant_build_info gauge")
        lines.append('stockassistant_build_info{app="stockassistant",component="api"} 1')
        return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    # Project assets (icons, etc.)
    assets_dir = root / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    # Serve frontend when present:
    # - Prefer a built Vite output under frontend/dist
    # - Fallback to the lightweight no-build frontend/ directory (CDN React)
    dist = root / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend_dist")
    else:
        fallback = root / "frontend"
        if (fallback / "index.html").exists():
            app.mount("/", StaticFiles(directory=str(fallback), html=True), name="frontend_fallback")

    return app


app = create_app()

