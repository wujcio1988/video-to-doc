FROM python:3.11-slim-bookworm

# Avoid prompts from apt
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system dependencies: ffmpeg, tesseract-ocr with Polish & English models, curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-pol \
    tesseract-ocr-eng \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# GF-024: jeden `pip install .` — wcześniej pakiet instalowany dwa razy
# (drugie instalowanie po skopiowaniu źródeł niczego nie zmieniało w skonfigurowanym
# obrazie, a wydłużało build). Config NIE jest już kopiowany jako /app/config.yaml —
# konfiguracja wyłącznie przez mount/env.
COPY . .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir . && \
    rm -f /app/config.yaml

# GF-024: kontener nie działa jako root
RUN useradd --create-home --uid 10001 vtd && \
    mkdir -p /app/recordings /app/output /app/data && \
    chown -R vtd:vtd /app
USER vtd

EXPOSE 9870

VOLUME ["/app/recordings", "/app/output", "/app/data"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:9870/health || exit 1

# VTD-003: --host 0.0.0.0 jest bezpieczny tylko z ustawionym VTD_AUTH_TOKEN
# (bez tokenu Studio wymusza bind na 127.0.0.1 i loguje ostrzeżenie).
ENTRYPOINT ["vtd"]
CMD ["studio", "--host", "0.0.0.0", "--port", "9870"]
