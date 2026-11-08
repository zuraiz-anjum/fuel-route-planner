# One container with SQLite inside it. The stations are loaded from the
# price file at every start, so there is nothing worth keeping between runs.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_DEBUG=0 \
    DJANGO_ALLOWED_HOSTS=*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# A throwaway key: collectstatic only needs the settings to load.
RUN DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput \
    && useradd --system --create-home app \
    && chown -R app:app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz/')"

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["gunicorn", "fuelroute.wsgi", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "60"]
