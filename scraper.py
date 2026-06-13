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

        # EMERGENCY FALLBACK REGEX
        logger.warning(f"AI Engine completely unavailable. Activating Regex Emergency Backup...")
        if 'linkedin' in platform_name.lower():
            platform_key = 'LinkedIn'
        elif 'jobstreet' in platform_name.lower():
            platform_key = 'JobStreet'
        elif 'indeed' in platform_name.lower():
            platform_key = 'Indeed'
        elif 'kitalulus' in platform_name.lower():
            platform_key = 'KitaLulus'
        elif 'loker' in platform_name.lower():
            platform_key = 'Loker.id'
        elif 'karirhub' in platform_name.lower() or 'kemnaker' in platform_name.lower():
            platform_key = 'Karirhub Kemnaker'
        else:
            platform_key = 'LinkedIn'
            
        fallback_regex = regex_fallbacks.get(platform_key)
        
        if fallback_regex:
            matched_urls = []
            for url_str in raw_urls_for_regex:
                if "google.com/url" in url_str or "/url?" in url_str:
                    match_clean = re.search(r'url=(https?://[^&]+)', url_str)
                    if match_clean:
                        url_str = urllib.parse.unquote(match_clean.group(1))

                if re.search(fallback_regex, url_str):
                    if url_str.startswith('/'):
                        if 'jobstreet' in platform_key.lower():
                            url_str = "https://id.jobstreet.com" + url_str
                        elif 'indeed' in platform_key.lower():
                            url_str = "https://id.indeed.com" + url_str
                        elif 'kitalulus' in platform_key.lower():
                            url_str = "https://www.kitalulus.com" + url_str
                        elif 'linkedin' in platform_key.lower():
                            url_str = "https://www.linkedin.com" + url_str
                        elif 'karirhub' in platform_key.lower():
                            url_str = "https://karirhub.kemnaker.go.id" + url_str
                        elif 'loker' in platform_key.lower():
                            url_str = "https://www.loker.id" + url_str
                    matched_urls.append(url_str)
            return list(set(matched_urls))
            
        return []

    def self_healing_google_search(self, platform_name: str, query: str) -> str:
        encoded_query = urllib.parse.quote(query)
        google_url = f"https://www.google.com/search?q={encoded_query}"
        logger.info(f"== [Self-Healing Active] == Triggering Google Search alternative for {platform_name}")
        return self.fetch_page_content(google_url, use_browser=True)

    def extract_job_details_with_ai(self, raw_text: str) -> dict:
        if not settings.GEMINI_API_KEY:
            return self._mock_fallback(raw_text, "API Key Missing")

        current_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        system_instruction = (
            "You are a strict data extraction AI. Convert webpage text into a minified JSON object without markdown formatting."
        )

        prompt = (
            "Extract job data into a valid JSON object with EXACTLY these keys:\n"
            "\"job_title\" (string), \"company_name\" (string/null), \"company_logo\" (string URL/null), \"location\" (string), "
            "\"posted_at\" (string YYYY-MM-DD or null), \"requirements\" (array of strings), "
            "\"min_age\" (integer/null), \"max_age\" (integer/null), \"province\" (string/null), "
            "\"min_salary\" (integer/null), \"max_salary\" (integer/null).\n\n"
            
            "ADDITIONAL INSTRUCTIONS FOR NEW FIELDS:\n"
            "- \"min_age\": Extract the minimum age requirement if specified in the text (e.g. 20). Otherwise null.\n"
            "- \"max_age\": Extract the maximum age requirement if specified in the text (e.g. 35). Otherwise null.\n"
            "- \"province\": Identify/infer the Indonesian province for the job's location/city. For example, if location is "
            "'Bandung', province should be 'Jawa Barat'. If location is 'Jakarta Selatan' or 'Jakarta', province should be 'DKI Jakarta'. "
            "If it cannot be mapped or is remote/work from home/outside Indonesia, set as null.\n"
            "- \"min_salary\": Extract the minimum monthly salary in Rupiah (IDR) if specified (e.g., if salary is 'Rp 3 - 4.5 Juta', set as 3000000). Otherwise null.\n"
            "- \"max_salary\": Extract the maximum monthly salary in Rupiah (IDR) if specified (e.g., if salary is 'Rp 3 - 4.5 Juta', set as 4500000; if 's.d. Rp 5.000.000', set as 5000000). Otherwise null.\n\n"
            
            f"CRITICAL: Current local time is ({current_now}). "
            "If text indicates relative times like '2 hours ago', '2 jam yang lalu', '10 mins ago', or 'baru saja', "
            "deduct it accurately from current local time. Do NOT roll back to yesterday if it does not cross midnight.\n\n"
            
            "RULES:\n"
            "1. No ```json markdown wrappers.\n"
            "2. Escape inner quotes properly.\n\n"
            f"Raw Text:\n{raw_text[:2500]}"
        )

        url, headers, payload = self._prepare_ai_request(system_instruction, prompt)

        try:
            response = self._post(url, json_data=payload, headers=headers, timeout=20.0)
            if response.status_code == 200:
                content = self._parse_ai_response(response.json()).strip()
                content_str = re.sub(r'```json\s*|\s*```', '', content)
                
                first_brace = content_str.find('{')
                last_brace = content_str.rfind('}')
                if first_brace != -1 and last_brace != -1:
                    content_str = content_str[first_brace:last_brace+1]
                
                content_str = re.sub(r'\n\s*', ' ', content_str)
                return json.loads(content_str)
            else:
                return self._mock_fallback(raw_text, f"HTTP {response.status_code}")
        except Exception as e:
            return self._mock_fallback(raw_text, str(e))

    def _mock_fallback(self, raw_text: str, reason: str) -> dict:
        lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
        title = lines[0] if lines else "Unknown Job Title"
        if len(title) > 80: title = title[:80] + "..."
        return {
            "job_title": f"{title} (AI Raw)",
            "company_name": "Under Verification",
            "company_logo": None,
            "requirements": ["Kendala ekstraksi AI.", f"Alasan: {reason}"],
            "location": "Indonesia",
            "posted_at": None,
            "min_age": None,
            "max_age": None,
            "province": None,
            "min_salary": None,
            "max_salary": None
        }

    def push_to_laravel(self, platform_name: str, job_data: dict, source_url: str) -> bool:
        # Note: Function name kept for compatibility, but now pushes directly to DB
        try:
            import psycopg2
            import json
            from datetime import datetime
            
            # Connect to DB
            conn = psycopg2.connect(
                dbname="loker",
                user="postgres",
                password="bismillah",
                host="product_database",
                port="5432"
            )
            cursor = conn.cursor()

            # conn = psycopg2.connect(
            #     dbname="jobline",
            #     user="postgres",
            #     password="P@ssw0rd",
            #     host="127.0.0.1",
            #     port="5432"
            # )
            # cursor = conn.cursor()
            
            # Pengecekan apakah URL loker sudah ada di DB
            cursor.execute("SELECT id FROM job_listings WHERE source_url = %s", (source_url,))
            if cursor.fetchone():
                import logging
                logger = logging.getLogger("hermes.scraper")
                logger.info(f"⏭️ [Skip] Job with URL {source_url} already exists in DB.")
                cursor.close()
                conn.close()
                return True # Treat as success so we don't retry unnecessarily
                
            # Dapatkan platform_id
            cursor.execute("SELECT id FROM platforms WHERE name ILIKE %s", (platform_name,))
            platform_row = cursor.fetchone()
            if not platform_row:
                import logging
                logger = logging.getLogger("hermes.scraper")
                logger.warning(f"Platform {platform_name} not found in DB. Defaulting to id 1.")
                platform_id = 1
            else:
                platform_id = platform_row[0]
                
            reqs = job_data.get("requirements", [])
            if isinstance(reqs, str):
                reqs = [reqs]
            elif not isinstance(reqs, list):
                reqs = []
                
            now = datetime.now()
            
            insert_query = """
                INSERT INTO job_listings (
                    platform_id, job_title, company_name, company_logo, 
                    requirements, source_url, location, posted_at, 
                    min_age, max_age, province, min_salary, max_salary,
                    created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, 
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s
                )
            """
            
            # Extract values, converting posted_at string to datetime if needed
            posted_at = job_data.get("posted_at")
            if posted_at:
                try:
                    posted_at = datetime.strptime(posted_at, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    try:
                        posted_at = datetime.strptime(posted_at, "%Y-%m-%d")
                    except ValueError:
                        posted_at = None
            
            cursor.execute(insert_query, (
                platform_id,
                job_data.get("job_title") or "Unknown Job",
                job_data.get("company_name"),
                job_data.get("company_logo"),
                json.dumps(reqs),
                source_url,
                job_data.get("location") or "Indonesia",
                posted_at,
                job_data.get("min_age"),
                job_data.get("max_age"),
                job_data.get("province"),
                job_data.get("min_salary"),
                job_data.get("max_salary"),
                now,
                now
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            import logging
            logger = logging.getLogger("hermes.scraper")
            logger.info(f"Successfully inserted job directly into DB: {job_data.get('job_title')}")
            return True
        except Exception as e:
            import logging
            logger = logging.getLogger("hermes.scraper")
            logger.error(f"Error pushing job directly to DB: {str(e)}")
            return False

    def scrape_platform(self, platform_name: str, max_jobs: int = 5) -> int:
        config = PLATFORM_CONFIGS.get(platform_name)
        if not config: return 0

        logger.info(f"=== Starting scrape cycle for platform: {platform_name} ===")
        search_urls = [config['search_url']]
        
        if platform_name == 'Loker.id':
            detected_locations = self.discover_loker_id_locations()
            if detected_locations:
                sampled_locations = random.sample(detected_locations, min(3, len(detected_locations)))
                search_urls.extend(sampled_locations)
                logger.info(f"🎯 Loker.id Target sebaran diperluas ke: {search_urls}")

        raw_job_urls = []
        
        for target_url in search_urls:
            logger.info(f"📡 Membaca daftar lowongan dari target URL: {target_url}")
            html = self.fetch_page_content(target_url, use_browser=config['use_browser'])
            
            if html:
                found_urls = self.extract_job_urls_with_ai(html, platform_name)
                raw_job_urls.extend(found_urls)
            
            time.sleep(random.uniform(2.0, 4.0))

        if not raw_job_urls and platform_name == 'KitaLulus':
            logger.info("Attempting KitaLulus direct URL fallback to /lowongan...")
            html = self.fetch_page_content("[https://www.kitalulus.com/lowongan](https://www.kitalulus.com/lowongan)", use_browser=config['use_browser'])
            if html:
                raw_job_urls = self.extract_job_urls_with_ai(html, platform_name)

        if not raw_job_urls:
            logger.warning(f"Main route for {platform_name} returned 0 results. Triggering self-healing...")
            fallback_html = self.self_healing_google_search(platform_name, config['fallback_query'])
            if fallback_html:
                raw_job_urls = self.extract_job_urls_with_ai(fallback_html, f"Google Search for {platform_name}")

        logger.info(f"Total raw URLs discovered by AI/Fallback: {len(raw_job_urls)}")
        
        valid_target_urls = []
        for url in raw_job_urls:
            if "google.com" in url:
                continue
                
            norm_url = self._normalize_url(url)
            
            if not self._is_valid_job_url(norm_url, platform_name):
                logger.warning(f"⏭️ [Skip URL] URL bukan halaman detail lowongan valid: {norm_url}")
                continue
            
            if norm_url in self.visited_urls:
                logger.info(f"⏭️ [Skip Awal] URL sudah pernah di-scrape sebelumnya: {norm_url}")
                continue
                
            if norm_url not in valid_target_urls:
                valid_target_urls.append(norm_url)
                
            if len(valid_target_urls) >= max_jobs:
                break

        logger.info(f"Total fresh & unique URLs ready to process: {len(valid_target_urls)}")
        successful_pushes = 0

        for index, norm_url in enumerate(valid_target_urls):
            logger.info(f"Processing job [{index+1}/{len(valid_target_urls)}]: {norm_url}")

            detail_html = self.fetch_page_content(norm_url, use_browser=config['use_browser'])
            if not detail_html:
                continue

            soup = BeautifulSoup(detail_html, 'html.parser')
            
            ignored_tags = ["script", "style", "nav", "header", "footer", "aside", "iframe"]
            if "loker.id" not in platform_name.lower():
                ignored_tags.append("form")
                
            for element in soup(ignored_tags):
                element.decompose()
            
            plain_text = soup.get_text(separator="\n")
            cleaned_text = "\n".join([line.strip() for line in plain_text.split("\n") if line.strip()])
            
            job_details = self.extract_job_details_with_ai(cleaned_text)
            
            job_title = job_details.get("job_title")
            invalid_titles = {
                "unknown job", "unknown", "unknown job title", "job title", "null",
                "job fair", "sign in", "login", "register", "cookie consent", "cookie",
                "kementerian ketenagakerjaan ri", "kemnaker", "jobstreet", "linkedin", "indeed",
                "hubungi kami", "contact us", "about us", "tentang kami", "privacy policy"
            }
            if not job_title or job_title.strip().lower() in invalid_titles:
                logger.warning(f"⚠️ [Skip Detail] Judul lowongan tidak valid ('{job_title}') untuk URL: {norm_url}")
                continue
                
            pushed = self.push_to_laravel(platform_name, job_details, norm_url)
            if pushed:
                successful_pushes += 1
                self._mark_url_processed(norm_url)
                
            time.sleep(random.uniform(2.0, 4.5))

        return successful_pushes
