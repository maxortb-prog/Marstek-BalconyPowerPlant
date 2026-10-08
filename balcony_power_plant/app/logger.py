"""Colored logging with an additional TRACE level.

EN: Levels TRACE < DEBUG < INFO < WARNING < ERROR, each with its own ANSI color.
DE: Log-Level TRACE < DEBUG < INFO < WARNING < ERROR, jeweils mit eigener ANSI-Farbe.

TRACE   grey     raw UDP datagrams / MQTT payloads   (rohe UDP-Pakete / MQTT-Payloads)
DEBUG   cyan     internal flow                       (interner Ablauf)
INFO    green    normal operation                    (normaler Betrieb)
WARNING yellow   timeouts, late answers, repairs     (Timeouts, verspätete Antworten)
ERROR   red      failures                            (Fehler)
"""

from __future__ import annotations

import logging
import os
import sys

TRACE = 5
logging.addLevelName(TRACE, "TRACE")

_RESET = "\033[0m"
_COLORS = {
    TRACE: "\033[90m",          # grey
    logging.DEBUG: "\033[36m",  # cyan
    logging.INFO: "\033[32m",   # green
    logging.WARNING: "\033[33m",  # yellow
    logging.ERROR: "\033[31m",  # red
    logging.CRITICAL: "\033[1;31m",  # bold red
}

LEVELS = {
    "trace": TRACE,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def _trace(self: logging.Logger, msg, *args, **kwargs) -> None:
    if self.isEnabledFor(TRACE):
        self._log(TRACE, msg, args, **kwargs)


logging.Logger.trace = _trace  # type: ignore[attr-defined]


class ColorFormatter(logging.Formatter):
    """Formatter that wraps every line in the color of its level."""

    def __init__(self, use_color: bool) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if not self.use_color:
            return text
        return f"{_COLORS.get(record.levelno, '')}{text}{_RESET}"


def setup_logging(level_name: str) -> None:
    """Configure the root logger (stdout, colored unless NO_COLOR is set)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ColorFormatter(use_color="NO_COLOR" not in os.environ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    set_level(level_name)
    # third-party libraries stay quiet unless we trace
    logging.getLogger("aiomqtt").setLevel(logging.WARNING)


def set_level(level_name: str) -> None:
    """Change the log level at runtime (also used by the HA select entity)."""
    level = LEVELS.get(str(level_name).lower(), logging.INFO)
    logging.getLogger().setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
