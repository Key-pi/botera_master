FROM python:3.12-slim

ARG INSTALL_DEV=false

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY pyproject.toml README.md /app/
COPY apps /app/apps
COPY bot /app/bot
COPY core /app/core
RUN if [ "$INSTALL_DEV" = "true" ]; then \
        pip install --no-cache-dir ".[dev]"; \
    else \
        pip install --no-cache-dir "."; \
    fi

COPY . /app
