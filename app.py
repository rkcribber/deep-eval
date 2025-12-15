"""
Flask API Application with Async Task Processing

This application uses Celery for handling long-running tasks (2+ minutes).
Requests are queued immediately and processed asynchronously.

Architecture:
- Flask API: Accepts requests, returns task_id immediately
- Celery Workers: Process tasks in background
- Redis: Message broker and result storage
"""

import json
import os
from flask import Flask, request, jsonify, url_for
from tasks import process_data_task
from celery.result import AsyncResult
from celery_app import celery
import redis
from config import REDIS_URL

app = Flask(__name__)

# Load contracts from files
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(BASE_DIR, 'request_contract.json')) as f:
    REQUEST_CONTRACT = json.load(f)

with open(os.path.join(BASE_DIR, 'response_contract.json')) as f:
    RESPONSE_CONTRACT = json.load(f)


@app.route('/api/data', methods=['POST'])
def submit_task():
    """
    Submit a task for async processing.
    Returns immediately with a task_id to check status later.

    Request body (from request_contract.json):
        - message (required): string
        - value (optional): number

    Returns:
        - task_id: Use this to check status at /api/status/<task_id>
        - status_url: Direct URL to check task status
    """
    # Check if request has JSON data
    if not request.is_json:
        error_response = RESPONSE_CONTRACT['error'].copy()
        error_response['message'] = "Request must be JSON"
        return jsonify(error_response), 400

    data = request.get_json()

    # Validate required fields from request contract
    for field in REQUEST_CONTRACT.get('required_fields', []):
        if field not in data:
            error_response = RESPONSE_CONTRACT['error'].copy()
            error_response['message'] = f"Missing required field: {field}"
            return jsonify(error_response), 400

    # Queue the task for async processing
    task = process_data_task.delay(data)

    # Build accepted response
    response = RESPONSE_CONTRACT['accepted'].copy()
    response['task_id'] = task.id
    response['status_url'] = f"/api/status/{task.id}"

    return jsonify(response), 202  # 202 Accepted

@app.route('/api/process', methods=['POST'])
def process_json():
    """
    Accept JSON payload for custom processing.

    Request body:
        - JSON object (structure TBD)

    Returns:
        - task_id: Use this to check status at /api/status/<task_id>
        - status_url: Direct URL to check task status
    """
    # Validate JSON request
    if not request.is_json:
        return jsonify({
            'status': 'error',
            'message': 'Request must be JSON'
        }), 400

    data = request.get_json()

    # TODO: Add validation for required fields
    # if 'required_field' not in data:
    #     return jsonify({'status': 'error', 'message': 'Missing required field'}), 400

    # TODO: Add processing logic here
    # For now, just echo back the received data

    # Option 1: Sync response (for quick processing)
    result = {
        'status': 'success',
        'message': 'JSON received successfully',
        'received_data': data
    }
    return jsonify(result), 200

    # Option 2: Async processing (uncomment when ready)
    # task = your_processing_task.delay(data)
    # return jsonify({
    #     'status': 'accepted',
    #     'task_id': task.id,
    #     'status_url': f"/api/status/{task.id}"
    # }), 202


@app.route('/api/status/<task_id>', methods=['GET'])
def get_task_status(task_id):
    """
    Check the status of a submitted task.

    Returns:
        - 404: Task ID not found (never existed or expired)
        - PENDING: Task is waiting in queue
        - PROCESSING: Task is being processed (includes progress %)
        - SUCCESS: Task completed, includes result
        - FAILURE: Task failed, includes error message
    """
    task_result = AsyncResult(task_id, app=celery)

    # Check if task exists in Redis backend
    # PENDING state could mean either "waiting in queue" or "task never existed"
    if task_result.state == 'PENDING':
        # For non-existent tasks, Celery returns PENDING with no metadata
        # We need to check if the task was ever actually submitted

        # Method 1: Check if task ID exists in Redis directly
        try:
            import redis as redis_lib
            from config import REDIS_URL
            r = redis_lib.from_url(REDIS_URL)

            # Celery stores task metadata with key pattern: celery-task-meta-{task_id}
            task_key = f"celery-task-meta-{task_id}"
            exists = r.exists(task_key)

            if not exists:
                # Task was never submitted or has expired
                return jsonify({
                    'status': 'not_found',
                    'task_id': task_id,
                    'message': 'Task not found. It may have never existed or has expired.'
                }), 404

        except Exception as e:
            # If Redis check fails, fall back to returning pending
            # (safer than returning 404 incorrectly)
            pass

        response = RESPONSE_CONTRACT['pending'].copy()
        response['task_id'] = task_id

    elif task_result.state == 'PROCESSING':
        response = RESPONSE_CONTRACT['processing'].copy()
        response['task_id'] = task_id
        response['progress'] = task_result.info.get('progress', 0)

    elif task_result.state == 'SUCCESS':
        response = RESPONSE_CONTRACT['success'].copy()
        response['task_id'] = task_id
        response['state'] = 'SUCCESS'
        response['progress'] = 100
        response['result'] = task_result.result

    elif task_result.state == 'FAILURE':
        response = RESPONSE_CONTRACT['error'].copy()
        response['task_id'] = task_id
        response['message'] = str(task_result.info)
        return jsonify(response), 500

    else:
        response = {
            'status': 'unknown',
            'task_id': task_id,
            'state': task_result.state
        }

    return jsonify(response), 200


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint to verify the API is running."""
    return jsonify({
        "status": "healthy",
        "service": "flask-api"
    }), 200


@app.route('/health/celery', methods=['GET'])
def celery_health_check():
    """Check if Celery workers are available."""
    try:
        # Ping Celery workers
        inspect = celery.control.inspect()
        stats = inspect.stats()

        if stats:
            return jsonify({
                "status": "healthy",
                "service": "celery",
                "workers": list(stats.keys())
            }), 200
        else:
            return jsonify({
                "status": "unhealthy",
                "service": "celery",
                "message": "No workers available"
            }), 503
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "service": "celery",
            "message": str(e)
        }), 503


@app.route('/api/redis/deep-health-check', methods=['GET'])
def redis_deep_health_check():
    """
    Detailed Redis health check endpoint.
    Returns comprehensive information about Redis status, memory, clients, and more.
    """
    try:
        # Connect to Redis
        r = redis.from_url(REDIS_URL)

        # Ping to check connection
        ping_response = r.ping()

        # Get detailed server info
        info = r.info()

        # Get memory info
        memory_info = r.info('memory')

        # Get client info
        client_info = r.info('clients')

        # Get stats
        stats_info = r.info('stats')

        # Get keyspace info
        keyspace_info = r.info('keyspace')

        # Get current queue length (Celery default queue)
        queue_length = r.llen('celery')

        # Build response
        response = {
            "status": "healthy" if ping_response else "unhealthy",
            "service": "redis",
            "connection": {
                "ping": ping_response,
                "url": REDIS_URL.replace(r.connection_pool.connection_kwargs.get('password', ''), '***') if r.connection_pool.connection_kwargs.get('password') else REDIS_URL
            },
            "server": {
                "redis_version": info.get('redis_version'),
                "uptime_seconds": info.get('uptime_in_seconds'),
                "uptime_days": info.get('uptime_in_days'),
                "connected_clients": client_info.get('connected_clients'),
                "blocked_clients": client_info.get('blocked_clients'),
                "role": info.get('role'),
            },
            "memory": {
                "used_memory_human": memory_info.get('used_memory_human'),
                "used_memory_peak_human": memory_info.get('used_memory_peak_human'),
                "used_memory_rss_human": memory_info.get('used_memory_rss_human'),
                "maxmemory_human": memory_info.get('maxmemory_human') or 'unlimited',
                "memory_fragmentation_ratio": memory_info.get('mem_fragmentation_ratio'),
            },
            "stats": {
                "total_connections_received": stats_info.get('total_connections_received'),
                "total_commands_processed": stats_info.get('total_commands_processed'),
                "instantaneous_ops_per_sec": stats_info.get('instantaneous_ops_per_sec'),
                "rejected_connections": stats_info.get('rejected_connections'),
                "expired_keys": stats_info.get('expired_keys'),
                "evicted_keys": stats_info.get('evicted_keys'),
                "keyspace_hits": stats_info.get('keyspace_hits'),
                "keyspace_misses": stats_info.get('keyspace_misses'),
            },
            "queue": {
                "celery_queue_length": queue_length,
            },
            "keyspace": keyspace_info
        }

        return jsonify(response), 200

    except redis.ConnectionError as e:
        return jsonify({
            "status": "unhealthy",
            "service": "redis",
            "error": "Connection failed",
            "message": str(e)
        }), 503
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "service": "redis",
            "error": "Unexpected error",
            "message": str(e)
        }), 503


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5003)

