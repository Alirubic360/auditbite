FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# WeasyPrint (claim PDFs) needs pango + a font
RUN apt-get update && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install .
RUN useradd -m app && chown -R app /app
USER app
ENV AUDITBITE_ENV=production PORT=8000
EXPOSE 8000
# Two processes from one image (see fly.toml): API (default) and worker (`python -m auditbite.worker`).
CMD ["sh", "-c", "uvicorn auditbite.main:app --host 0.0.0.0 --port ${PORT}"]
