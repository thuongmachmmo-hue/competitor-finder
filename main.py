"""
Google Ads Competitor Finder v9
- SerpAPI cho dữ liệu Google Ads
- Supabase để lưu cache deduplicate vĩnh viễn
- Cache không mất dù server restart hay ngủ
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
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

app = FastAPI(title="Google Ads Competitor Finder v9")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Supabase helpers ──
async def supabase_get_seen() -> set:
    """Lấy danh sách domain đã thấy từ Supabase"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/seen_domains?select=domain",
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {SUPABASE_KEY}",
                }
            )
            if resp.status_code == 200:
                data = resp.json()
                return set(item["domain"] for item in data)
    except Exception as e:
        print(f"Supabase get error: {e}")
    return set()

async def supabase_add_domains(domains: list):
    """Thêm domain mới vào Supabase"""
    if not domains:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            rows = [{"domain": d} for d in domains]
            await client.post(
                f"{SUPABASE_URL}/rest/v1/seen_domains",
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {SUPABASE_KEY}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=ignore-duplicates",
                },
                json=rows
            )
    except Exception as e:
        print(f"Supabase add error: {e}")

async def supabase_reset():
    """Xóa toàn bộ cache trong Supabase"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.delete(
                f"{SUPABASE_URL}/rest/v1/seen_domains?domain=neq.null",
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {SUPABASE_KEY}",
                }
            )
    except Exception as e:
        print(f"Supabase reset error: {e}")

async def supabase_create_table():
    """Tạo bảng seen_domains nếu chưa có (dùng SQL API)"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/rpc/exec_sql",
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": f"Bearer {SUPABASE_KEY}",
                    "Content-Type": "application/json",
                },
                json={"sql": """
                    CREATE TABLE IF NOT EXISTS seen_domains (
                        domain TEXT PRIMARY KEY,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    );
                """}
            )
    except:
        pass

# ── Domain helpers ──
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
        resp = await client.get("https://serpapi.com/search", params={**params, "api_key": SERPAPI_KEY})
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
    new_domains = []
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
                new_domains.append(domain)
                sitelinks = [sl.get("title","") for sl in (ad.get("sitelinks",{}).get("inline",[]) or []) if isinstance(sl,dict)]
                results.append(make_result(domain, ad.get("title",""), ad.get("description",""), ad.get("displayed_link",""), ad.get("tracking_link") or ad.get("link",""), sitelinks, ad.get("extensions",[]), "Google Ads", True, q, country.upper()))
            for org in data.get("organic_results",[])[:4]:
                domain = extract_domain(org.get("link",""))
                if not domain or domain in seen: continue
                seen.add(domain)
                new_domains.append(domain)
                results.append(make_result(domain, org.get("title",""), org.get("snippet",""), org.get("displayed_link",""), org.get("link",""), [], [], "Organic", False, q, country.upper()))
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Error [{country}] '{q}': {e}")

    # Lưu domain mới vào Supabase
    if new_domains and SUPABASE_URL:
        await supabase_add_domains(new_domains)

    return results

async def analyze_one_country(url: str, country: str, seen: set):
    results = []
    new_domains = []
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
                new_domains.append(d)
                sitelinks = [sl.get("title","") for sl in (ad.get("sitelinks",{}).get("inline",[]) or []) if isinstance(sl,dict)]
                results.append(make_result(d, ad.get("title",""), ad.get("description",""), ad.get("displayed_link",""), ad.get("tracking_link") or ad.get("link",""), sitelinks, ad.get("extensions",[]), "Google Ads", True, q, country.upper()))
            for org in data.get("organic_results",[])[:3]:
                d = extract_domain(org.get("link",""))
                if not d or d in seen: continue
                seen.add(d)
                new_domains.append(d)
                results.append(make_result(d, org.get("title",""), org.get("snippet",""), org.get("displayed_link",""), org.get("link",""), [], [], "Organic", False, q, country.upper()))
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Error [{country}] '{q}': {e}")

    if new_domains and SUPABASE_URL:
        await supabase_add_domains(new_domains)

    return results, domain_target, title_kw

# ── Startup ──
@app.on_event("startup")
async def startup():
    await supabase_create_table()

# ── Endpoints ──
@app.get("/")
async def root():
    return {"status": "online", "version": "9.0", "engine": "SerpAPI + Supabase"}

@app.get("/health")
async def health():
    seen_count = len(await supabase_get_seen()) if SUPABASE_URL else 0
    return {
        "status": "ok",
        "serpapi_configured": bool(SERPAPI_KEY),
        "supabase_configured": bool(SUPABASE_URL),
        "seen_domains_count": seen_count,
        "engine": "SerpAPI + Supabase"
    }

@app.post("/reset-seen")
async def reset_seen():
    if SUPABASE_URL:
        await supabase_reset()
    return {"status": "ok", "message": "Đã xóa toàn bộ cache trong Supabase"}

@app.get("/find-similar")
async def find_similar(
    keyword: str = Query(...),
    countries: str = Query("us"),
    reset: bool = Query(False),
):
    if not SERPAPI_KEY:
        return {"error": "Chưa có SERPAPI_KEY", "total": 0, "results": []}

    # Lấy cache từ Supabase
    seen = await supabase_get_seen() if SUPABASE_URL else set()
    if reset:
        await supabase_reset()
        seen = set()

    country_list = [c.strip().lower() for c in countries.split(",") if c.strip()] or ["us"]
    tasks = [search_one_country(keyword, c, seen, limit=20) for c in country_list]
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
    if not SERPAPI_KEY:
        return {"error": "Chưa có SERPAPI_KEY", "total": 0, "results": []}

    seen = await supabase_get_seen() if SUPABASE_URL else set()
    if reset:
        await supabase_reset()
        seen = set()

    country_list = [c.strip().lower() for c in countries.split(",") if c.strip()] or ["us"]
    tasks = [analyze_one_country(url, c, seen) for c in country_list]
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

