"""
Structured Logging with Task Context and Hourly File Rotation

Provides per-task logging with unique task_id in every log message.
Logs are written to both stdout and hourly rotating files.

Log files are stored in /app/logs/ (Docker) or ./logs/ (local)
Each file represents a 1-hour time frame: app_2025-12-13_14.log

Usage:
    from logger import get_task_logger

    # In your task or pipeline:
    log = get_task_logger(task_id)
    log.info("Starting OCR step")
    log.error("Failed to process: %s", error_message)

Output format:
    [2025-12-13 10:30:45] [INFO] [task_id=abc123] Starting OCR step
"""

import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime


# Log directory - use /app/logs in Docker, ./logs locally
LOG_DIR = os.environ.get('LOG_DIR', None)

if LOG_DIR is None:
    # Default to local logs directory
    LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')

# Create log directory if it doesn't exist
if not os.path.exists(LOG_DIR):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except (PermissionError, OSError):
        # Fallback to local logs directory if default is not writable
        LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
        except (PermissionError, OSError):
            # If even local logs fail, use temp directory
            import tempfile
            LOG_DIR = os.path.join(tempfile.gettempdir(), 'deep-eval-flask-logs')
            os.makedirs(LOG_DIR, exist_ok=True)


class HourlyRotatingFileHandler(TimedRotatingFileHandler):
    """
    Custom handler that creates hourly log files with readable names.

    File format: app_YYYY-MM-DD_HH.log
    Example: app_2025-12-13_14.log (logs from 14:00-15:00)
    """

    def __init__(self, log_dir: str, prefix: str = 'app'):
        self.log_dir = log_dir
        self.prefix = prefix

        # Create initial log file path
        log_file = self._get_log_filename()

        # Initialize with hourly rotation
        super().__init__(
            log_file,
            when='H',  # Rotate every hour
            interval=1,
            backupCount=168,  # Keep 7 days of hourly logs (24 * 7)
            encoding='utf-8'
        )

        # Override the suffix to use our format
        self.suffix = ""
        self.namer = self._namer

    def _get_log_filename(self) -> str:
        """Generate log filename for current hour."""
        now = datetime.now()
        filename = f"{self.prefix}_{now.strftime('%Y-%m-%d_%H')}.log"
        return os.path.join(self.log_dir, filename)

    def _namer(self, default_name: str) -> str:
        """Custom namer for rotated files."""
        return self._get_log_filename()

    def doRollover(self):
        """Override rollover to create new file with current hour."""
        if self.stream:
            self.stream.close()
            self.stream = None

        # Update to new filename for current hour
        self.baseFilename = self._get_log_filename()

        # Open new file
        self.mode = 'a'
        self.stream = self._open()

        # Clean up old files
        self._cleanup_old_files()

    def _cleanup_old_files(self):
        """Remove log files older than backupCount hours."""
        try:
            files = os.listdir(self.log_dir)
            log_files = [f for f in files if f.startswith(self.prefix) and f.endswith('.log')]

            if len(log_files) > self.backupCount:
                # Sort by filename (which includes timestamp)
                log_files.sort()
                # Remove oldest files
                for old_file in log_files[:-self.backupCount]:
                    try:
                        os.remove(os.path.join(self.log_dir, old_file))
                    except OSError:
                        pass
        except OSError:
            pass


class TaskLoggerAdapter(logging.LoggerAdapter):
    """
    Logger adapter that prepends task_id to all log messages.

    This allows filtering logs by task_id to debug specific requests.
    """

    def process(self, msg, kwargs):
        task_id = self.extra.get('task_id', 'unknown')
        return f"[task_id={task_id}] {msg}", kwargs


def setup_logging():
    """
    Configure the root logger with:
    - Console output (stdout)
    - Hourly rotating file output

    Log files are stored in LOG_DIR with format: app_YYYY-MM-DD_HH.log
    """
    # Create formatter with timestamp
    formatter = logging.Formatter(
        fmt='[%(asctime)s] [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Remove existing handlers to avoid duplicate logs
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add stdout handler (for Docker logs / console)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler.setLevel(logging.INFO)
    root_logger.addHandler(stdout_handler)

    # Add hourly rotating file handler
    try:
        file_handler = HourlyRotatingFileHandler(LOG_DIR, prefix='app')
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)
        root_logger.addHandler(file_handler)
        root_logger.info(f"Logging to directory: {LOG_DIR}")
    except Exception as e:
        root_logger.warning(f"Could not setup file logging: {e}")

    return root_logger


def get_task_logger(task_id: str, name: str = 'pipeline') -> TaskLoggerAdapter:
    """
    Get a logger with task_id context.

    All log messages from this logger will include the task_id prefix,
    making it easy to filter logs for a specific task.

    Args:
        task_id: Unique task identifier
        name: Logger name (default: 'pipeline')

    Returns:
        TaskLoggerAdapter with task_id context

    Example:
        log = get_task_logger("abc123")
        log.info("Processing started")
        # Output: [2025-12-13 10:30:45] [INFO] [task_id=abc123] Processing started

    Log files:
        Logs are written to: {LOG_DIR}/app_YYYY-MM-DD_HH.log
    """
    logger = logging.getLogger(name)
    return TaskLoggerAdapter(logger, {'task_id': task_id})


def get_logger(name: str = 'app') -> logging.Logger:
    """
    Get a standard logger without task context.

    Use this for application-level logging (startup, health checks, etc.)

    Args:
        name: Logger name

    Returns:
        Standard logger
    """
    return logging.getLogger(name)


def get_log_directory() -> str:
    """Return the current log directory path."""
    return LOG_DIR


# Initialize logging on module import
setup_logging()

