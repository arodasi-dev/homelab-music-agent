FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# ffmpeg converte o áudio pra m4a (o iPhone toca); nodejs é o runtime JS que o yt-dlp usa pro YouTube.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg nodejs \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py accounts.py manage.py ./

RUN useradd --system --no-create-home music && mkdir /data && chown music /data
USER music

EXPOSE 8091
CMD ["gunicorn", "--bind", "0.0.0.0:8091", "--workers", "1", "--threads", "8", "--timeout", "120", "agent:create_app_from_env()"]
