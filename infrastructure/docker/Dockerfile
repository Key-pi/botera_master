FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY pyproject.toml README.md /app/
COPY apps /app/apps
COPY bot /app/bot
COPY core /app/core
RUN pip install --no-cache-dir ".[dev]"

COPY . /app
