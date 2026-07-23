FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY app/ ./app/
COPY main.py .

# Render injects $PORT at runtime
ENV PORT=8000
# Disable Python stdout buffering so logs appear immediately in Render
ENV PYTHONUNBUFFERED=1

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
