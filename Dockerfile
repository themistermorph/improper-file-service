FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Abhängigkeiten zuerst (Layer-Caching)
COPY pyproject.toml ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

# Spool-Verzeichnis für resumable Uploads
RUN mkdir -p /var/lib/ifs/spool

EXPOSE 8000 21
# Passive-Portrange für FTPS
EXPOSE 30000-30100

CMD ["python", "-m", "ifs.cli", "run-api"]
