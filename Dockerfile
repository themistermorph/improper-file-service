# Cache-freundliche Reihenfolge: Abhängigkeiten zuerst (eigene Schicht), Quellcode danach.
# Der Code wird über PYTHONPATH eingebunden – reine Code-/UI-Änderungen lösen damit
# KEINE pip-Installation mehr aus (nur COPY src + Image-Export).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# 1) Laufzeit-Abhängigkeiten (ändert sich nur bei pyproject.toml/requirements.txt).
#    Der BuildKit-Cache beschleunigt auch den (seltenen) Neu-Download.
COPY pyproject.toml requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && pip install -r requirements.txt

# 2) Anwendungscode
COPY src ./src

# Spool-Verzeichnis für resumable Uploads
RUN mkdir -p /var/lib/ifs/spool

EXPOSE 8000 21
# Passive-Portrange für FTPS
EXPOSE 30000-30100

CMD ["python", "-m", "ifs.cli", "run-api"]
