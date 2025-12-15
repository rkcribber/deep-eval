# Celery Configuration Settings

Documentation for `config.py` - Celery settings optimized for long-running tasks (5+ minutes).

## Configuration Overview

```python
CELERY_CONFIG = {
    'broker_url': REDIS_URL,
    'result_backend': REDIS_URL,
    'task_serializer': 'json',
    'result_serializer': 'json',
    'accept_content': ['json'],
    'timezone': 'UTC',
    'enable_utc': True,
    'task_track_started': True,
    'task_time_limit': 420,
    'task_soft_time_limit': 360,
    'broker_transport_options': {
        'visibility_timeout': 600,
    },
    'worker_prefetch_multiplier': 1,
    'result_expires': 86400,
}
```

---

## Settings Explained

### Connection Settings

| Setting | Value | Description |
|---------|-------|-------------|
| `broker_url` | `REDIS_URL` | Redis URL for message queue (task broker) |
| `result_backend` | `REDIS_URL` | Redis URL for storing task results |

---

### Serialization Settings

| Setting | Value | Description |
|---------|-------|-------------|
| `task_serializer` | `'json'` | Format for serializing task arguments |
| `result_serializer` | `'json'` | Format for serializing task results |
| `accept_content` | `['json']` | Allowed content types for deserialization |

---

### Timezone Settings

| Setting | Value | Description |
|---------|-------|-------------|
| `timezone` | `'UTC'` | Timezone for scheduled tasks |
| `enable_utc` | `True` | Use UTC internally for all times |

---

### Task Tracking

| Setting | Value | Description |
|---------|-------|-------------|
| `task_track_started` | `True` | Track when tasks start (enables `STARTED` state) |

---

### Timeout Settings (Critical for Long Tasks)

| Setting | Value | Description |
|---------|-------|-------------|
| `task_time_limit` | `420` (7 min) | **Hard limit** - Worker is killed if task exceeds this |
| `task_soft_time_limit` | `360` (6 min) | **Soft limit** - Raises `SoftTimeLimitExceeded` exception, allows graceful cleanup |

#### How Timeouts Work:

```
Task starts
    │
    ├── 5 min ──── Normal completion ✓
    │
    ├── 6 min ──── Soft limit hit → SoftTimeLimitExceeded raised
    │              Task can catch this and cleanup
    │
    └── 7 min ──── Hard limit hit → Worker KILLED
                   Task terminated immediately
```

#### Recommended Values by Task Duration:

| Task Duration | `task_soft_time_limit` | `task_time_limit` |
|---------------|------------------------|-------------------|
| 2 minutes | 150 (2.5 min) | 180 (3 min) |
| 5 minutes | 360 (6 min) | 420 (7 min) |
| 10 minutes | 660 (11 min) | 720 (12 min) |
| 30 minutes | 1920 (32 min) | 2100 (35 min) |

---

### Broker Transport Options

| Setting | Value | Description |
|---------|-------|-------------|
| `visibility_timeout` | `600` (10 min) | Time before Redis re-queues an unacknowledged task |

#### Why This Matters:

When a worker picks up a task, Redis marks it as "invisible" to other workers. If the worker doesn't acknowledge completion within `visibility_timeout`, Redis assumes the worker died and **re-queues the task**.

**Problem**: If `visibility_timeout` < task duration → duplicate task execution

**Rule**: `visibility_timeout` should be **at least 2x** your longest task duration.

| Task Duration | Minimum `visibility_timeout` |
|---------------|------------------------------|
| 2 minutes | 300 (5 min) |
| 5 minutes | 600 (10 min) |
| 10 minutes | 1200 (20 min) |

---

### Worker Settings

| Setting | Value | Description |
|---------|-------|-------------|
| `worker_prefetch_multiplier` | `1` | Number of tasks a worker prefetches |

#### Why `1` for Long Tasks:

- **Default** (`4`): Worker grabs 4 tasks at once, processes them sequentially
- **Problem**: If each task takes 5 min, tasks 2-4 wait 5-15 min before starting
- **Solution**: Set to `1` so workers only take tasks they can immediately process

| Task Duration | Recommended `worker_prefetch_multiplier` |
|---------------|------------------------------------------|
| < 10 seconds | 4 (default) |
| 10 sec - 1 min | 2 |
| > 1 minute | 1 |

---

### Result Settings

| Setting | Value | Description |
|---------|-------|-------------|
| `result_expires` | `86400` (24 hr) | How long to keep task results in Redis |

After this time, calling `AsyncResult(task_id)` will return `PENDING` even for completed tasks.

| Use Case | Recommended `result_expires` |
|----------|------------------------------|
| Short-lived results | 3600 (1 hour) |
| Standard | 86400 (24 hours) |
| Long-term tracking | 604800 (7 days) |

---

## Adjusting for Different Task Durations

### For 2-Minute Tasks

```python
CELERY_CONFIG = {
    # ... other settings ...
    'task_time_limit': 180,
    'task_soft_time_limit': 150,
    'broker_transport_options': {
        'visibility_timeout': 300,
    },
}
```

### For 5-Minute Tasks (Current)

```python
CELERY_CONFIG = {
    # ... other settings ...
    'task_time_limit': 420,
    'task_soft_time_limit': 360,
    'broker_transport_options': {
        'visibility_timeout': 600,
    },
}
```

### For 10-Minute Tasks

```python
CELERY_CONFIG = {
    # ... other settings ...
    'task_time_limit': 720,
    'task_soft_time_limit': 660,
    'broker_transport_options': {
        'visibility_timeout': 1200,
    },
}
```

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `REDIS_URL` | Redis connection string | `redis://localhost:6379/0` |

### Redis URL Formats:

```bash
# Local Redis
REDIS_URL=redis://localhost:6379/0

# Redis with password
REDIS_URL=redis://:password@localhost:6379/0

# DigitalOcean Managed Redis (TLS)
REDIS_URL=rediss://default:password@host:25061/0
```

