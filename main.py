"""
Google Ads Competitor Finder v8
- Quay lại SerpAPI (ổn định nhất)
- Deduplicate + Multi-country + Export
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
import os
import re
from dotenv import load_dotenv

load_dotenv()
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")

app = FastAPI(title="Google Ads Competitor Finder v8")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_global_seen: set = set()

def extract_domain(url: str) -> str:
    if not url:
        return ""
    url = re.sub(r"https?://", "", url.lower().strip())
    url = re.sub(r"www\.", "", url)
    domain = url.split("/")[0].split("?")[0].strip()
    skip = ["google.com","youtube.com","facebook.com","wikipedia.org","amazon.com","bing.com","reddit.com","trustpilot.com"]
    if any(s in domain for s in skip):
        return ""
    return domain

async def serpapi_get(params: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            "https://serpapi.com/search",
            params={**params, "api_key": SERPAPI_KEY}
        )
        return resp.json()

def make_result(domain, title, desc, displayed, landing, sitelinks, extensions, source, has_ads, query, country):
    return {
        "domain": domain, "title": title, "description": desc,
        "displayed_url": displayed, "landing_page": landing,
        "sitelinks": sitelinks[:4], "extensions": extensions[:3],
        "source": source, "has_ads": has_ads, "query": query, "country": country,
        "semrush_url": f"https://www.semrush.com/analytics/overview/?q={domain}",
        "ads_spy_url": f"https://adstransparency.google.com/?query={domain}",
        "similarweb_url": f"https://www.similarweb.com/website/{domain}",
    }

async def search_one_country(keyword: str, country: str, seen: set, limit: int = 20):
    results = []
    queries = [keyword, f"best {keyword}", f"{keyword} service", f"{keyword} near me", f"top {keyword}", f"buy {keyword}"]
    for q in queries:
        if len(results) >= limit:
            break
        try:
            data = await serpapi_get({"engine": "google", "q": q, "gl": country, "hl": "en", "num": 10})
            for ad in data.get("ads", []):
                domain = extract_domain(ad.get("link","") or ad.get("displayed_link",""))
                if not domain or domain in seen: continue
                seen.add(domain)
                sitelinks = [sl.get("title","") for sl in (ad.get("sitelinks",{}).get("inline",[]) or []) if isinstance(sl,dict)]
                results.append(make_result(domain, ad.get("title",""), ad.get("description",""), ad.get("displayed_link",""), ad.get("tracking_link") or ad.get("link",""), sitelinks, ad.get("extensions",[]), "Google Ads", True, q, country.upper()))
            for org in data.get("organic_results",[])[:4]:
                domain = extract_domain(org.get("link",""))
                if not domain or domain in seen: continue
                seen.add(domain)
                results.append(make_result(domain, org.get("title",""), org.get("snippet",""), org.get("displayed_link",""), org.get("link",""), [], [], "Organic", False, q, country.upper()))
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Error [{country}] '{q}': {e}")
    return results

async def analyze_one_country(url: str, country: str, seen: set):
    results = []
    domain_target = extract_domain(url) or url.strip()
    seen.add(domain_target)
    try:
        data = await serpapi_get({"engine": "google", "q": f"site:{domain_target}", "num": 3})
        organic = data.get("organic_results", [])
        title = organic[0].get("title", domain_target) if organic else domain_target
        title_words = re.sub(r"[^\w\s]","", title).split()[:4]
        title_kw = " ".join(title_words) if title_words else domain_target
    except:
        title_kw = domain_target

    queries = [f"{domain_target} competitor", f"alternative to {domain_target}", f"sites like {domain_target}", title_kw, f"best {title_kw}", f"{domain_target} vs", f"{title_kw} review"]
    for q in queries:
        if len(results) >= 40: break
        try:
            data = await serpapi_get({"engine": "google", "q": q, "gl": country, "hl": "en", "num": 10})
            for ad in data.get("ads", []):
                d = extract_domain(ad.get("link","") or ad.get("displayed_link",""))
                if not d or d in seen: continue
                seen.add(d)
                sitelinks = [sl.get("title","") for sl in (ad.get("sitelinks",{}).get("inline",[]) or []) if isinstance(sl,dict)]
                results.append(make_result(d, ad.get("title",""), ad.get("description",""), ad.get("displayed_link",""), ad.get("tracking_link") or ad.get("link",""), sitelinks, ad.get("extensions",[]), "Google Ads", True, q, country.upper()))
            for org in data.get("organic_results",[])[:3]:
                d = extract_domain(org.get("link",""))
                if not d or d in seen: continue
                seen.add(d)
                results.append(make_result(d, org.get("title",""), org.get("snippet",""), org.get("displayed_link",""), org.get("link",""), [], [], "Organic", False, q, country.upper()))
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Error [{country}] '{q}': {e}")
    return results, domain_target, title_kw

@app.get("/")
async def root():
    return {"status": "online", "version": "8.0", "engine": "SerpAPI"}

@app.get("/health")
async def health():
    return {"status": "ok", "serpapi_configured": bool(SERPAPI_KEY), "engine": "SerpAPI"}

@app.post("/reset-seen")
async def reset_seen():
    global _global_seen
    _global_seen = set()
    return {"status": "ok", "message": "Đã xóa cache"}

@app.get("/find-similar")
async def find_similar(
    keyword: str = Query(...),
    countries: str = Query("us"),
    reset: bool = Query(False),
):
    global _global_seen
    if reset: _global_seen = set()
    if not SERPAPI_KEY:
        return {"error": "Chưa có SERPAPI_KEY", "total": 0, "results": []}
    country_list = [c.strip().lower() for c in countries.split(",") if c.strip()] or ["us"]
    tasks = [search_one_country(keyword, c, _global_seen, limit=20) for c in country_list]
    results_per_country = await asyncio.gather(*tasks)
    all_results = []
    for r in results_per_country: all_results.extend(r)
    all_results.sort(key=lambda x: (0 if x["has_ads"] else 1, x["domain"]))
    for i, r in enumerate(all_results): r["rank"] = i + 1
    ads_count = sum(1 for r in all_results if r["has_ads"])
    return {"keyword": keyword, "countries": country_list, "total": len(all_results), "ads_count": ads_count, "organic_count": len(all_results) - ads_count, "results": all_results}

@app.get("/analyze")
async def analyze(
    url: str = Query(...),
    countries: str = Query("us"),
    reset: bool = Query(False),
):
    global _global_seen
    if reset: _global_seen = set()
    if not SERPAPI_KEY:
        return {"error": "Chưa có SERPAPI_KEY", "total": 0, "results": []}
    country_list = [c.strip().lower() for c in countries.split(",") if c.strip()] or ["us"]
    tasks = [analyze_one_country(url, c, _global_seen) for c in country_list]
    results_per_country = await asyncio.gather(*tasks)
    all_results = []
    website_info = {}
    for results, domain_target, title_kw in results_per_country:
        all_results.extend(results)
        if not website_info: website_info = {"domain": domain_target, "title": title_kw, "description": ""}
    all_results.sort(key=lambda x: (0 if x["has_ads"] else 1, x["domain"]))
    for i, r in enumerate(all_results): r["rank"] = i + 1
    ads_count = sum(1 for r in all_results if r["has_ads"])
    return {"url": url, "countries": country_list, "website_info": website_info, "total": len(all_results), "ads_count": ads_count, "organic_count": len(all_results) - ads_count, "results": all_results}

