FROM python:3.11-slim

WORKDIR /app

# Install dependencies for the app and Pillow/reportlab
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libjpeg-dev \
    zlib1g-dev \
    libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Note: Playwright browser scraping disabled on Railway (use API scrapers instead)
# To enable Playwright locally: pip install playwright && playwright install chromium

# Run the app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
