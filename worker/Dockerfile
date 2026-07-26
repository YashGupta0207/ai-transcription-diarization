FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY worker/requirements.txt ./worker-requirements.txt
COPY backend/requirements.txt ./backend-requirements.txt
RUN pip install --no-cache-dir -r worker-requirements.txt -r backend-requirements.txt

COPY backend/app ./backend/app
COPY worker ./worker

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "worker.run_worker"]
