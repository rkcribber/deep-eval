FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create logs directory for hourly log rotation
RUN mkdir -p /app/logs && chmod 777 /app/logs

EXPOSE 5003

CMD ["gunicorn", "--bind=0.0.0.0:5003", "app:app"]

