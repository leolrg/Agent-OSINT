import logging
import sys

import structlog


def configure_logging(level: int = logging.INFO, *, force: bool = False) -> None:
    # Idempotent: if another caller (e.g. the worker, which splices in a
    # RedisEventSink) has already configured structlog, don't overwrite
    # their processor chain. Pass force=True to truly reset.
    if structlog.is_configured() and not force:
        return
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


logger = structlog.get_logger("osint")
