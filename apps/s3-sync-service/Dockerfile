FROM python:3.12-slim

WORKDIR /app

# psycopg[binary] ships libpq — no system build deps needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8003

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8003"]
