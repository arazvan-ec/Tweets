FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Build deps for transitive C/Cython packages (lxml, etc.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# Js2Py-3.13 is the specific fork twikit needs; pin it explicitly so pip
# doesn't substitute the (non-functional) plain Js2Py.
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && pip install Js2Py-3.13

COPY . .

# Web service runs on $PORT (Railway sets it). Default to 8080 locally.
ENV PORT=8080
EXPOSE 8080

# Default command: serve the web app. Cron service overrides this with
# `python scripts/fetch_tweets.py ...`.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 2 --timeout 60 server.main:app"]
