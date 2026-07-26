"""Structured JSON-ish logging shared by API and worker processes."""
import logging
import sys


def setup_logging(service_name: str) -> logging.Logger:
    logger = logging.getLogger(service_name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt=f'{{"service":"{service_name}","level":"%(levelname)s","time":"%(asctime)s","message":"%(message)s"}}'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger
