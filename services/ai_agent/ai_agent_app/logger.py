import logging
import logging.handlers
import os
import sys
from pathlib import Path

# Same env var and default as services.ai_agent.ai_agent_app.config's
# Settings.log_level -- read independently here (rather than importing
# Settings) so this module has no dependency on config-loading order.
_DEFAULT_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
_MAX_LOG_BYTES = int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024)))
_LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))


def setup_logger(name: str, log_level: str | None = None) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        # hasHandlers() would also return True whenever the ROOT logger (or
        # any ancestor) has handlers -- e.g. after logging.basicConfig(), or
        # under pytest's own log capture -- causing this function to skip
        # attaching this logger's own console/rotating-file handlers
        # entirely in those environments. Check this logger's own handler
        # list instead so setup is truly idempotent per-name, not
        # accidentally skipped based on unrelated global logging state.
        return logger

    logger.setLevel(getattr(logging, (log_level or _DEFAULT_LOG_LEVEL).upper(), logging.INFO))

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File handler -- rotates so logs/app.log can't grow unbounded across a
    # long-running production process; LOG_MAX_BYTES/LOG_BACKUP_COUNT let
    # ops tune retention without a code change.
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=_MAX_LOG_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger

# Primary loggers
app_logger = setup_logger("rahma_app")
sheet_logger = setup_logger("rahma_sheets")
agent_logger = setup_logger("rahma_agent")
webhook_logger = setup_logger("rahma_webhook")
