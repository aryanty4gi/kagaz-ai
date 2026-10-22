# Kagaz AI: runs all six agents 24/7 on a cloud host (Render, Railway, Fly.io, any VM).
# Configure the same variables as .env (ASI1_API_KEY and the six KAGAZ_*_SEED values)
# in the host's environment settings. Never bake .env into the image.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# Tesseract OCR (English + Hindi) reads photos and screenshots of notices.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-hin \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# One process keeps memory low on small free plans (the coordinator's mailbox must
# already exist; create it once with run_all.py and the Agentverse inspector).
# Set KAGAZ_RUN_SCRIPT=run_all.py to run one process per agent instead.
ENV KAGAZ_RUN_SCRIPT=run_local.py
CMD ["sh", "-c", "python $KAGAZ_RUN_SCRIPT"]
