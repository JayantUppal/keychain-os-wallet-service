FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

# Warm the schema on boot, then serve with gunicorn.
CMD ["sh", "-c", "alembic upgrade head && gunicorn -w 4 -b 0.0.0.0:5000 'wallet_service.app:create_app()'"]
