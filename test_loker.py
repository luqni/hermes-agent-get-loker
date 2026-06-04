import logging
from scraper import HermesScraper

# Set logging level to INFO
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

if __name__ == "__main__":
    print("Mulai testing khusus Loker.id...")
    scraper = HermesScraper()
    
    # max_jobs dibatasi 3 untuk testing
    success_count = scraper.scrape_platform("Loker.id", max_jobs=3)
    
    print(f"\nSelesai! Berhasil memproses dan men-push {success_count} lowongan dari Loker.id.")
