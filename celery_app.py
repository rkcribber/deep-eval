"""
Celery Application Setup
"""
from celery import Celery
from celery.signals import worker_ready, worker_shutdown, task_failure
from config import CELERY_CONFIG
import logging

logger = logging.getLogger(__name__)

celery = Celery('deep_eval')
celery.config_from_object(CELERY_CONFIG)

# Register task modules - this tells Celery where to find tasks
celery.conf.update(
    imports=['tasks']
)


# ==============================================================================
# Signal Handlers for Worker Resilience
# ==============================================================================

@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    """Log when worker is ready to accept tasks."""
    logger.info("Celery worker is ready and connected to Redis broker")


@worker_shutdown.connect
def on_worker_shutdown(sender, **kwargs):
    """Log when worker is shutting down."""
    logger.info("Celery worker is shutting down")


@task_failure.connect
def on_task_failure(sender, task_id, exception, args, kwargs, traceback, einfo, **kw):
    """Log task failures with details."""
    logger.error(
        "Task %s failed: %s\nArgs: %s\nKwargs: %s",
        task_id, str(exception), args, kwargs
    )


