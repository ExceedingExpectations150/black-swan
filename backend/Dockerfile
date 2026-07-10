# Black Swan backend — FastAPI + local TimesFM (PyTorch CPU) + SQLite.
# Long-running process (async tick loop + WebSocket): deploy as an always-on
# container, single instance, >= 2 GB RAM (torch + the TimesFM model).
FROM python:3.12-slim

WORKDIR /app

# Build tools some wheels need; removed after install to keep the image lean.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install the CPU-only PyTorch wheel FIRST so the requirements resolve to it
# instead of pulling the multi-GB CUDA build.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
# The TimesFM checkpoint (~1 GB) downloads from Hugging Face on the FIRST
# /api/start and is cached here; the first start is therefore slow.
ENV HF_HOME=/app/.hf-cache

EXPOSE 8000
# Hosts inject $PORT; default to 8000 locally.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
