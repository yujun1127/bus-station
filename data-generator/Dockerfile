FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    cron curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fetch_data.py .
COPY entrypoint.sh .
COPY crontab.txt .
COPY nearby.html .

RUN mkdir -p /app/output \
 && chmod +x entrypoint.sh \
 && crontab crontab.txt

CMD ["./entrypoint.sh"]
