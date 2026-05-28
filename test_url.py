import re
from urllib.parse import urlparse

def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
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
        from urllib.parse import parse_qs
        qs = parse_qs(parsed.query)
        if 'jk' in qs:
            return f"https://id.indeed.com/viewjob?jk={qs['jk'][0]}"
        return f"https://id.indeed.com{parsed.path}"

    elif 'karirhub' in netloc:
        match = re.search(r'/lowongan/(\d+)', parsed.path)
        if match:
            return f"https://karirhub.kemnaker.go.id/lowongan/{match.group(1)}"

    return f"{parsed.scheme}://{netloc}{parsed.path}"

urls = [
    "https://id.linkedin.com/jobs/view/brand-executive-at-kalbe-consumer-health-4414078970?position=1&pageNum=0&refId=pNfx2mawjeie16UAcjmjjA%3D%3D&trackingId=MrTSLJYRtK3SnrZkNiY76g%3D%3D",
    "https://www.linkedin.com/jobs/view/4414078970/",
    "https://id.jobstreet.com/id/job/92317360?type=standard",
    "https://karirhub.kemnaker.go.id/lowongan/12345?query=abc"
]

for u in urls:
    print(f"Original: {u}")
    print(f"Normalized: {_normalize_url(u)}")
    print("-")
