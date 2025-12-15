FROM python:3.11-slim

WORKDIR /app

# Update SSL certificates and pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates \
    && pip install --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir --retries 5 --timeout 60 -r requirements.txt

COPY . .

# Create logs directory for hourly log rotation
RUN mkdir -p /app/logs && chmod 777 /app/logs

EXPOSE 5003

CMD ["gunicorn", "--bind=0.0.0.0:5003", "app:app"]

