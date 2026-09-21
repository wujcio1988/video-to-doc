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

# Copy package metadata for caching pip dependencies
COPY pyproject.toml .
COPY README.md .
COPY LICENSE .

# Pre-install dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Copy application source code
COPY vtd/ /app/vtd/
COPY config.example.yaml /app/config.yaml

# Final install of the vtd package
RUN pip install --no-cache-dir .

# Create volume directories
RUN mkdir -p /app/recordings /app/output /app/data

EXPOSE 9870

VOLUME ["/app/recordings", "/app/output", "/app/data"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:9870/health || exit 1

ENTRYPOINT ["vtd"]
CMD ["studio", "--host", "0.0.0.0", "--port", "9870"]
