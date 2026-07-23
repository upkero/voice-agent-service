# Two targets from one file. The API serves HTTP and must stay small; the agent
# carries a speech model and a voice and cannot. Sharing the base keeps the
# dependency set identical between them, so the process that books a table runs
# the same code that was tested.

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock* ./

RUN uv sync --no-dev --no-install-project


FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="/app" \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --no-create-home appuser && \
    mkdir -p /app && \
    chown appuser:appgroup /app

WORKDIR /app

COPY --from=builder --chown=appuser:appgroup /app/.venv .venv
COPY --chown=appuser:appgroup src/ src/


# --- HTTP process -----------------------------------------------------------
FROM base AS api

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health/live')"

CMD ["uvicorn", "src.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8080", \
     "--workers", "1", \
     "--no-access-log"]


# --- Agent worker -----------------------------------------------------------
FROM base AS agent

ARG PIPER_VERSION=2023.11.14-2
ARG WHISPER_MODEL=small
ARG PIPER_VOICES="ru/ru_RU/irina/medium/ru_RU-irina-medium en/en_US/amy/medium/en_US-amy-medium"

# Models are baked in rather than downloaded at start-up. `docker compose up`
# then works on a machine that is offline, and a cold container does not make
# the first caller wait through a 500 MB download.
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Piper as a static binary, not the wheel: the wheel pulls phonemiser and
# onnxruntime builds whose availability varies by Python version, and a demo
# that fails to install is worse than one that shells out to a process.
RUN curl -fsSL -o /tmp/piper.tar.gz \
      "https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_linux_x86_64.tar.gz" && \
    tar -xzf /tmp/piper.tar.gz -C /opt && \
    rm /tmp/piper.tar.gz && \
    ln -s /opt/piper/piper /usr/local/bin/piper

RUN mkdir -p /opt/piper/voices && \
    for voice in ${PIPER_VOICES}; do \
      name="$(basename "$voice")"; \
      curl -fsSL -o "/opt/piper/voices/${name}.onnx" \
        "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/${voice}.onnx?download=true"; \
      curl -fsSL -o "/opt/piper/voices/${name}.onnx.json" \
        "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/${voice}.onnx.json?download=true"; \
    done && \
    chown -R appuser:appgroup /opt/piper

# Whisper and Silero, fetched at build time into the image's cache.
ENV HF_HOME=/opt/models/huggingface \
    TORCH_HOME=/opt/models/torch
RUN mkdir -p /opt/models && chown -R appuser:appgroup /opt/models

USER appuser

RUN python -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL}', device='cpu', compute_type='int8')" && \
    python -c "from livekit.plugins import silero; silero.VAD.load()"

# `start` rather than `dev`: dev watches the filesystem and reloads, which is
# not what a container should do to a live call.
CMD ["python", "-m", "src.entrypoint", "start"]
