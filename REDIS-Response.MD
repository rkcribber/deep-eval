# Redis Deep Health Check Response

Documentation for the `GET /api/redis/deep-health-check` endpoint response.

## Response Structure

```json
{
    "status": "healthy",
    "service": "redis",
    "connection": { ... },
    "server": { ... },
    "memory": { ... },
    "stats": { ... },
    "queue": { ... },
    "keyspace": { ... }
}
```

---

## Fields Explained

### Top Level

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Overall health status: `"healthy"` or `"unhealthy"` |
| `service` | string | Service name, always `"redis"` |

---

### `connection`

Connection details to the Redis server.

| Field | Type | Description |
|-------|------|-------------|
| `ping` | boolean | `true` if Redis responded to PING command |
| `url` | string | Redis connection URL (password masked if present) |

---

### `server`

Redis server information.

| Field | Type | Description |
|-------|------|-------------|
| `redis_version` | string | Redis server version (e.g., `"7.4.7"`) |
| `uptime_seconds` | integer | How long Redis has been running (in seconds) |
| `uptime_days` | integer | How long Redis has been running (in days) |
| `connected_clients` | integer | Number of currently connected clients |
| `blocked_clients` | integer | Number of clients blocked on a blocking call (BLPOP, BRPOP, etc.) |
| `role` | string | Server role: `"master"` or `"slave"` |

---

### `memory`

Memory usage statistics.

| Field | Type | Description |
|-------|------|-------------|
| `used_memory_human` | string | Total memory allocated by Redis (human-readable, e.g., `"1.71M"`) |
| `used_memory_peak_human` | string | Peak memory consumed by Redis (human-readable) |
| `used_memory_rss_human` | string | Memory from OS perspective (Resident Set Size) - actual RAM used |
| `maxmemory_human` | string | Maximum memory limit configured (`"0B"` or `"unlimited"` means no limit) |
| `memory_fragmentation_ratio` | float | Ratio of `used_memory_rss` to `used_memory`. Values `> 1.5` indicate fragmentation; `< 1` indicates swapping |

#### Memory Fragmentation Ratio Guidelines:
- **1.0 - 1.5**: Healthy, normal operation
- **> 1.5**: High fragmentation, consider restarting Redis
- **< 1.0**: Redis is swapping to disk, add more RAM immediately

---

### `stats`

Performance and operation statistics.

| Field | Type | Description |
|-------|------|-------------|
| `total_connections_received` | integer | Total number of connections accepted since server start |
| `total_commands_processed` | integer | Total number of commands processed since server start |
| `instantaneous_ops_per_sec` | integer | Current operations per second |
| `rejected_connections` | integer | Number of connections rejected due to `maxclients` limit |
| `expired_keys` | integer | Total number of key expiration events |
| `evicted_keys` | integer | Number of keys evicted due to `maxmemory` limit (should be `0` ideally) |
| `keyspace_hits` | integer | Number of successful key lookups |
| `keyspace_misses` | integer | Number of failed key lookups |

#### Cache Hit Ratio Calculation:
```
hit_ratio = keyspace_hits / (keyspace_hits + keyspace_misses) * 100
```
A good cache hit ratio is **> 90%**.

---

### `queue`

Celery task queue information.

| Field | Type | Description |
|-------|------|-------------|
| `celery_queue_length` | integer | Number of tasks waiting in the Celery queue. `0` means all tasks are being processed or completed. |

#### Queue Length Guidelines:
- **0**: No backlog, workers are keeping up
- **1-10**: Normal, slight backlog
- **> 100**: Workers may be overloaded, consider scaling

---

### `keyspace`

Database-level key statistics. Each database (e.g., `db0`) has:

| Field | Type | Description |
|-------|------|-------------|
| `keys` | integer | Total number of keys in this database |
| `expires` | integer | Number of keys with an expiration set |
| `avg_ttl` | integer | Average TTL (time-to-live) in milliseconds for keys with expiration |
| `subexpiry` | integer | Number of subkeys with expiration (Redis 7.4+) |

---

## Example Response

```json
{
    "connection": {
        "ping": true,
        "url": "redis://redis:6379/0"
    },
    "keyspace": {
        "db0": {
            "avg_ttl": 86395590,
            "expires": 2,
            "keys": 5,
            "subexpiry": 0
        }
    },
    "memory": {
        "maxmemory_human": "0B",
        "memory_fragmentation_ratio": 8.7,
        "used_memory_human": "1.71M",
        "used_memory_peak_human": "1.77M",
        "used_memory_rss_human": "14.55M"
    },
    "queue": {
        "celery_queue_length": 0
    },
    "server": {
        "blocked_clients": 1,
        "connected_clients": 18,
        "redis_version": "7.4.7",
        "role": "master",
        "uptime_days": 0,
        "uptime_seconds": 142
    },
    "service": "redis",
    "stats": {
        "evicted_keys": 0,
        "expired_keys": 0,
        "instantaneous_ops_per_sec": 4,
        "keyspace_hits": 7,
        "keyspace_misses": 40,
        "rejected_connections": 0,
        "total_commands_processed": 443,
        "total_connections_received": 35
    },
    "status": "healthy"
}
```

---

## Error Responses

When Redis is unavailable:

```json
{
    "status": "unhealthy",
    "service": "redis",
    "error": "Connection failed",
    "message": "Error connecting to redis://redis:6379/0"
}
```

HTTP Status Code: `503 Service Unavailable`

---

## Health Indicators Summary

| Metric | Healthy | Warning | Critical |
|--------|---------|---------|----------|
| `status` | `"healthy"` | - | `"unhealthy"` |
| `memory_fragmentation_ratio` | 1.0 - 1.5 | 1.5 - 2.0 | > 2.0 or < 1.0 |
| `evicted_keys` | 0 | 1-100 | > 100 |
| `rejected_connections` | 0 | 1-10 | > 10 |
| `celery_queue_length` | 0-10 | 10-100 | > 100 |
| Cache hit ratio | > 90% | 70-90% | < 70% |

