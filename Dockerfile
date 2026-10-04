FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 bot \
    && useradd --uid 10001 --gid 10001 --home-dir /app --no-create-home bot

COPY app/ ./app/
COPY assets/ ./assets/
COPY *.mp3 *.png ./

RUN mkdir -p /app/data && chown -R 10001:10001 /app/data
USER 10001:10001

CMD ["python", "-m", "app.main"]
