import httpx
from bs4 import BeautifulSoup
import sys

def test_httpx():
    url = "https://www.loker.id/cari-lowongan-kerja"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        print("Testing standard httpx request...")
        with httpx.Client(timeout=15.0) as client:
            response = client.get(url, headers=headers)
            print(f"Status Code: {response.status_code}")
            print(f"Response Headers: {dict(response.headers)}")
            soup = BeautifulSoup(response.text, 'html.parser')
            links = []
            for a in soup.find_all('a', href=True):
                href = a['href']
                if "loker.id" in href or href.startswith('/'):
                    links.append(href)
            print(f"Found {len(links)} links on the page.")
            if len(links) <= 5:
                print("First 200 chars of HTML:")
                print(response.text[:200])
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_httpx()
