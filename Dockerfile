FROM node:24-slim AS web

WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md requirements.lock ./
COPY app ./app
COPY --from=web /web/dist ./web/dist
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts
COPY evals ./evals

RUN pip install --no-cache-dir -r requirements.lock \
    && pip install --no-deps . \
    && groupadd --system app \
    && useradd --system --gid app app \
    && chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD ["python", "-c", "import sys, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3); sys.exit(0 if r.status == 200 else 1)"]

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.asgi:app --host 0.0.0.0 --port 8000"]
