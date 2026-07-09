import logging
import os
from datetime import datetime

_logger = None

def get_logger():
    global _logger
    if _logger is not None:
        return _logger

    os.makedirs("logs", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join("logs", f"topopixel_{timestamp}.log")

    _logger = logging.getLogger("topopixel")
    _logger.setLevel(logging.DEBUG)

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    handler.setFormatter(formatter)
    _logger.addHandler(handler)

    return _logger

def log(msg, level="info"):
    logger = get_logger()
    getattr(logger, level)(msg)