import os
import logging
from datetime import datetime
from typing import Optional, Union


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_LOG_DIR = os.getenv("LOG_DIR", os.path.join(PROJECT_ROOT, "logs"))
DEFAULT_LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
DEFAULT_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    log_dir: Optional[str] = None,
    run_name: Optional[str] = None,
    level: Union[int, str, None] = None,
    console: bool = True,
    fmt: Optional[str] = None,
) -> str:
    """
    Configure the root logger to emit messages both to the console and to a dedicated log file.

    Args:
        log_dir: Directory that will contain generated log files. Falls back to ``LOG_DIR`` env
            var or ``<project_root>/logs``.
        run_name: Optional file name stem for the log file. A timestamp-based name is used when
            not provided.
        level: Logging level (e.g. ``logging.INFO`` or ``\"DEBUG\"``). Defaults to ``LOG_LEVEL``
            env var or ``INFO``.
        console: When True (default) a console handler is also configured.
        fmt: Optional format string overriding the default formatter.

    Returns:
        Absolute path to the configured log file.
    """

    resolved_dir = log_dir or DEFAULT_LOG_DIR
    os.makedirs(resolved_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_label = run_name or f"run-{timestamp}"
    safe_run_label = run_label.replace("/", "_").replace(" ", "_")
    log_path = os.path.join(resolved_dir, f"{safe_run_label}.log")

    log_level = level
    if isinstance(log_level, str):
        log_level = getattr(logging, log_level.upper(), DEFAULT_LOG_LEVEL)
    if log_level is None:
        log_level = DEFAULT_LOG_LEVEL

    formatter = logging.Formatter(fmt or DEFAULT_LOG_FORMAT, DEFAULT_DATE_FORMAT)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers so repeated invocations don't duplicate outputs.
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    logging.captureWarnings(True)

    return log_path


def get_logger(name: str) -> logging.Logger:
    """
    Return a module-scoped logger.

    Args:
        name: Typically ``__name__`` from the caller module.

    Returns:
        logging.Logger: Logger instance bound to ``name``.
    """
    return logging.getLogger(name)

# def get_logger(name: str, log_file: str = None, level: int = LOG_LEVEL) -> logging.Logger:
#     """
#     Get a logger with a specific name and optional file logging.
#
#     Args:
#         name (str): Logger name, typically the module's `__name__`.
#         log_file (str): Log file name. If None, defaults to "<name>.log" under the logs directory.
#         level (int): Logging level (e.g., logging.DEBUG, logging.INFO).
#
#     Returns:
#         logging.Logger: Configured logger.
#     """
#     logger = logging.getLogger(name)
#     if logger.hasHandlers(): return logger # Avoid adding multiple handlers to the same logger
#
#
#     # Default to a log file based on the logger name
#     log_file = log_file or f"{name.replace('.', '->')}.log"
#     log_path = os.path.join(LOG_DIR, log_file)
#
#     # Set up file handler
#     file_handler = logging.FileHandler(log_path)
#     file_handler.setLevel(level)
#     file_handler.setFormatter(logging.Formatter(
#         "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
#     ))
#
#     # Set up console handler
#     console_handler = logging.StreamHandler()
#     console_handler.setLevel(level)
#     console_handler.setFormatter(logging.Formatter(
#         "%(asctime)s - %(levelname)s - %(message)s"
#     ))
#
#     # Attach handlers to logger
#     logger.setLevel(level)
#     logger.addHandler(file_handler)
#     logger.addHandler(console_handler)
#
#     return logger
