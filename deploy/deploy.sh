#!/usr/bin/env bash
set -euo pipefail

cd /home/ubuntu/apps/gestao_simples
# Also prevents two manual invocations of this script at once.
exec 9>/home/ubuntu/.academia-deploy.lock
flock -n 9

test -f .env
test -x .venv/bin/python
source .venv/bin/activate
# Do not source .env as shell code: Django loads it through python-dotenv.
python -m pip install --disable-pip-version-check -r requirements.txt
python -m pip check
python - <<'PY'
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
from django.conf import settings
if settings.DEBUG:
    raise SystemExit('Deploy requires DJANGO_DEBUG=false in the server .env')
if len(settings.SECRET_KEY) < 50 or settings.SECRET_KEY.startswith('django-insecure-'):
    raise SystemExit('Deploy requires a strong DJANGO_SECRET_KEY')
if not settings.ALLOWED_HOSTS or '*' in settings.ALLOWED_HOSTS:
    raise SystemExit('Configure explicit ALLOWED_HOSTS')
if settings.DATABASES['default']['ENGINE'] != 'django.db.backends.postgresql':
    raise SystemExit('Deploy requires the PostgreSQL backend')
PY
python manage.py check
python manage.py migrate --noinput
python manage.py collectstatic --noinput
sudo -n /usr/bin/systemctl restart academia-gunicorn
/usr/bin/systemctl is-active --quiet academia-gunicorn
# Wait for a real HTTP response; no external integrations are invoked.
python - <<'PY'
import os
import time
import urllib.request
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
from django.conf import settings
host = next((h for h in settings.ALLOWED_HOSTS if not h.startswith('.')), None)
if not host:
    raise SystemExit('Provide a concrete host in ALLOWED_HOSTS for the health check')
for attempt in range(10):
    try:
        req = urllib.request.Request('http://127.0.0.1:8000/login/', headers={'Host': host})
        with urllib.request.urlopen(req, timeout=3) as response:
            if response.status == 200:
                break
    except Exception:
        if attempt == 9:
            raise SystemExit('Gunicorn did not pass the HTTP health check')
        time.sleep(2)
else:
    raise SystemExit('Gunicorn returned an unexpected HTTP status')
PY
sudo -n /usr/bin/systemctl reload nginx
printf 'Deploy completed successfully.\n'
