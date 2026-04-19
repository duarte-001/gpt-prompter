"""Stock Assistant — intelligent prompt generation for financial LLM reasoning."""

import logging

_logger = logging.getLogger("stock_qa")
if not _logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-5s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    _logger.addHandler(_handler)
    _logger.setLevel(logging.INFO)
