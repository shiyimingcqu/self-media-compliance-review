FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    REVIEW_DATA_DIR=/var/lib/review \
    HF_HOME=/var/lib/review/cache/huggingface \
    IMAGEIO_FFMPEG_EXE=/usr/bin/ffmpeg

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgomp1 ca-certificates fonts-noto-cjk libreoffice-writer libreoffice-calc \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 review \
    && useradd --uid 10001 --gid review --create-home review \
    && mkdir -p /var/lib/review \
    && chown review:review /var/lib/review

WORKDIR /app/backend
COPY backend/requirements*.txt ./
ARG INSTALL_LOCAL_ASR=true
RUN pip install -r requirements-docker.txt \
    && if [ "$INSTALL_LOCAL_ASR" = "true" ]; then pip install -r requirements-asr.txt; fi
COPY --chown=review:review backend/app ./app
COPY --chown=review:review backend/migrations ./migrations
COPY --chown=review:review deploy/relocate_uploads.py ./tools/relocate_uploads.py
USER review
EXPOSE 8000
# One worker: startup recovery and in-process paid-call executors are single-instance.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-graceful-shutdown", "120"]
