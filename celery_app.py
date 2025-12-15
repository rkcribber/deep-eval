"""
Celery Application Setup
"""
from celery import Celery
from config import CELERY_CONFIG

celery = Celery('deep_eval')
celery.config_from_object(CELERY_CONFIG)

# Register task modules - this tells Celery where to find tasks
celery.conf.update(
    imports=['tasks']
)


