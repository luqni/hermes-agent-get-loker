import os
import re
import json
import logging
import time
import random
import urllib.parse
from datetime import datetime
from bs4 import BeautifulSoup
import httpx
from playwright.sync_api import sync_playwright

try:
    from playwright_stealth import stealth_sync
except ImportError:
    stealth_sync = None

from config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("hermes.scraper")

PLATFORM_CONFIGS = {
    'LinkedIn': {
        'search_url': "https://www.linkedin.com/jobs/search?keywords=loker%20indonesia&location=Indonesia&geoId=102478259&f_TPR=r604800",
        'fallback_query': "site:linkedin.com/jobs/view/ \"Indonesia\" \"loker\"",
        'use_browser': True
    },
    'JobStreet': {
        'search_url': "https://id.jobstreet.com/id/jobs?daterange=7",
        'fallback_query': "site:id.jobstreet.com/id/job/ \"loker terbaru\"",
        'use_browser': True
    },
    'Karirhub Kemnaker': {
        'search_url': "https://karirhub.kemnaker.go.id/",
        'fallback_query': "site:karirhub.kemnaker.go.id/lowongan/",
        'use_browser': True
    },
    'KitaLulus': {
        'search_url': "https://www.kitalulus.com/lowongan-kerja",
        'fallback_query': "site:kitalulus.com/lowongan-kerja/ \"Jakarta\"",
        'use_browser': True
    },
    'Loker.id': {
        'search_url': "https://www.loker.id/",  
        'fallback_query': "site:loker.id/ \"lowongan kerja terbaru\"",
        'use_browser': True
    }
}

class HermesScraper:
    def __init__(self):
        logger.info(f"HermesScraper initialized. AI Provider: {settings.AI_PROVIDER}, Configured Model: {settings.GEMINI_MODEL}")
        
        # --- FIX KONSISTENSI FOLDER EASYPANEL ---
        if not os.path.exists(settings.DATA_DIR):
            os.makedirs(settings.DATA_DIR, exist_ok=True)
            logger.info(f"Created persistent data directory at: {settings.DATA_DIR}")

        self.visited_file = os.path.join(settings.DATA_DIR, "scraped_jobs.txt")
        self.visited_urls = set()
        self._load_visited_urls()

    def _get(self, url: str, headers: dict = None, timeout: float = 25.0) -> httpx.Response:
        req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        if headers:
            req_headers.update(headers)
        with httpx.Client(timeout=timeout) as client:
            return client.get(url, headers=req_headers)

    def _post(self, url: str, json_data: dict, headers: dict = None, timeout: float = 25.0) -> httpx.Response:
        req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        if headers:
            req_headers.update(headers)
        with httpx.Client(timeout=timeout) as client:
            return client.post(url, json=json_data, headers=req_headers)

    def _normalize_url(self, url: str) -> str:
        try:
            url = url.strip()
            parsed = urllib.parse.urlparse(url)
            netloc = parsed.netloc.lower()
            path_lower = parsed.path.lower()
            
            # --- FIX SUPER DUPLIKAT LINKEDIN ---
            if 'linkedin.com' in netloc:
                qs = urllib.parse.parse_qs(parsed.query)
                if 'currentJobId' in qs:
                    return f"https://www.linkedin.com/jobs/view/{qs['currentJobId'][0]}"
                
                match_id = re.search(r'(\d{8,12})', path_lower)
                if match_id:
                    return f"https://www.linkedin.com/jobs/view/{match_id.group(1)}"
                return f"https://www.linkedin.com{parsed.path.rstrip('/')}"
                
            # --- FIX SUPER DUPLIKAT JOBSTREET (SEEK ERA) ---
            elif 'jobstreet' in netloc:
                match_id = re.search(r'/job/(\d+)', path_lower)
                if match_id:
                    return f"https://id.jobstreet.com/job/{match_id.group(1)}"
                return f"https://id.jobstreet.com{parsed.path.rstrip('/')}"
                
            # --- FIX SUPER DUPLIKAT INDEED ---
            elif 'indeed.com' in netloc:
                qs = urllib.parse.parse_qs(parsed.query)
                if 'jk' in qs:
                    return f"https://id.indeed.com/viewjob?jk={qs['jk'][0]}"
                return f"https://id.indeed.com{parsed.path.rstrip('/')}"

            # --- FIX SUPER DUPLIKAT KARIRHUB KEMNAKER ---
            elif 'karirhub' in netloc:
                match = re.search(r'/lowongan/([a-zA-Z0-9-]+)', path_lower)
                if match:
                    return f"https://karirhub.kemnaker.go.id/lowongan/{match.group(1)}"
                return f"https://karirhub.kemnaker.go.id{parsed.path.rstrip('/')}"
            elif 'kitalulus' in netloc:
                return f"https://www.kitalulus.com{parsed.path}"
            elif 'loker.id' in netloc:
                qs = urllib.parse.parse_qs(parsed.query)
                if 'jobid' in qs:
                    return f"https://www.loker.id/cari-lowongan-kerja?jobid={qs['jobid'][0]}"
                return f"https://www.loker.id{parsed.path.rstrip('/')}"

            return f"{parsed.scheme}://{netloc}{parsed.path.rstrip('/')}"
        except Exception as e:
            logger.error(f"Error normalizing URL {url}: {str(e)}")
            return url

    def _is_valid_job_url(self, url: str, platform_name: str) -> bool:
        url_lower = url.lower()
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.strip("/")
        if not path or len(path) < 4:
            return False
            
        if "linkedin" in platform_name.lower():
            return "/jobs/view/" in url_lower
        elif "jobstreet" in platform_name.lower():
            return "/job/" in url_lower
        elif "indeed" in platform_name.lower():
            return "/viewjob" in url_lower or "/rc/clk" in url_lower
        elif "kitalulus" in platform_name.lower():
            return "/lowongan-kerja/" in url_lower or "/lowongan/detail/" in url_lower
        elif "loker.id" in platform_name.lower():
            return "cari-lowongan-kerja" not in url_lower and "lokasi-pekerjaan" not in url_lower and len(path) > 10
        elif "karirhub" in platform_name.lower() or "kemnaker" in platform_name.lower():
            return "/lowongan/" in url_lower
            
        return True

    def _load_visited_urls(self):
        if os.path.exists(self.visited_file):
            with open(self.visited_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        self.visited_urls.add(self._normalize_url(line.strip()))

    def _mark_url_processed(self, url: str):
        norm_url = self._normalize_url(url)
        if norm_url not in self.visited_urls:
            self.visited_urls.add(norm_url)
            with open(self.visited_file, "a", encoding="utf-8") as f:
                f.write(norm_url + "\n")

    def fetch_page_content(self, url: str, use_browser: bool = False) -> str:
        if not use_browser:
            try:
                response = self._get(url)
                if response.status_code == 200:
                    return response.text
            except Exception as e:
                logger.error(f"Error in standard fetch for {url}: {str(e)}")

        logger.info(f"Using advanced browser automation to load: {url}")
        try:
            with sync_playwright() as p:
                with p.chromium.launch(headless=True) as browser:
                    with browser.new_context(
                        ignore_https_errors=True,
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                        viewport={"width": 1280, "height": 720}
                    ) as context:
                        page = context.new_page()
                        if stealth_sync:
                            stealth_sync(page)
                            
                        page.goto(url, wait_until="domcontentloaded", timeout=40000)
                        page.wait_for_timeout(random.randint(2500, 5000))
                        page.evaluate(f"window.scrollBy(0, {random.randint(350, 450)})")
                        page.wait_for_timeout(random.randint(1500, 3000))
                        
                        content = page.content()
                        page.close()
                        return content
        except Exception as e:
            logger.error(f"Playwright automation failed for {url}: {str(e)}")
            return ""

    def discover_loker_id_locations(self) -> list:
        homepage_url = "https://www.loker.id"
        logger.info(f"🔍 [Discover] Membaca filter lokasi langsung dari {homepage_url}...")
        
        html = self.fetch_page_content(homepage_url, use_browser=True)
        if not html:
            logger.warning("⚠️ [Discover] Gagal memuat homepage Loker.id untuk scanning lokasi.")
            return []
            
        soup = BeautifulSoup(html, 'html.parser')
        discovered_urls = []
        
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/lokasi-pekerjaan/' in href:
                if href.startswith('/'):
                    href = "https://www.loker.id" + href
                
                if href not in discovered_urls:
                    discovered_urls.append(href)
                    
        logger.info(f"✨ [Discover] Berhasil menemukan {len(discovered_urls)} lokasi aktif di Loker.id!")
        return discovered_urls

    def _prepare_ai_request(self, system_instruction: str, prompt_text: str) -> tuple:
        provider = settings.AI_PROVIDER.lower()
        model = settings.GEMINI_MODEL
        key = settings.GEMINI_API_KEY

        if provider == "openrouter":
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://hermes-agent.internal", 
                "X-Title": "Hermes Autonomous Job Scraper Bot"
            }
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt_text}
                ],
            }
        else:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [{"parts": [{"text": f"{system_instruction}\n\n{prompt_text}"}]}]
            }
            if "gemma" not in model.lower():
                payload["generationConfig"] = {"responseMimeType": "application/json"}
                
        return url, headers, payload

    def _parse_ai_response(self, response_json: dict) -> str:
        try:
            if settings.AI_PROVIDER.lower() == "openrouter":
                return response_json['choices'][0]['message']['content']
            else:
                return response_json['candidates'][0]['content']['parts'][0]['text']
        except Exception as e:
            logger.error(f"Failed parsing response structure: {str(e)}")
            return ""

    def extract_job_urls_with_ai(self, html_content: str, platform_name: str) -> list:
        regex_fallbacks = {
            'LinkedIn': r'linkedin\.com/jobs/view/[0-9]+',
            'JobStreet': r'(?:jobstreet\.(?:com|co\.id))?/[^"\'\s<>]+?/job/[0-9]+',
            'Indeed': r'(?:indeed\.com)?/(?:rc/clk|viewjob)\?[^"\'\s<>]+',
            'KitaLulus': r'kitalulus\.com/lowongan-kerja/[^"\'\s<>]+|kitalulus\.com/lowongan/detail/[^"\'\s<>]+',
            'Loker.id': r'loker\.id/[^"\'\s<>]+', 
            'Karirhub Kemnaker': r'(?:karirhub\.kemnaker\.go\.id)?/lowongan/[^"\'\s<>]+'
        }

        soup = BeautifulSoup(html_content, 'html.parser')
        links = []
        raw_urls_for_regex = []

        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(strip=True)[:30]
            if len(href) > 15 and not any(x in href.lower() for x in ['javascript:', 'help', 'privacy', 'terms', 'cookie', 'settings']):
                links.append({"text": text, "url": href})
                raw_urls_for_regex.append(href)

        if not links:
            logger.warning(f"No raw links found in HTML for {platform_name}.")
            return []
        if not settings.GEMINI_API_KEY:
            return []

        logger.info(f"Found {len(links)} cleaned links on {platform_name}. Querying AI filtering...")
        
        # --- BLOK YANG MENYEBABKAN ERROR (SUDAH DIBERSIHKAN) ---
        system_instruction = "You are a JSON parser. Output a clean JSON array of strings containing valid job post URLs."
        prompt = (
            f"Filter the list from {platform_name} and return ONLY direct job detail page URLs.\n"
            f"Data:\n{json.dumps(links[:80])}\n\n"
            f"Return clean JSON array of strings only."
        )

        url, headers, payload = self._prepare_ai_request(system_instruction, prompt)

        for attempt in range(3):
            try:
                response = self._post(url, json_data=payload, headers=headers, timeout=25.0)
                if response.status_code == 200:
                    res_text = self._parse_ai_response(response.json()).strip()
                    res_text = re.sub(r'```json\s*|\s*```', '', res_text)
                    
                    first_bracket = res_text.find('[')
                    last_bracket = res_text.rfind(']')
                    if first_bracket != -1 and last_bracket != -1:
                        res_text = res_text[first_bracket:last_bracket+1]
                    
                    ai_urls = json.loads(res_text)
                    cleaned_urls = []
                    platform_key = platform_name.lower()
                    for u in ai_urls:
                        if u.startswith('/'):
                            if 'jobstreet' in platform_key:
                                u = "https://id.jobstreet.com" + u
                            elif 'karirhub' in platform_key:
                                u = "https://karirhub.kemnaker.go.id" + u
                            elif 'kitalulus' in platform_key:
                                u = "https://www.kitalulus.com" + u
                            elif 'linkedin' in platform_key:
                                u = "https://www.linkedin.com" + u
                            elif 'indeed' in platform_key:
                                u = "https://id.indeed.com" + u
                            elif 'loker.id' in platform_key or 'loker' in platform_key:
                                u = "https://www.loker.id" + u
                        cleaned_urls.append(u)
                    return cleaned_urls
                elif response.status_code in [429, 503]:
                    sleep_time = 5 * (2 ** attempt) 
                    time.sleep(sleep_time)
            except Exception as e:
                logger.error(f"AI Link extraction failed: {str(e)}")
                time.sleep(1.5)

    def self_healing_google_search(self, platform_name: str, query: str) -> str:
        encoded_query = urllib.parse.quote(query)
        google_url = f"https://www.google.com/search?q={encoded_query}"
        logger.info(f"== [Self-Healing Active] == Triggering Google Search alternative for {platform_name}")
        return self.fetch_page_content(google_url, use_browser=True)

    def extract_job_details_with_ai(self, raw_text: str) -> dict:
        if not settings.GEMINI_API_KEY:
            return self._mock_fallback(raw_text, "API Key Missing")

        current_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        system_instruction =
