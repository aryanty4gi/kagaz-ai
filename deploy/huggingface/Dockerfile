# Hugging Face Space (Docker SDK) for Kagaz AI.
# Builds from the public GitHub repo. Set ASI1_API_KEY and the six KAGAZ_*_SEED
# values as Space secrets so the agent addresses stay the same.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1 \
    KAGAZ_ONLINE_CHECKS=true PORT=7860
WORKDIR /home/user/app
# Change this value (or use "Factory rebuild") to pull the latest code.
ARG CACHEBUST=1
RUN git clone --depth 1 https://github.com/aryanty4gi/kagaz-ai.git .
RUN pip install --no-cache-dir --user -r requirements.txt
EXPOSE 7860
CMD ["python", "run_all.py"]
