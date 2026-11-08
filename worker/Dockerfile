# Task 1 — teacher-labelling worker image.
# Build (from repo root):  docker build -t teacher-worker:latest -f worker/Dockerfile .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY worker/worker.py /app/worker.py
COPY worker/clean.py /app/clean.py
COPY prompts/teacher_prompt.txt /app/prompts/teacher_prompt.txt

ENV PROMPT_PATH=/app/prompts/teacher_prompt.txt
CMD ["python", "worker.py"]
