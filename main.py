import os
import csv
import io
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database as db
import dataforseo as dfs
from classifier import enrich_keywords, normalize_scores, is_ssdi_relevant


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="SSDIHunter", lifespan=lifespan)


# ─── Request models ────────────────────────────────────────────────────────────

class ResearchRequest(BaseModel):
    seed: str
    min_volume: int = 50
    max_competition: int = 100
    intent_filter: Optional[str] = None  # None = all

class GapRequest(BaseModel):
    seed: str
    min_volume: int = 50
    max_competition: int = 40  # tight competition ceiling for gaps

class LongTailRequest(BaseModel):
    seed: str
    min_words: int = 3
    min_volume: int = 10
    max_competition: int = 100

class SaveRequest(BaseModel):
    list_name: str
    keywords: list[dict]

class AddToListRequest(BaseModel):
    list_id: int
    keywords: list[dict]


# ─── Seed expansion ────────────────────────────────────────────────────────────
# Google Ads suppresses broad "disability" as a sensitive category.
# These expansion maps bypass that by fanning out to specific seeds that work,
# then aggregating and deduplicating the combined results.

SEED_EXPANSIONS = {
    "disability": [
        "ssdi", "ssi", "social security disability", "disability claim",
        "disability benefits", "disability appeal", "disability attorney",
        "disability application", "disability eligibility", "disability denial",
    ],
    "disabled": [
        "ssdi", "ssi", "social security disability", "disability benefits",
        "disability claim", "disabled benefits",
    ],
    "social security": [
        "ssdi", "ssi", "social security disability", "social security benefits",
        "social security appeal", "social security claim",
    ],
    "benefits": [
        "ssdi benefits", "ssi benefits", "disability benefits",
        "social security disability benefits",
    ],
}

def expand_seed(seed: str) -> list[str]:
    """Return list of seeds to query. Expands suppressed broad terms."""
    normalized = seed.lower().strip()
    return SEED_EXPANSIONS.get(normalized, [normalized])


# ─── Helpers ───────────────────────────────────────────────────────────────────

def apply_filters(keywords: list, min_volume: int, max_competition: int, intent_filter: str = None) -> list:
    out = [k for k in keywords
           if k["search_volume"] >= min_volume
           and k["competition_index"] <= max_competition]
    if intent_filter and intent_filter != "All":
        out = [k for k in out if k["intent"] == intent_filter]
    return out


async def fetch_and_enrich(seed: str, mode: str) -> list:
    cache_hash = db.cache_key(mode, {"seed": seed.lower().strip()})
    cached = db.get_cached(cache_hash)
    if cached is not None and len(cached) > 0:
        return cached

    seeds = expand_seed(seed)
    errors = []

    async def fetch_one(s: str) -> list:
        try:
            return await dfs.get_keywords_for_seed(s)
        except Exception as e:
            errors.append(f"{s}: {e}")
            return []

    results_per_seed = await asyncio.gather(*[fetch_one(s) for s in seeds])

    all_raw = []
    seen_keywords = set()
    for raw in results_per_seed:
        for item in raw:
            kw = (item.get("keyword") or "").strip().lower()
            if kw and kw not in seen_keywords:
                seen_keywords.add(kw)
                all_raw.append(item)

    if not all_raw and errors:
        raise Exception("DataForSEO API errors: " + " | ".join(errors))

    enriched = enrich_keywords(all_raw)
    enriched = normalize_scores(enriched)
    enriched.sort(key=lambda x: x["opportunity_score"], reverse=True)

    if enriched:
        db.set_cache(cache_hash, seed, enriched)
    return enriched


# ─── API routes ────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    creds_ok = await dfs.verify_credentials()
    return {"status": "ok", "dataforseo": creds_ok}


@app.post("/api/research")
async def research(req: ResearchRequest):
    try:
        keywords = await fetch_and_enrich(req.seed, "research")
        filtered = apply_filters(keywords, req.min_volume, req.max_competition, req.intent_filter)
        db.add_history(req.seed, "Research", len(filtered))
        return {"keywords": filtered, "total": len(filtered)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/gaps")
async def gaps(req: GapRequest):
    try:
        keywords = await fetch_and_enrich(req.seed, "gaps")
        filtered = apply_filters(keywords, req.min_volume, req.max_competition)
        filtered = [k for k in filtered if k["cpc"] >= 1.0]
        filtered.sort(key=lambda x: x["opportunity_score"], reverse=True)
        db.add_history(req.seed, "Gap Finder", len(filtered))
        return {"keywords": filtered, "total": len(filtered)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/longtail")
async def longtail(req: LongTailRequest):
    try:
        keywords = await fetch_and_enrich(req.seed, "longtail")
        filtered = apply_filters(keywords, req.min_volume, req.max_competition)
        filtered = [k for k in filtered if len(k["keyword"].split()) >= req.min_words]
        for k in filtered:
            k["longtail_score"] = round(len(k["keyword"].split()) * k["opportunity_score"], 1)
        filtered.sort(key=lambda x: x["longtail_score"], reverse=True)
        db.add_history(req.seed, "Long-tail", len(filtered))
        return {"keywords": filtered, "total": len(filtered)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/competitor")
async def competitor(domain: str = Query(..., description="Competitor domain e.g. allsup.com")):
    cache_hash = db.cache_key("competitor", {"domain": domain.lower().strip()})
    cached = db.get_cached(cache_hash)
    if cached is not None:
        db.add_history(domain, "Competitor", len(cached))
        return {"keywords": cached, "total": len(cached)}

    raw = await dfs.get_keywords_for_site(domain)
    enriched = enrich_keywords(raw)
    enriched = normalize_scores(enriched)
    enriched.sort(key=lambda x: x["opportunity_score"], reverse=True)
    db.set_cache(cache_hash, domain, enriched)
    db.add_history(domain, "Competitor", len(enriched))
    return {"keywords": enriched, "total": len(enriched)}


@app.get("/api/history")
async def history():
    return db.get_history()


@app.get("/api/lists")
async def get_lists():
    return db.get_lists()


@app.get("/api/lists/{list_id}")
async def get_list(list_id: int):
    keywords = db.get_list_keywords(list_id)
    return {"keywords": keywords}


@app.post("/api/lists")
async def create_list(req: SaveRequest):
    list_id = db.create_list(req.list_name)
    db.add_keywords_to_list(list_id, req.keywords)
    return {"id": list_id, "name": req.list_name}


@app.post("/api/lists/{list_id}/keywords")
async def add_to_list(list_id: int, req: AddToListRequest):
    db.add_keywords_to_list(list_id, req.keywords)
    return {"ok": True}


@app.delete("/api/lists/{list_id}")
async def delete_list(list_id: int):
    db.delete_list(list_id)
    return {"ok": True}


@app.delete("/api/lists/keywords/{keyword_id}")
async def delete_keyword(keyword_id: int):
    db.delete_list_keyword(keyword_id)
    return {"ok": True}


@app.get("/api/lists/{list_id}/export")
async def export_list(list_id: int):
    keywords = db.get_list_keywords(list_id)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["keyword","search_volume","cpc","competition_index","intent","opportunity_score"])
    writer.writeheader()
    for kw in keywords:
        writer.writerow({k: kw.get(k, "") for k in writer.fieldnames})
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=ssdi-list-{list_id}.csv"},
    )


# ─── Serve frontend ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html", encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print(f"\n  SSDIHunter starting → http://localhost:{port}\n")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
