"""Entry point: echo question (skeleton) or fetch yfinance metrics."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python src/app.py` by putting project root on sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def cmd_echo(question: str) -> None:
    print(question)


def cmd_ask(args: argparse.Namespace) -> None:
    from src.config import DEFAULT_YF_PERIOD, OLLAMA_BASE_URL
    from src.ollama_runtime import ensure_ollama_running
    from src.pipeline import answer_question

    base = (args.ollama_url or OLLAMA_BASE_URL).strip()
    if not ensure_ollama_running(base):
        print("Ollama is not reachable and could not be started.", file=sys.stderr)
        sys.exit(1)

    q = " ".join(args.question).strip()
    if not q:
        print("Empty question.", file=sys.stderr)
        sys.exit(1)
    tickers_path = Path(args.tickers_file) if args.tickers_file else None
    res = answer_question(
        q,
        period=args.period or DEFAULT_YF_PERIOD,
        tickers_json_path=tickers_path,
        model=args.model,
        ollama_base_url=args.ollama_url,
        embedding_model=args.embed_model,
        use_rag=not args.no_rag,
        index_metrics_to_rag=args.index_rag,
    )
    if res.error:
        print(res.error, file=sys.stderr)
    if res.answer:
        print(res.answer)
    if not res.answer and not res.error:
        print("(empty response)", file=sys.stderr)
        sys.exit(1)


def cmd_fetch(args: argparse.Namespace) -> None:
    from src.config import DEFAULT_YF_PERIOD, TICKERS_JSON, load_ticker_mapping
    from src.fetcher import fetch_all_tickers, summaries_to_json, write_csv_last_session

    tickers_path = Path(args.tickers_file) if args.tickers_file else TICKERS_JSON
    mapping = load_ticker_mapping(tickers_path)
    if args.limit is not None:
        mapping = dict(list(mapping.items())[: max(0, args.limit)])

    period = args.period or DEFAULT_YF_PERIOD
    results = fetch_all_tickers(mapping, period=period)

    out = summaries_to_json(results)
    if args.output_csv:
        write_csv_last_session(results, Path(args.output_csv))
    if args.output_json:
        Path(args.output_json).write_text(out, encoding="utf-8")
    if not args.quiet:
        print(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Stock Q&A app")
    sub = parser.add_subparsers(dest="command", required=False)

    p_echo = sub.add_parser("echo", help="Print question (skeleton)")
    p_echo.add_argument(
        "question",
        nargs="*",
        help="Question text; if omitted, reads one line from stdin",
    )

    p_ask = sub.add_parser("ask", help="Ask a question (Ollama + yfinance for tickers in the question)")
    p_ask.add_argument("question", nargs="+", help="Question text")
    p_ask.add_argument(
        "--tickers-file",
        type=str,
        default=None,
        help="Path to tickers JSON (default: project some_tickers.json)",
    )
    p_ask.add_argument(
        "--period",
        type=str,
        default=None,
        help="yfinance period for fetched metrics",
    )
    p_ask.add_argument("--model", type=str, default=None, help="Ollama model name")
    p_ask.add_argument(
        "--ollama-url",
        type=str,
        default=None,
        dest="ollama_url",
        help="Ollama base URL (default: http://127.0.0.1:11434)",
    )
    p_ask.add_argument(
        "--embed-model",
        type=str,
        default=None,
        help="Ollama embedding model for RAG (default: nomic-embed-text)",
    )
    p_ask.add_argument(
        "--no-rag",
        action="store_true",
        help="Disable Chroma retrieval",
    )
    p_ask.add_argument(
        "--index-rag",
        action="store_true",
        help="After fetch, upsert live metrics chunks into Chroma",
    )

    p_fetch = sub.add_parser("fetch", help="Download OHLCV and compute metrics (default: some_tickers.json)")
    p_fetch.add_argument(
        "--tickers-file",
        type=str,
        default=None,
        help="Path to tickers JSON (default: project some_tickers.json)",
    )
    p_fetch.add_argument(
        "--period",
        type=str,
        default=None,
        help="yfinance period (e.g. 1y, 2y, max); default from config",
    )
    p_fetch.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Only first N tickers (for testing)",
    )
    p_fetch.add_argument(
        "--output-json",
        type=str,
        default=None,
        metavar="PATH",
        help="Write full JSON summary to file",
    )
    p_fetch.add_argument(
        "--output-csv",
        type=str,
        default=None,
        metavar="PATH",
        help="Write one row per ticker (latest session metrics) to CSV",
    )
    p_fetch.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print JSON to stdout (use with --output-*)",
    )

    args = parser.parse_args()

    if args.command == "fetch":
        cmd_fetch(args)
        return
    if args.command == "ask":
        cmd_ask(args)
        return
    if args.command == "echo":
        if args.question:
            text = " ".join(args.question).strip()
        else:
            if sys.stdin.isatty():
                text = input("Question: ").strip()
            else:
                text = sys.stdin.read().strip()
        if not text:
            print("No question provided.", file=sys.stderr)
            sys.exit(1)
        cmd_echo(text)
        return

    # Default: same as echo for backward compatibility
    rest = sys.argv[1:]
    if rest:
        cmd_echo(" ".join(rest).strip())
    else:
        if sys.stdin.isatty():
            q = input("Question: ").strip()
        else:
            q = sys.stdin.read().strip()
        if not q:
            parser.print_help()
            sys.exit(1)
        cmd_echo(q)


if __name__ == "__main__":
    main()
