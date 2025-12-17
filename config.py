"""
Configuration for Flask and Celery
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Redis URL for Celery broker and result backend
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

# ==============================================================================
# Document Processing API Keys (Vertex AI / Gemini & OpenAI)
# ==============================================================================
VERTEX_AI_API_KEY = os.getenv('VERTEX_AI_API_KEY', '')
VERTEX_PROJECT_ID = os.getenv('VERTEX_PROJECT_ID', 'formidable-feat-476715-d7')
VERTEX_LOCATION = os.getenv('VERTEX_LOCATION', 'us-central1')
VERTEX_MODEL_NAME = os.getenv('VERTEX_MODEL_NAME', 'gemini-2.5-pro')

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
OPENAI_ASSISTANT_ID = os.getenv('OPENAI_ASSISTANT_ID', '')

# Celery Configuration
CELERY_CONFIG = {
    'broker_url': REDIS_URL,
    'result_backend': REDIS_URL,
    'task_serializer': 'json',
    'result_serializer': 'json',
    'accept_content': ['json'],
    'timezone': 'UTC',
    'enable_utc': True,
    'task_track_started': True,
    
    # Broker connection retry on startup (suppresses deprecation warning)
    'broker_connection_retry_on_startup': True,

    # Timeout settings for long-running tasks (10+ minutes processing)
    'task_time_limit': 900,              # Hard kill after 15 minutes
    'task_soft_time_limit': 840,         # Raise exception after 14 minutes

    # Prevent task loss during long processing
    'broker_transport_options': {
        'visibility_timeout': 1200,       # 20 minutes - must be > task duration
    },
    
    # Don't prefetch tasks for long-running workers
    'worker_prefetch_multiplier': 1,
    
    # Keep results for 24 hours
    'result_expires': 86400,
}

