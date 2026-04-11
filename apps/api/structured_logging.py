import json
import logging
from typing import Any


LOGGER_NAME = "luamts.api"


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def log_event(event: str, **fields: Any) -> None:
    payload = {"event": event, **fields}
    logging.getLogger(LOGGER_NAME).info(json.dumps(payload, sort_keys=True))
