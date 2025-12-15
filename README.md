# Deep Eval Flask API

A Flask API designed for long-running tasks (2+ minutes) using Celery and Redis.

## Document Processing Pipeline

When a PDF is submitted via `/api/data`, the following pipeline runs:

```
┌────────────────┐    ┌────────────────┐    ┌────────────────┐    ┌────────────────┐
│  1. Download   │───▶│   2. OCR with  │───▶│ 3. Evaluate    │───▶│ 4. Annotate    │
│     PDF        │    │   Vertex AI    │    │ with OpenAI    │    │    PDF         │
│                │    │   (Gemini)     │    │ Assistant      │    │                │
└────────────────┘    └────────────────┘    └────────────────┘    └────────────────┘
```

### Pipeline Steps

1. **Download PDF**: Downloads from the provided `student_uploaded_pdf_url`
2. **OCR with Vertex AI**: Extracts text and coordinates using Gemini 2.5 Pro
3. **Evaluate with OpenAI**: Sends OCR result to OpenAI Assistant for evaluation
4. **Annotate PDF**: Creates annotated PDF with evaluation comments in right margin

### Output Files

For each task, the following files are generated in the `output/` directory:

- `{task_id}_ocr.json` - OCR output with text and coordinates
- `{task_id}_evaluation.json` - OpenAI evaluation result
- `{task_id}_annotated.pdf` - Final annotated PDF with comments

## Architecture

```
┌─────────────┐     ┌─────────────────────────────────────┐
│   Client    │────▶│         Docker Compose              │
│             │     │  ┌───────────┐  ┌────────────────┐  │
└─────────────┘     │  │ Flask API │  │ Celery Workers │  │
      │             │  │ (Gunicorn)│  │  (2 workers)   │  │
      │             │  └─────┬─────┘  └───────┬────────┘  │
      │             │        │                │           │
      │             │        └───────┬────────┘           │
      │             │                │                    │
      │             │         ┌──────▼──────┐             │
      │             │         │    Redis    │             │
      │             │         └─────────────┘             │
      │             └─────────────────────────────────────┘
      │
      ▼ Poll /api/status/{task_id}
┌─────────────┐
│   Result    │
└─────────────┘
```

## How It Works

1. **Client** sends POST request to `/api/data`
2. **Flask API** validates request, queues task to Redis, returns `task_id` immediately (202 Accepted)
3. **Celery Workers** pick up tasks from Redis and process them
4. **Client** polls `/api/status/{task_id}` to check progress
5. **Result** is returned when task completes

## Quick Start

### Using Docker Compose (Recommended)

```bash
cd deep-eval-flask

# Start all services
docker-compose up --build

# Scale workers if needed
docker-compose up -d --scale worker=4
```

### Manual Setup (Development)

```bash
# Terminal 1: Start Redis
docker run -p 6379:6379 redis:7-alpine

# Terminal 2: Start Flask API
pip install -r requirements.txt
python app.py

# Terminal 3: Start Celery Worker
celery -A celery_app worker --loglevel=info
```

## API Endpoints

### Submit Task
```bash
curl -X POST http://localhost:5003/api/data \
  -H "Content-Type: application/json" \
  -d '{
    "student_uploaded_pdf_url": "https://example.com/student-answer.pdf",
    "uid": "53591"
  }'
```

**Required Fields:**
- `student_uploaded_pdf_url`: URL to the student's PDF file
- `uid`: Unique identifier for the mains copy (used to update external API)

Response (202 Accepted):
```json
{
  "status": "accepted",
  "task_id": "abc123-def456",
  "message": "Task queued for processing",
  "status_url": "/api/status/abc123-def456"
}
```

### Check Task Status
```bash
curl http://localhost:5003/api/status/{task_id}
```

Possible responses:
- **Pending**: `{"status": "pending", "task_id": "...", "state": "PENDING"}`
- **Processing**: `{"status": "processing", "task_id": "...", "progress": 45}`
- **Completed**: `{"status": "success", "task_id": "...", "progress": 100, "result": {...}}`
- **Failed**: `{"status": "error", "task_id": "...", "message": "Error details"}`

### Health Checks
```bash
curl http://localhost:5003/health         # API health
curl http://localhost:5003/health/celery  # Celery workers health
```

## DigitalOcean Deployment

### Single Droplet Setup ($48/month)

| Component | Resource | Specification |
|-----------|----------|---------------|
| All-in-One | Droplet | s-4vcpu-8gb |

```bash
# SSH into your droplet
ssh root@your-droplet-ip

# Install Docker
curl -fsSL https://get.docker.com | sh

# Clone your repo
git clone your-repo
cd deep-eval-flask

# Start everything
docker-compose up -d

# Scale workers as needed
docker-compose up -d --scale worker=4
```

## File Structure

```
deep-eval-flask/
├── app.py                    # Flask API application
├── celery_app.py             # Celery configuration
├── config.py                 # Environment configuration
├── tasks.py                  # Celery task definitions
├── logger.py                 # Structured logging with task context & hourly rotation
├── request_contract.json     # Request schema
├── response_contract.json    # Response schema
├── requirements.txt          # Python dependencies
├── Dockerfile                # API container
├── Dockerfile.worker         # Worker container
├── docker-compose.yml        # Docker setup
├── processing/               # Document processing module
│   ├── __init__.py
│   ├── document_processor.py # OCR and evaluation logic
│   ├── annotate_pdf.py       # PDF annotation logic
│   ├── pipeline.py           # Main pipeline orchestration
│   ├── gemini-prompt.txt     # Prompt for OCR
│   └── PatrickHand-Regular.ttf # Font for annotations
├── tmp/                      # Temporary files (per-task subdirs)
├── output/                   # Output files (annotated PDFs, JSONs)
├── logs/                     # Hourly rotating log files
└── README.md                 # This file
```

## Structured Logging

The application uses structured logging with **task_id context** and **hourly file rotation** for easy debugging in multi-request environments.

### Log File Structure

Logs are written to both:
1. **Console (stdout)** - for Docker logs
2. **Hourly rotating files** - in the `logs/` directory

File naming format: `app_YYYY-MM-DD_HH.log`

```
logs/
├── app_2025-12-13_10.log   # Logs from 10:00-11:00
├── app_2025-12-13_11.log   # Logs from 11:00-12:00
├── app_2025-12-13_12.log   # Logs from 12:00-13:00
└── ...
```

Log files are automatically:
- Rotated every hour
- Retained for 7 days (168 hourly files)
- Cleaned up automatically

### Log Format

Every log message includes the task_id, making it easy to filter logs for specific requests:

```
[2025-12-13 10:30:45] [INFO] [task_id=abc123-def456] Task started
[2025-12-13 10:30:46] [INFO] [task_id=abc123-def456] Downloading PDF from: https://example.com/doc.pdf
[2025-12-13 10:30:48] [INFO] [task_id=abc123-def456] PDF downloaded to: /app/tmp/abc123-def456/abc123-def456_input.pdf
[2025-12-13 10:30:48] [INFO] [task_id=abc123-def456] [10%] ocr_starting
[2025-12-13 10:31:00] [INFO] [task_id=abc123-def456] ✅ OCR Output saved to: /app/output/abc123-def456_ocr.json
[2025-12-13 10:31:30] [INFO] [task_id=abc123-def456] Task completed successfully. Pages processed: 16
```

### Filtering Logs by Task ID

```bash
# Filter logs for a specific task from files
grep "task_id=abc123-def456" logs/app_2025-12-13_10.log

# Search across all hourly logs
grep "task_id=abc123-def456" logs/app_*.log

# Filter logs from Docker (console output)
docker logs deep-eval-flask-worker-1 2>&1 | grep "task_id=abc123-def456"

# Watch logs for a specific task in real-time
docker logs -f deep-eval-flask-worker-1 2>&1 | grep "task_id=abc123-def456"
```

### Viewing Log Files

```bash
# List all log files
ls -la logs/

# View current hour's logs
cat logs/app_$(date +%Y-%m-%d_%H).log

# Tail the current log file
tail -f logs/app_$(date +%Y-%m-%d_%H).log

# View logs from a specific hour
cat logs/app_2025-12-13_14.log
```

### Using the Logger in Code

```python
from logger import get_task_logger, get_log_directory

# In your task function or pipeline
log = get_task_logger(task_id)
log.info("Starting OCR step")
log.warning("Low confidence result: %s", confidence)
log.error("Failed to process: %s", error_message)

# Get the log directory path
log_dir = get_log_directory()  # Returns '/app/logs' in Docker
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `REDIS_URL` | Redis connection URL | `redis://localhost:6379/0` |
| `LOG_DIR` | Directory for log files | `/app/logs` |
| `VERTEX_AI_API_KEY` | Google Vertex AI API key | (required) |
| `VERTEX_PROJECT_ID` | Google Cloud project ID | (required) |
| `VERTEX_LOCATION` | Vertex AI location | `us-central1` |
| `VERTEX_MODEL_NAME` | Gemini model name | `gemini-2.5-pro` |
| `OPENAI_API_KEY` | OpenAI API key | (required) |
| `OPENAI_ASSISTANT_ID` | OpenAI Assistant ID | (required) |
