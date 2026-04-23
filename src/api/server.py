from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("STOCK_ASSISTANT_HOST", "127.0.0.1")
    port = int(os.environ.get("STOCK_ASSISTANT_PORT", "8787"))
    # Note: worker count is managed by the process manager (gunicorn/uvicorn workers).
    uvicorn.run(
        "src.api.app:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()

