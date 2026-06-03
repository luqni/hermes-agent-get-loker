import logging
from scraper import HermesScraper

# Set logging level ke INFO agar terlihat di terminal
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

if __name__ == "__main__":
    print("Mulai testing khusus KitaLulus...")
    scraper = HermesScraper()
    
    # max_jobs dibatasi 3 agar tidak terlalu lama saat testing
    success_count = scraper.scrape_platform("KitaLulus", max_jobs=3)
    
    print(f"\nSelesai! Berhasil memproses dan men-push {success_count} lowongan dari KitaLulus.")
