FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir numpy requests scikit-learn fastapi uvicorn
COPY anomaly/detector.py anomaly/detector.py
ENV PYTHONPATH=/app
CMD ["python", "-m", "uvicorn", "anomaly.detector:app", "--host", "0.0.0.0", "--port", "8001"]
