FROM python:3.14-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV STAGEVIVA_DB=/app/data/stageviva.db
RUN mkdir -p /app/data
VOLUME ["/app/data"]
EXPOSE 8000

# Hosting providers such as Render supply PORT at runtime.
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}"]
