import httpx
import base64
import os
from typing import Optional

BASE_URL = "https://api.dataforseo.com/v3"
LOCATION = "United States"
LANGUAGE = "English"
MAX_RESULTS = 700  # per request cap before pagination


def _auth_headers() -> dict:
    login = os.getenv("DATAFORSEO_LOGIN", "")
    password = os.getenv("DATAFORSEO_PASSWORD", "")
    token = base64.b64encode(f"{login}:{password}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }


def _extract_items(response: dict) -> list:
    try:
        tasks = response.get("tasks", [])
        if not tasks:
            return []
        result = tasks[0].get("result") or []
        if not result:
            return []
        # keywords_for_keywords wraps items inside result[0]["items"]
        if "items" in (result[0] or {}):
            return result[0]["items"] or []
        # search_volume returns result as the flat list
        return result
    except (IndexError, KeyError, TypeError):
        return []


async def get_keywords_for_seed(seed: str, limit: int = MAX_RESULTS) -> list:
    payload = [{
        "keywords": [seed],
        "location_name": LOCATION,
        "language_name": LANGUAGE,
        "search_partners": False,
        "limit": limit,
    }]
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{BASE_URL}/keywords_data/google_ads/keywords_for_keywords/live",
            headers=_auth_headers(),
            json=payload,
        )
        r.raise_for_status()
        return _extract_items(r.json())


async def get_search_volume(keywords: list[str]) -> list:
    # DataForSEO accepts up to 1000 keywords per request
    chunks = [keywords[i:i+1000] for i in range(0, len(keywords), 1000)]
    results = []
    async with httpx.AsyncClient(timeout=90) as client:
        for chunk in chunks:
            payload = [{
                "keywords": chunk,
                "location_name": LOCATION,
                "language_name": LANGUAGE,
            }]
            r = await client.post(
                f"{BASE_URL}/keywords_data/google_ads/search_volume/live",
                headers=_auth_headers(),
                json=payload,
            )
            r.raise_for_status()
            results.extend(_extract_items(r.json()))
    return results


async def get_keywords_for_site(domain: str, limit: int = MAX_RESULTS) -> list:
    payload = [{
        "target": domain,
        "location_name": LOCATION,
        "language_name": LANGUAGE,
        "limit": limit,
    }]
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{BASE_URL}/keywords_data/google_ads/keywords_for_site/live",
            headers=_auth_headers(),
            json=payload,
        )
        r.raise_for_status()
        return _extract_items(r.json())


async def verify_credentials() -> bool:
    try:
        payload = [{"keywords": ["test"], "location_name": LOCATION, "language_name": LANGUAGE, "limit": 1}]
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{BASE_URL}/keywords_data/google_ads/search_volume/live",
                headers=_auth_headers(),
                json=payload,
            )
            return r.status_code == 200
    except Exception:
        return False
