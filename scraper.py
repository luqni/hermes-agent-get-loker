import re
import json
import logging
import time
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
        'search_url': "https://www.linkedin.com/jobs/search?location=Indonesia&geoId=102478259&f_TPR=r604800",
        'fallback_query': "site:linkedin.com/jobs/view/ \"Indonesia\" \"loker\"",
        'use_browser': True
    },
    # 'JobStreet': {
    #     'search_url': "https://id.jobstreet.com/id/jobs?daterange=7",
    #     'fallback_query': "site:id.jobstreet.com/id/job/ \"loker terbaru\"",
    #     'use_browser': True
    # },
    # 'Indeed': {
    #     'search_url': "https://id.indeed.com/jobs?q=dibutuhkan+segera&l=Indonesia",
    #     'fallback_query': "site:id.indeed.com/viewjob/ OR site:id.indeed.com/rc/clk",
    #     'use_browser': True
    # },
    # 'Karir.com': {
    #     'search_url': "https://www.karir.com/search",
    #     'fallback_query': "site:karir.com/opportunities/",
    #     'use_browser': False
    # },
    # 'Loker.id': {
    #     'search_url': "https://www.loker.id/cari-lowongan-kerja",
    #     'fallback_query': "site:loker.id/lowongan/",
    #     'use_browser': False
    # },
    'Karirhub Kemnaker': {
        'search_url': "https://karirhub.kemnaker.go.id/",
        'fallback_query': "site:karirhub.kemnaker.go.id/lowongan/",
        'use_browser': True
    }
}

class HermesScraper:
    def __init__(self):
        logger.info(f"HermesScraper initialized. AI Provider: {settings.AI_PROVIDER}, Configured Model: {settings.GEMINI_MODEL}")
        self.client = httpx.Client(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
            timeout=30.0
        )
        import os
        self.visited_file = os.path.join(settings.DATA_DIR, "scraped_jobs.txt")
        self.visited_urls = set()
        self._load_visited_urls()

    def _normalize_url(self, url: str) -> str:
        import urllib.parse
        import re
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc.lower()
        
        if 'linkedin.com' in netloc:
            match = re.search(r'/jobs/view/(?:.*?-)?(\d+)', parsed.path)
            if match:
                return f"https://www.linkedin.com/jobs/view/{match.group(1)}"
            return f"https://www.linkedin.com{parsed.path}"
            
        elif 'jobstreet' in netloc:
            match = re.search(r'/job/(?:.*?-)?(\d+)', parsed.path)
            if match:
                return f"https://www.jobstreet.co.id/id/job/{match.group(1)}"
            return f"https://www.jobstreet.co.id{parsed.path}"
            
        elif 'indeed.com' in netloc:
            qs = urllib.parse.parse_qs(parsed.query)
            if 'jk' in qs:
                return f"https://id.indeed.com/viewjob?jk={qs['jk'][0]}"
            return f"https://id.indeed.com{parsed.path}"

        elif 'karirhub' in netloc:
            match = re.search(r'/lowongan/(\d+)', parsed.path)
            if match:
                return f"https://karirhub.kemnaker.go.id/lowongan/{match.group(1)}"

        return f"{parsed.scheme}://{netloc}{parsed.path}"

    def _load_visited_urls(self):
        import os
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
                response = self.client.get(url)
                if response.status_code == 200:
                    return response.text
            except Exception as e:
                logger.error(f"Error in standard fetch for {url}: {str(e)}")

        logger.info(f"Using advanced browser automation to load: {url}")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    ignore_https_errors=True,
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 768}
                )
                page = context.new_page()
                if stealth_sync:
                    stealth_sync(page)
                    
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(4000)
                page.evaluate("window.scrollBy(0, 400)")
                page.wait_for_timeout(2000)
                
                content = page.content()
                browser.close()
                return content
        except Exception as e:
            logger.error(f"Playwright automation failed for {url}: {str(e)}")
            return ""

    def _prepare_ai_request(self, system_instruction: str, prompt_text: str) -> tuple:
        """Helper to dynamically format URL, headers, and payload based on AI_PROVIDER."""
        provider = settings.AI_PROVIDER.lower()
        model = settings.GEMINI_MODEL
        key = settings.GEMINI_API_KEY

        if provider == "openrouter":
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                # FIX HEADERS: Gunakan domain publik standar agar OpenRouter tidak memblokir localhost
                "HTTP-Referer": "https://hermes-agent.internal", 
                "X-Title": "Hermes Autonomous Job Scraper Bot"
            }
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt_text}
                ],
                # Beberapa model free-tier sensitif terhadap response_format, 
                # kita biarkan prompt natural yang memaksa bentuk JSON agar lebih aman
            }
        else: # Default: gemini native
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [{"parts": [{"text": f"{system_instruction}\n\n{prompt_text}"}]}]
            }
            if "gemma" not in model.lower():
                payload["generationConfig"] = {"responseMimeType": "application/json"}
                
        return url, headers, payload

    def _parse_ai_response(self, response_json: dict) -> str:
        """Helper to dynamically extract raw text content from OpenRouter or Gemini response."""
        try:
            if settings.AI_PROVIDER.lower() == "openrouter":
                actual_model = response_json.get('model', 'unknown')
                logger.info(f"OpenRouter actual model used for this request: {actual_model}")
                return response_json['choices'][0]['message']['content']
            else:
                return response_json['candidates'][0]['content']['parts'][0]['text']
        except Exception as e:
            logger.error(f"Failed parsing response structure: {str(e)}")
            return ""

    def extract_job_urls_with_ai(self, html_content: str, platform_name: str) -> list:
        """[CONFIGURABLE] Extracts job URLs using the selected AI Provider or switches to backup."""
        regex_fallbacks = {
            'LinkedIn': r'linkedin\.com/jobs/view/[0-9]+',
            'JobStreet': r'(?:jobstreet\.(?:com|co\.id))?/[^"\'\s<>]+?/job/[0-9]+',
            'Indeed': r'(?:indeed\.com)?/(?:rc/clk|viewjob)\?[^"\'\s<>]+'
        }

        soup = BeautifulSoup(html_content, 'html.parser')
        links = []
        raw_urls_for_regex = []

        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(strip=True)
            if len(href) > 10:
                links.append({"text": text[:50], "url": href})
                raw_urls_for_regex.append(href)

        if not links:
            logger.warning(f"No raw links found in HTML for {platform_name}. Possibly blocked by Cloudflare or CAPTCHA.")
            return []
        if not settings.GEMINI_API_KEY:
            return []

        logger.info(f"Found {len(links)} raw links on {platform_name}. Sending to AI for filtering...")
        
        system_instruction = "You are an expert web scraper assistant. You must output a JSON array of strings."
        prompt = (
            f"Analyze this list of links crawled from {platform_name}.\n"
            f"Filter and return ONLY valid, direct job detail page URLs.\n"
            f"Links data:\n{json.dumps(links[:120])}\n\n"
            f"Return a clean JSON array of strings containing ONLY the URLs. No markdown wrapper like ```json."
        )

        url, headers, payload = self._prepare_ai_request(system_instruction, prompt)

        # Retry loop for stability
        for attempt in range(3):
            try:
                response = self.client.post(url, json=payload, headers=headers, timeout=30.0)
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
                            # elif 'indeed' in platform_key:
                            #     u = "https://id.indeed.com" + u
                            # elif 'linkedin' in platform_key:
                            #     u = "https://www.linkedin.com" + u
                            # elif 'karir.com' in platform_key:
                            #     u = "https://www.karir.com" + u
                            # elif 'loker.id' in platform_key:
                            #     u = "https://www.loker.id" + u
                            elif 'karirhub' in platform_key:
                                u = "https://karirhub.kemnaker.go.id" + u
                        cleaned_urls.append(u)
                    return cleaned_urls
                elif response.status_code in [429, 503]:
                    # Perbaikan: Menghapus variabel 'index' yang tidak ada di fungsi ini
                    # Percobaan 1 = 5s, Percobaan 2 = 10s, Percobaan 3 = 20s, Percobaan 4 = 40s
                    sleep_time = 5 * (2 ** attempt) 
                    logger.warning(f"OpenRouter 429 (Rate Limit) untuk model {settings.GEMINI_MODEL}. Mencoba kembali dalam {sleep_time} detik...")
                    time.sleep(sleep_time)
            except Exception as e:
                logger.error(f"AI Link extraction attempt {attempt+1} failed: {str(e)}")
                time.sleep(2)

        # EMERGENCY FALLBACK REGEX
        logger.warning(f"AI Engine completely unavailable. Activating Regex Emergency Backup...")
        platform_key = 'LinkedIn' if 'linkedin' in platform_name.lower() else ('JobStreet' if 'jobstreet' in platform_name.lower() else 'Indeed')
        fallback_regex = regex_fallbacks.get(platform_key)
        
        if fallback_regex:
            matched_urls = []
            for url_str in raw_urls_for_regex:
                if "google.com/url" in url_str or "/url?" in url_str:
                    match_clean = re.search(r'url=(https?://[^&]+)', url_str)
                    if match_clean:
                        import urllib.parse
                        url_str = urllib.parse.unquote(match_clean.group(1))

                if re.search(fallback_regex, url_str):
                    if url_str.startswith('/'):
                        if 'jobstreet' in platform_key.lower():
                            url_str = "https://id.jobstreet.com" + url_str
                        elif 'indeed' in platform_key.lower():
                            url_str = "https://id.indeed.com" + url_str
                    matched_urls.append(url_str)
            return list(set(matched_urls))
            
        return []

    def self_healing_google_search(self, platform_name: str, query: str) -> str:
        """[SISTEM SELF-HEALING] Jika platform utama terblokir, cari memutar lewat Google."""
        import urllib.parse
        
        # Meng-encode karakter spasi dan tanda kutip secara aman agar tidak merusak URL
        encoded_query = urllib.parse.quote(query)
        google_url = f"https://www.google.com/search?q={encoded_query}"
        
        logger.info(f"== [Self-Healing Active] == Triggering Google Search alternative for {platform_name}")
        return self.fetch_page_content(google_url, use_browser=True)

    def extract_job_details_with_ai(self, raw_text: str) -> dict:
        """[CONFIGURABLE] Parses job text into clean JSON format using selected Provider."""
        if not settings.GEMINI_API_KEY:
            return self._mock_fallback(raw_text, "API Key Missing")

        system_instruction = "You are a JSON job data extractor. You must only output a valid JSON object."
        prompt = (
            "Extract the following job posting into a valid JSON object with EXACTLY these keys: "
            "\"job_title\" (string), \"company_name\" (string, use null if not found), "
            "\"company_logo\" (string URL, use null if not found), "
            "\"requirements\" (array of strings), "
            "\"location\" (string, default to Indonesia if not specified), "
            "\"posted_at\" (string, extract the date or time the job was posted, return null if not found). "
            "CRITICAL: Do not wrap response in markdown blocks. Return raw JSON string only."
        )
        prompt_text = f"{prompt}\n\nRaw Text:\n{raw_text[:4000]}"

        url, headers, payload = self._prepare_ai_request(system_instruction, prompt_text)

        try:
            response = self.client.post(url, json=payload, headers=headers, timeout=20.0)
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
                logger.error(f"AI API returned error status {response.status_code}")
                return self._mock_fallback(raw_text, f"HTTP Error {response.status_code}")
        except Exception as e:
            logger.error(f"Exception during AI parsing: {str(e)}")
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
            "posted_at": None
        }

    def push_to_laravel(self, platform_name: str, job_data: dict, source_url: str) -> bool:
        webhook_url = f"{settings.LARAVEL_API_URL}/webhooks/jobs"
        headers = {"X-Hermes-Token": settings.HERMES_WEBHOOK_TOKEN, "Content-Type": "application/json"}
        
        reqs = job_data.get("requirements", [])
        if isinstance(reqs, str):
            reqs = [reqs]
        elif not isinstance(reqs, list):
            reqs = []
            
        payload = {
            "platform_name": platform_name,
            "job_title": job_data.get("job_title") or "Unknown Job",
            "company_name": job_data.get("company_name"),
            "company_logo": job_data.get("company_logo"),
            "requirements": reqs,
            "source_url": source_url,
            "location": job_data.get("location") or "Indonesia",
            "posted_at": job_data.get("posted_at")
        }
        try:
            response = self.client.post(webhook_url, json=payload, headers=headers)
            return response.status_code in [200, 201]
        except Exception as e:
            logger.error(f"Error pushing job to Laravel: {str(e)}")
            return False

    def scrape_platform(self, platform_name: str, max_jobs: int = 5) -> int:
        config = PLATFORM_CONFIGS.get(platform_name)
        if not config: return 0

        logger.info(f"=== Starting scrape cycle for platform: {platform_name} ===")
        html = self.fetch_page_content(config['search_url'], use_browser=config['use_browser'])
        
        job_urls = []
        if html:
            job_urls = self.extract_job_urls_with_ai(html, platform_name)

        if not job_urls:
            logger.warning(f"Main route for {platform_name} returned 0 results. Triggering self-healing...")
            fallback_html = self.self_healing_google_search(platform_name, config['fallback_query'])
            if fallback_html:
                job_urls = self.extract_job_urls_with_ai(fallback_html, f"Google Search for {platform_name}")

        logger.info(f"Total functional job URLs discovered: {len(job_urls)}")
        
        target_urls = job_urls[:max_jobs]
        successful_pushes = 0

        for index, url in enumerate(target_urls):
            logger.info(f"Processing job [{index+1}/{len(target_urls)}]: {url}")
            if "google.com" in url: continue
            
            norm_url = self._normalize_url(url)
            if norm_url in self.visited_urls:
                logger.info(f"⏭️ Skipping already processed job: {norm_url}")
                continue

            detail_html = self.fetch_page_content(url, use_browser=config['use_browser'])
            if not detail_html: continue

            soup = BeautifulSoup(detail_html, 'html.parser')
            for element in soup(["script", "style", "nav", "header", "footer", "form"]):
                element.decompose()
            
            plain_text = soup.get_text(separator="\n")
            cleaned_text = "\n".join([line.strip() for line in plain_text.split("\n") if line.strip()])
            
            job_details = self.extract_job_details_with_ai(cleaned_text)
            pushed = self.push_to_laravel(platform_name, job_details, norm_url)
            if pushed: 
                successful_pushes += 1
                self._mark_url_processed(norm_url)

        return successful_pushes