FROM python:3.12-slim

# Faster, cleaner Python in containers.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Non-root runtime user; /data is the SQLite volume mount point.
RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --create-home app \
    && mkdir -p /data \
    && chown app:app /data

WORKDIR /app

# Project metadata + source. setuptools' packages.find includes `app*`, so the
# package must be present at build time for the wheel install to succeed.
COPY pyproject.toml ./
COPY app ./app
RUN pip install .

COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x entrypoint.sh

USER app

EXPOSE 8080

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.runner.app:app", "--host", "0.0.0.0", "--port", "8080"]
