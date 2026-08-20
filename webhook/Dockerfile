FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn pydantic
COPY webhook/alert_handler.py webhook/alert_handler.py
ENV PYTHONPATH=/app
CMD ["python", "-m", "uvicorn", "webhook.alert_handler:app", "--host", "0.0.0.0", "--port", "8002"]
