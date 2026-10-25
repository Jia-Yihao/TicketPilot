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

COPY pyproject.toml README.md ./
COPY app ./app
COPY --from=web /web/dist ./web/dist
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts
COPY evals ./evals

RUN pip install . \
    && groupadd --system app \
    && useradd --system --gid app app \
    && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.asgi:app --host 0.0.0.0 --port 8000"]
