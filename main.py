import time
import logging
import psutil
from fastapi import FastAPI, BackgroundTasks, HTTPException, Header
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
import httpx

from config import settings
from scraper import HermesScraper, PLATFORM_CONFIGS

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("hermes.main")

app = FastAPI(
    title="Hermes Agent - Autonomous Scraper",
    description="Python/FastAPI Agentic Scraper for Loker Merah Putih",
    version="1.0.0"
)

scraper = HermesScraper()
scheduler = BackgroundScheduler()

# Global statistics tracker
stats = {
    "total_scraped_today": 0,
    "last_scraped_platform": "None",
    "last_scrape_time": "Never"
}

class ScrapeRequest(BaseModel):
    platform_name: str
    max_jobs: int = 5

def send_heartbeat():
    """Sends active resource stats heartbeat ping directly to Laravel API."""
    logger.info("Sending heartbeat ping to Laravel...")
    url = f"{settings.LARAVEL_API_URL}/webhooks/hermes-heartbeat"
    headers = {
        "X-Hermes-Token": settings.HERMES_WEBHOOK_TOKEN,
        "Content-Type": "application/json"
    }
    
    # Read host system resource usage safely
    try:
        cpu_usage = f"{psutil.cpu_percent()}%"
        ram_usage = f"{psutil.virtual_memory().percent}%"
    except Exception:
        cpu_usage = "0%"
        ram_usage = "0%"

    payload = {
        "cpu_usage": cpu_usage,
        "ram_usage": ram_usage,
        "scraped_today": stats["total_scraped_today"],
        "status": "running"
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                logger.info("Heartbeat accepted by Laravel successfully.")
            else:
                logger.error(f"Laravel rejected heartbeat. Status: {response.status_code}, Body: {response.text}")
    except Exception as e:
        logger.error(f"Failed to transmit heartbeat to Laravel: {str(e)}")

# Platform names list for round-robin cycle
PLATFORMS_LIST = list(PLATFORM_CONFIGS.keys())
last_scraped_index = -1

def trigger_scheduled_scrape():
    """Triggered by APScheduler to run a round-robin scrape cycle on one platform at a time."""
    global last_scraped_index
    if not PLATFORMS_LIST:
        return

    # Select next platform in round-robin sequence
    last_scraped_index = (last_scraped_index + 1) % len(PLATFORMS_LIST)
    platform_name = PLATFORMS_LIST[last_scraped_index]
    
    logger.info(f"[Scheduler] Initiating scheduled round-robin scrape for platform: {platform_name}")
    try:
        count = scraper.scrape_platform(platform_name, max_jobs=3)
        stats["total_scraped_today"] += count
        stats["last_scraped_platform"] = platform_name
        stats["last_scrape_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        logger.error(f"[Scheduler] Scraping failed for {platform_name}: {str(e)}")
    
    # Send an updated heartbeat immediately after a scraping run
    send_heartbeat()

# Register scheduled background tasks
scheduler.add_job(send_heartbeat, 'interval', minutes=5, id='heartbeat_job')
scheduler.add_job(trigger_scheduled_scrape, 'interval', minutes=settings.SCRAPE_INTERVAL_MINUTES, id='scrape_job')

@app.on_event("startup")
def startup_event():
    logger.info("Starting up Hermes Agent background scheduler...")
    scheduler.start()
    # Trigger first heartbeat immediately on boot
    send_heartbeat()

@app.on_event("shutdown")
def shutdown_event():
    logger.info("Shutting down Hermes Agent background scheduler...")
    scheduler.shutdown()

@app.get("/health")
def get_health():
    """Checks overall health status and retrieves resource usage metrics."""
    return {
        "status": "healthy",
        "cpu_usage": f"{psutil.cpu_percent()}%",
        "ram_usage": f"{psutil.virtual_memory().percent}%",
        "scheduler_running": scheduler.running,
        "statistics": stats
    }

@app.post("/api/scrape")
def trigger_scrape(req: ScrapeRequest, background_tasks: BackgroundTasks, x_hermes_token: str = Header(None)):
    """API endpoint allowing Laravel Admin Dashboard to trigger an on-demand scraping run."""
    # Simple token validation
    if not x_hermes_token or x_hermes_token != settings.HERMES_WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if req.platform_name not in PLATFORM_CONFIGS:
        raise HTTPException(status_code=400, detail=f"Invalid platform. Supported: {PLATFORMS_LIST}")

    def run_scrape():
        logger.info(f"[On-Demand] Starting manual scrape for {req.platform_name}")
        try:
            count = scraper.scrape_platform(req.platform_name, max_jobs=req.max_jobs)
            stats["total_scraped_today"] += count
            stats["last_scraped_platform"] = req.platform_name
            stats["last_scrape_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
            send_heartbeat()
        except Exception as e:
            logger.error(f"[On-Demand] Manual scrape failed: {str(e)}")

    background_tasks.add_task(run_scrape)
    return {"message": f"Scrape task for {req.platform_name} successfully enqueued in background."}
