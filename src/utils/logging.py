"""
Structured logging configuration using structlog.

Usage anywhere in the project:
    from src.utils.logging import get_logger
    logger = get_logger(__name__)
    logger.info("event_name", key="value")
"""
import logging
import sys
from typing import Any

import structlog


def _add_module_name(logger: Any, method_name: str, event_dict: dict) -> dict:
    """
    Custom processor: inject the logger's bound name as 'module' into the log record.
    Works with PrintLogger (structlog native) as well as stdlib loggers.
    """
    # structlog passes the name as _logger._name when using get_logger("name")
    name = getattr(logger, "_name", None) or getattr(logger, "name", None)
    if name:
        event_dict.setdefault("module", name)
    return event_dict


def setup_logging(log_level: str = "INFO", environment: str = "development") -> None:
    """
    Configure structlog for the application.
    - Development: pretty-printed console output with colours
    - Production:  JSON output for log aggregators (e.g., Datadog, GCP)
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=numeric_level,
    )

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _add_module_name,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if environment == "development":
        renderer: Any = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,  # allow re-config between tests
    )


def get_logger(name: str = __name__) -> Any:
    """Return a bound structlog logger for a given module name."""
    return structlog.get_logger(name)
