# 1. Install curl dan pip/dependencies dasar Anda terlebih dahulu
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# 2. Pastikan urutan setelah "pip install playwright" dijalankan di Dockerfile Anda:
RUN pip install -r requirements.txt

# 3. Suruh Playwright menginstall browser beserta seluruh dependensi sistemnya sendiri secara otomatis
RUN playwright install chromium --with-deps