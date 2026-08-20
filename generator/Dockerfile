FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir numpy requests
COPY generator/simulator.py generator/simulator.py
ENV PYTHONPATH=/app
CMD ["python", "-m", "generator.simulator"]
