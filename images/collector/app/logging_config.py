import logging
import os
import sys

import structlog


def _add_collector_context(_, __, event_dict):
    event_dict.setdefault("service", "collector")
    event_dict.setdefault("component", os.getenv("COLLECTOR_COMPONENT", "traffic-collector"))
    event_dict.setdefault("interface", os.getenv("INTERFACE", "wlan0"))
    return event_dict


def configure_logging() -> None:
    log_level = os.getenv("LOG_LEVEL", "info").upper()
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level, logging.INFO),
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            _add_collector_context,
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, log_level, logging.INFO)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


configure_logging()
