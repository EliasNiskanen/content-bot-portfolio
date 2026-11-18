# Catfish Gaming -videobotti. amd64 (Windows / Docker Desktop) ja arm64 (Raspberry Pi 4/5, 64-bit OS).
#   docker compose up -d --build                     # schedule-silmukka taustalle
#   docker compose run --rm gta6-bot run --mock      # putkitesti
FROM python:3.12-slim-trixie

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data APP_DIR=/app TZ=Europe/Helsinki U2NET_HOME=/data/models
# ffmpeg (libass tekstityksille), fontit (DejaVu = config video.font, Liberation = Arial-varafontti), aikavyöhykkeet
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-dejavu-core fonts-liberation fontconfig tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

WORKDIR /app
COPY requirements.txt .
# onnxruntime = CPU-versio (rembg:n hahmonirrotus); ei pip-välimuistia kuvaan
RUN pip install -r requirements.txt
COPY bot ./bot
COPY assets ./assets
COPY config.yaml .

VOLUME ["/data"]
ENTRYPOINT ["python", "-m", "bot.main"]
CMD ["schedule"]
