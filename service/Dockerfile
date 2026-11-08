# Task 3 — inference service image. CPU-only, no model weights baked in.
# Build (from repo root):  docker build -t command-parser:latest -f service/Dockerfile .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=0 \
    TRANSFORMERS_NO_ADVISORY_WARNINGS=1

WORKDIR /app

# Install CPU-only PyTorch first (avoids the large CUDA libraries) — version pinned.
RUN pip install --no-cache-dir torch==2.2.2 --index-url https://download.pytorch.org/whl/cpu

# Then the remaining pinned dependencies.
COPY service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Application code + the schema used for output validation. NO model weights.
COPY service/app.py /app/app.py
COPY data/schema.json /app/schema.json

EXPOSE 8000
# single worker: the model is loaded once per process from object storage at start-up
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
