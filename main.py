"""
Google Ads Competitor Finder v5
Chức năng 1: Tìm dự án tương tự theo từ khóa/ngành
Chức năng 2: Phân tích đối thủ đang chạy Ads cùng ngành với 1 website
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

app = FastAPI(title="Google Ads Competitor Finder v5")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


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


async def fetch_website_info(url: str) -> dict:
    """Lấy thông tin cơ bản của website để hiểu ngành"""
    try:
        domain = extract_domain(url) or url
        # Dùng SerpAPI để lấy thông tin website
        data = await serpapi_get({
            "engine": "google",
            "q": f"site:{domain}",
            "num": 5,
        })
        organic = data.get("organic_results", [])
        title = organic[0].get("title", domain) if organic else domain
        snippet = organic[0].get("snippet", "") if organic else ""

        # Lấy knowledge graph nếu có
        kg = data.get("knowledge_graph", {})
        description = kg.get("description", snippet)

        return {
            "domain": domain,
            "title": title,
            "description": description,
            "category": kg.get("type", ""),
        }
    except Exception as e:
        return {"domain": url, "title": url, "description": "", "category": ""}


async def find_similar_projects(keyword: str, country: str = "us"):
    """Chức năng 1: Tìm dự án tương tự theo từ khóa"""
    results = []
    seen = set()

    queries = [
        keyword,
        f"{keyword} website",
        f"best {keyword} platform",
        f"top {keyword} companies",
        f"{keyword} alternative",
        f"{keyword} review",
    ]

    for q in queries:
        if len(results) >= 30:
            break
        try:
            data = await serpapi_get({
                "engine": "google",
                "q": q,
                "gl": country,
                "hl": "en",
                "num": 10,
            })

            # Lấy Ads trước
            for ad in data.get("ads", []):
                domain = extract_domain(ad.get("link","") or ad.get("displayed_link",""))
                if not domain or domain in seen:
                    continue
                seen.add(domain)
                sitelinks = [sl.get("title","") for sl in (ad.get("sitelinks",{}).get("inline",[]) or []) if isinstance(sl,dict)]
                results.append({
                    "rank": len(results)+1,
                    "domain": domain,
                    "title": ad.get("title",""),
                    "description": ad.get("description",""),
                    "displayed_url": ad.get("displayed_link",""),
                    "landing_page": ad.get("tracking_link") or ad.get("link",""),
                    "sitelinks": sitelinks[:4],
                    "extensions": ad.get("extensions",[])[:3],
                    "source": "Google Ads",
                    "has_ads": True,
                    "query": q,
                    "semrush_url": f"https://www.semrush.com/analytics/overview/?q={domain}",
                    "ads_spy_url": f"https://adstransparency.google.com/?query={domain}",
                    "similarweb_url": f"https://www.similarweb.com/website/{domain}",
                })

            # Organic
            for org in data.get("organic_results", [])[:4]:
                domain = extract_domain(org.get("link",""))
                if not domain or domain in seen:
                    continue
                seen.add(domain)
                results.append({
                    "rank": len(results)+1,
                    "domain": domain,
                    "title": org.get("title",""),
                    "description": org.get("snippet",""),
                    "displayed_url": org.get("displayed_link",""),
                    "landing_page": org.get("link",""),
                    "sitelinks": [],
                    "extensions": [],
                    "source": "Organic",
                    "has_ads": False,
                    "query": q,
                    "semrush_url": f"https://www.semrush.com/analytics/overview/?q={domain}",
                    "ads_spy_url": f"https://adstransparency.google.com/?query={domain}",
                    "similarweb_url": f"https://www.similarweb.com/website/{domain}",
                })

            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Error: {e}")

    results.sort(key=lambda x: (0 if x["has_ads"] else 1, x["rank"]))
    for i, r in enumerate(results):
        r["rank"] = i+1
    return results[:50]


async def analyze_competitors(website_url: str, country: str = "us"):
    """Chức năng 2: Phân tích đối thủ đang chạy Ads cùng ngành với 1 website"""
    results = []
    seen = set()

    # Bước 1: Lấy thông tin website
    info = await fetch_website_info(website_url)
    domain = info["domain"]
    seen.add(domain)

    # Bước 2: Tạo queries dựa trên domain và title
    title_words = re.sub(r"[^\w\s]", "", info["title"]).split()[:4]
    title_kw = " ".join(title_words) if title_words else domain

    queries = [
        f"{domain} competitor",
        f"alternative to {domain}",
        f"sites like {domain}",
        f"{title_kw}",
        f"best {title_kw}",
        f"{title_kw} vs",
        f"{domain} vs",
    ]

    # Bước 3: Tìm đối thủ
    for q in queries:
        if len(results) >= 40:
            break
        try:
            data = await serpapi_get({
                "engine": "google",
                "q": q,
                "gl": country,
                "hl": "en",
                "num": 10,
            })

            for ad in data.get("ads", []):
                d = extract_domain(ad.get("link","") or ad.get("displayed_link",""))
                if not d or d in seen:
                    continue
                seen.add(d)
                sitelinks = [sl.get("title","") for sl in (ad.get("sitelinks",{}).get("inline",[]) or []) if isinstance(sl,dict)]
                results.append({
                    "rank": len(results)+1,
                    "domain": d,
                    "title": ad.get("title",""),
                    "description": ad.get("description",""),
                    "displayed_url": ad.get("displayed_link",""),
                    "landing_page": ad.get("tracking_link") or ad.get("link",""),
                    "sitelinks": sitelinks[:4],
                    "extensions": ad.get("extensions",[])[:3],
                    "source": "Google Ads",
                    "has_ads": True,
                    "query": q,
                    "semrush_url": f"https://www.semrush.com/analytics/overview/?q={d}",
                    "ads_spy_url": f"https://adstransparency.google.com/?query={d}",
                    "similarweb_url": f"https://www.similarweb.com/website/{d}",
                })

            for org in data.get("organic_results", [])[:3]:
                d = extract_domain(org.get("link",""))
                if not d or d in seen:
                    continue
                seen.add(d)
                results.append({
                    "rank": len(results)+1,
                    "domain": d,
                    "title": org.get("title",""),
                    "description": org.get("snippet",""),
                    "displayed_url": org.get("displayed_link",""),
                    "landing_page": org.get("link",""),
                    "sitelinks": [],
                    "extensions": [],
                    "source": "Organic",
                    "has_ads": False,
                    "query": q,
                    "semrush_url": f"https://www.semrush.com/analytics/overview/?q={d}",
                    "ads_spy_url": f"https://adstransparency.google.com/?query={d}",
                    "similarweb_url": f"https://www.similarweb.com/website/{d}",
                })

            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Error: {e}")

    results.sort(key=lambda x: (0 if x["has_ads"] else 1, x["rank"]))
    for i, r in enumerate(results):
        r["rank"] = i+1

    return {"website_info": info, "results": results[:50]}


# ── Endpoints ──

@app.get("/")
async def root():
    return {"status": "online", "version": "5.0"}

@app.get("/health")
async def health():
    return {"status": "ok", "serpapi_configured": bool(SERPAPI_KEY)}

@app.get("/find-similar")
async def find_similar(
    keyword: str = Query(..., description="Từ khóa hoặc tên ngành"),
    country: str = Query("us"),
):
    """Chức năng 1: Tìm dự án tương tự"""
    if not SERPAPI_KEY:
        return {"error": "Chưa có SERPAPI_KEY", "total": 0, "results": []}
    results = await find_similar_projects(keyword, country)
    ads_count = sum(1 for r in results if r["has_ads"])
    return {
        "keyword": keyword,
        "total": len(results),
        "ads_count": ads_count,
        "organic_count": len(results) - ads_count,
        "results": results,
    }

@app.get("/analyze")
async def analyze(
    url: str = Query(..., description="URL hoặc domain của website cần phân tích"),
    country: str = Query("us"),
):
    """Chức năng 2: Phân tích đối thủ của 1 website"""
    if not SERPAPI_KEY:
        return {"error": "Chưa có SERPAPI_KEY", "total": 0, "results": []}
    data = await analyze_competitors(url, country)
    results = data["results"]
    ads_count = sum(1 for r in results if r["has_ads"])
    return {
        "url": url,
        "website_info": data["website_info"],
        "total": len(results),
        "ads_count": ads_count,
        "organic_count": len(results) - ads_count,
        "results": results,
    }

