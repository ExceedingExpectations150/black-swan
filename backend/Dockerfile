# Black Swan backend — FastAPI + local TimesFM (PyTorch CPU) + SQLite.
# Long-running process (async tick loop + WebSocket): deploy as an always-on
# container, single instance, >= 2 GB RAM (torch + the TimesFM model).
FROM python:3.12-slim

WORKDIR /app

# Build tools some wheels need; removed after install to keep the image lean.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch FIRST so the requirements resolve against it.
#   x86_64  -> pull the CPU-only wheel (avoids the multi-GB CUDA build).
#   aarch64 -> the /whl/cpu index has no ARM wheels; the default PyPI torch
#              wheel is already CPU-only on ARM (Oracle Ampere A1), so use it.
RUN if [ "$(uname -m)" = "aarch64" ] || [ "$(uname -m)" = "arm64" ]; then \
        pip install --no-cache-dir torch; \
    else \
        pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu; \
    fi

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
