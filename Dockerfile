# KUNCI PERBAIKAN: Wajib ada baris FROM di paling atas!
FROM python:3.11-slim

# Set working directory di dalam container
WORKDIR /app

# 1. Install curl dan tools dasar terlebih dahulu
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# 2. Copy file requirement dan install dependencies Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Suruh Playwright menginstall sistem dependencies secara otomatis
RUN playwright install chromium --with-deps

# 4. Copy seluruh kode project ke dalam container
COPY . .

# Eksekusi aplikasi menggunakan Uvicorn saat container dinyalakan
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]