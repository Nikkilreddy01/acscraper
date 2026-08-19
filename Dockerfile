FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PORT=10000

WORKDIR /app

# Install system utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser and OS dependencies
RUN apt-get update && playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*

COPY . .

EXPOSE 10000

CMD ["python", "-m", "uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "10000"]
