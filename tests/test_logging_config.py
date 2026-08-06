from __future__ import annotations

import importlib
import logging
import logging.handlers
import uuid

from services.ai_agent.ai_agent_app import logger as logger_module


def _cleanup_logger(name: str) -> None:
    test_logger = logging.getLogger(name)
    for handler in list(test_logger.handlers):
        handler.close()
        test_logger.removeHandler(handler)
    logging.Logger.manager.loggerDict.pop(name, None)


def test_log_level_env_var_actually_controls_logger_level(tmp_path, monkeypatch):
    """Regression: Settings.log_level read LOG_LEVEL from the environment
    but nothing ever consumed it -- setup_logger()'s log_level parameter
    defaulted to the literal "INFO" and every module-level logger
    (app_logger, agent_logger, ...) was constructed with no argument at
    all, so LOG_LEVEL was silently ignored regardless of what an operator
    set it to. Reloading the module re-reads the env var into the
    module-level default, exercising the exact code path a fresh process
    startup would take.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    importlib.reload(logger_module)

    logger_name = f"rahma_test_loglevel_{uuid.uuid4().hex}"
    try:
        test_logger = logger_module.setup_logger(logger_name)
        assert test_logger.level == logging.WARNING
    finally:
        _cleanup_logger(logger_name)
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        importlib.reload(logger_module)


def test_setup_logger_attaches_handlers_even_when_root_logger_already_has_some(tmp_path, monkeypatch):
    """Regression: the original guard was `if logger.hasHandlers(): return`,
    but hasHandlers() also returns True when the ROOT logger (or any
    ancestor) already has handlers -- e.g. after logging.basicConfig(), or
    under pytest's own log capture. That made setup_logger() silently skip
    attaching a brand-new named logger's own console/file handlers in any
    such environment, so LOG_LEVEL and file/rotation config never actually
    took effect there even after fixing them above.
    """
    monkeypatch.chdir(tmp_path)
    importlib.reload(logger_module)

    logger_name = f"rahma_test_guard_{uuid.uuid4().hex}"
    root_handler = logging.StreamHandler()
    logging.getLogger().addHandler(root_handler)
    try:
        assert logging.getLogger(logger_name).hasHandlers()
        test_logger = logger_module.setup_logger(logger_name)
        assert test_logger.handlers
    finally:
        logging.getLogger().removeHandler(root_handler)
        _cleanup_logger(logger_name)


def test_file_handler_rotates_instead_of_growing_unbounded(tmp_path, monkeypatch):
    """Regression: the file handler was a plain logging.FileHandler with no
    size cap -- logs/app.log would grow without bound for the lifetime of a
    long-running production process.
    """
    monkeypatch.chdir(tmp_path)
    importlib.reload(logger_module)

    logger_name = f"rahma_test_rotation_{uuid.uuid4().hex}"
    try:
        test_logger = logger_module.setup_logger(logger_name)
        rotating_handlers = [
            handler for handler in test_logger.handlers if isinstance(handler, logging.handlers.RotatingFileHandler)
        ]
        assert len(rotating_handlers) == 1
        assert rotating_handlers[0].maxBytes > 0
        assert rotating_handlers[0].backupCount > 0
        assert not any(
            isinstance(handler, logging.FileHandler) and not isinstance(handler, logging.handlers.RotatingFileHandler)
            for handler in test_logger.handlers
        )
    finally:
        _cleanup_logger(logger_name)
