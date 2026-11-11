FROM python:3.12-slim

ARG INSTALL_DEV=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends --reinstall tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && python -c "from zoneinfo import ZoneInfo; ZoneInfo('Europe/Kyiv')"

RUN pip install --upgrade pip

COPY pyproject.toml README.md /app/
COPY apps /app/apps
COPY bot /app/bot
COPY core /app/core
RUN if [ "$INSTALL_DEV" = "true" ]; then \
        pip install ".[dev]"; \
    else \
        pip install "."; \
    fi

COPY . /app

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/staticfiles \
    && chown -R app:app /app/staticfiles

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
