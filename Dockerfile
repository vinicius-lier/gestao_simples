# Imagem de produção (Coolify, build pack "Dockerfile"). Passo a passo em
# deploy/COOLIFY.md.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app . .
USER app

# Os estáticos entram na imagem; o WhiteNoise serve /static/ (não há Nginx).
RUN python manage.py collectstatic --noinput

EXPOSE 8000

# A imagem slim não tem curl/wget: o health check usa o próprio Python.
# O Host enviado é 127.0.0.1 — ele precisa estar em ALLOWED_HOSTS.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/login/', timeout=4)" || exit 1

# Aplica as migrations a cada deploy e sobe o Gunicorn.
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${WEB_CONCURRENCY:-2} --timeout 60 --access-logfile - --error-logfile -"]
