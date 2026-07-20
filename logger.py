import logging
import os
import sys
import threading
from datetime import datetime

_logger = None
_lock = threading.Lock()

def get_logger():
    global _logger
    if _logger is not None:
        return _logger
    with _lock:
        if _logger is not None:
            return _logger
        os.makedirs("logs", exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join("logs", f"topopixel_{timestamp}.log")
        logger = logging.getLogger("topopixel")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger.handlers.clear()
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        if "-v" in sys.argv or "--verbose" in sys.argv:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(logging.DEBUG)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
        _logger = logger
    return _logger

def log(msg, level="info"):
    getattr(get_logger(), level)(msg)