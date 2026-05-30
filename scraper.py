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
        self.visited_file = os.path.join(settings.DATA_DIR, "scraped_jobs.txt")
        self.visited_urls = set()
        self._load_visited_urls()

    def _normalize_url(self, url: str) -> str:
        try:
            parsed = urllib.parse.urlparse(url)
            netloc = parsed.netloc.lower()
            path_lower = parsed.path.lower()
            
            # --- FIX DUPLIKAT LINKEDIN ---
            if 'linkedin.com' in netloc:
                qs = urllib.parse.parse_qs(parsed.query)
                if 'currentJobId' in qs:
                    return f"https://www.linkedin.com/jobs/view/{qs['currentJobId'][0]}"
                
                # Ekstrak ID numerik (8-12 digit) dari path lowongan
                match_id = re.search(r'/jobs/view/.*?(\d{8,12})', path_lower)
                if match_id:
                    return f"https://www.linkedin.com/jobs/view/{match_id.group(1)}"
                
                return f"https://www.linkedin.com{parsed.path.rstrip('/')}"
                
            # --- FIX DUPLIKAT JOBSTREET ---
            elif 'jobstreet' in netloc:
                match = re.search(r'/job/(?:.*?-)?(\d+)', path_lower)
                if match:
                    return f"https://www.jobstreet.co.id/id/job/{match.group(1)}"
                return f"https://www.jobstreet.co.id{parsed.path.rstrip('/')}"
                
            # --- FIX DUPLIKAT INDEED ---
            elif 'indeed.com' in netloc:
                qs = urllib.parse.parse_qs(parsed.query)
                if 'jk' in qs:
                    return f"https://id.indeed.com/viewjob?jk={qs['jk'][0]}"
                return f"https://id.indeed.com{parsed.path.rstrip('/')}"

            # --- FIX DUPLIKAT KARIRHUB KEMNAKER ---
            elif 'karirhub' in netloc:
                # Tangkap ID lowongan berupa alphanumeric/angka di dalam path /lowongan/ID
                match = re.search(r'/lowongan/([a-zA-Z0-9-]+)', path_lower)
                if match:
                    return f"https://karirhub.kemnaker.go.id/lowongan/{match.group(1)}"
                return f"https://karirhub.kemnaker.go.id{parsed.path.rstrip('/')}"

            return f"{parsed.scheme}://{netloc}{parsed.path.rstrip('/')}"
        except Exception as e:
            logger.error(f"Error normalizing URL {url}: {str(e)}")
            return url

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
                
                # --- FIX ANTI-BANNED: Jeda Acak Manusiawi ---
                page.wait_for_timeout(random.randint(3000, 6000))
                page.evaluate(f"window.scrollBy(0, {random.randint(350, 500)})")
                page.wait_for_timeout(random.randint(2000, 4000))
                
                content = page.content()
                browser.close()
                return content
        except Exception as e:
            logger.error(f"Playwright automation failed for {url}: {str(e)}")
            return ""

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
                actual_model = response_json.get('model', 'unknown')
                logger.info(f"OpenRouter actual model used for this request: {actual_model}")
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
                                u = "[https://id.jobstreet.com](https://id.jobstreet.com)" + u
                            elif 'karirhub' in platform_key:
                                u = "[https://karirhub.kemnaker.go.id](https://karirhub.kemnaker.go.id)" + u
                        cleaned_urls.append(u)
                    return cleaned_urls
                elif response.status_code in [429, 503]:
                    sleep_time = 5 * (2 ** attempt) 
                    logger.warning(f"OpenRouter 429 (Rate Limit) untuk model {settings.GEMINI_MODEL}. Mencoba kembali dalam {sleep_time} detik...")
                    time.sleep(sleep_time)
            except Exception as e:
                logger.error(f"AI Link extraction attempt {attempt+1} failed: {str(e)}")
                time.sleep(2)

        logger.warning(f"AI Engine completely unavailable. Activating Regex Emergency Backup...")
        platform_key = 'LinkedIn' if 'linkedin' in platform_name.lower() else ('JobStreet' if 'jobstreet' in platform_name.lower() else 'Indeed')
        fallback_regex = regex_fallbacks.get(platform_key)
        
        if fallback_regex:
            matched_urls = []
            for url_str in raw_urls_for_regex:
                if "[google.com/url](https://google.com/url)" in url_str or "/url?" in url_str:
                    match_clean = re.search(r'url=(https?://[^&]+)', url_str)
                    if match_clean:
                        url_str = urllib.parse.unquote(match_clean.group(1))

                if re.search(fallback_regex, url_str):
                    if url_str.startswith('/'):
                        if 'jobstreet' in platform_key.lower():
                            url_str = "[https://id.jobstreet.com](https://id.jobstreet.com)" + url_str
                    matched_urls.append(url_str)
            return list(set(matched_urls))
            
        return []

    def self_healing_google_search(self, platform_name: str, query: str) -> str:
        encoded_query = urllib.parse.quote(query)
        google_url = f"[https://www.google.com/search?q=](https://www.google.com/search?q=){encoded_query}"
        logger.info(f"== [Self-Healing Active] == Triggering Google Search alternative for {platform_name}")
        return self.fetch_page_content(google_url, use_browser=True)

    def extract_job_details_with_ai(self, raw_text: str) -> dict:
        if not settings.GEMINI_API_KEY:
            return self._mock_fallback(raw_text, "API Key Missing")

        # --- FIX KETERANGAN WAKTU: Ambil Waktu Lokal Server Saat Ini ---
        current_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        system_instruction = (
            "You are a strict data extraction AI. Your ONLY job is to convert raw webpage text "
            "into a flawless, minified JSON object without any conversational text or markdown wrappers."
        )

        prompt = (
            "Carefully analyze the raw job posting text provided below and extract the information "
            "into a valid JSON object with EXACTLY these keys and strict formatting rules:\n\n"
            
            "- \"job_title\": (string) The exact title of the job position.\n"
            "- \"company_name\": (string or null) The official company name. Return null if not found.\n"
            "- \"company_logo\": (string URL or null) Clean absolute URL of the company logo image. Return null if not found.\n"
            "- \"location\": (string) Specific city or region (e.g., 'Jakarta', 'Bandung'). Default to 'Indonesia' if generic or not specified.\n"
            
            f"- \"posted_at\": (string or null) The estimated post date in YYYY-MM-DD format. "
            f"CRITICAL: Today's exact local time is ({current_now}). "
            f"If the webpage text indicates relative time like '2 hours ago', '2 jam yang lalu', '10 minutes ago', or 'baru saja', "
            f"deduct it accurately from the current local time provided above. Do NOT roll back to yesterday's date if the subtraction "
            f"does not cross midnight (00:00:00) of the current local time. Return null if completely unknown.\n"
            
            "- \"requirements\": (array of strings) A clean list of qualifications, skills, or job descriptions. "
            "Break down long paragraphs into short, distinct array elements. Do not leave this array empty; if no specific "
            "requirements are found, summarize the job responsibilities into 2-3 points.\n\n"
            
            "CRITICAL RULES:\n"
            "1. Do NOT wrap the output in ```json ... ``` markdown blocks.\n"
            "2. Ensure all quotes inside string values are properly escaped (\\\") to prevent invalid JSON.\n"
            "3. Respond ONLY with the raw JSON object string starting with { and ending with }.\n\n"
            f"Raw Text:\n{raw_text[:4000]}"
        )

        url, headers, payload = self._prepare_ai_request(system_instruction, prompt)

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
                
            # Tambahkan jeda napas kecil antar pemrosesan halaman detail
            time.sleep(random.uniform(1.5, 3.5))

        return successful_pushes
