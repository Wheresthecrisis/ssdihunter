import os
import csv
import io
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

import database as db
import autocomplete as ac
import searchterms as st


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="SSDIHunter", lifespan=lifespan)


# ─── Request models ────────────────────────────────────────────────────────────

class ResearchRequest(BaseModel):
    seed: str
    min_frequency: int = 1
    max_rank: int = 10
    intent_filter: Optional[str] = None
    depth: str = "standard"

class GapRequest(BaseModel):
    seed: str
    min_frequency: int = 1
    max_rank: int = 5
    depth: str = "standard"

class LongTailRequest(BaseModel):
    seed: str
    min_words: int = 3
    depth: str = "standard"

class SaveRequest(BaseModel):
    list_name: str
    keywords: list[dict]

class AddToListRequest(BaseModel):
    list_id: int
    keywords: list[dict]

class VolumeEstimateRequest(BaseModel):
    keywords: list[dict]

class MatchAdviceRequest(BaseModel):
    keywords: list[dict]
    quick: bool = False  # True = match type + rationale only, no negatives


# ─── Helpers ───────────────────────────────────────────────────────────────────

def apply_filters(keywords: list, max_rank: int, min_frequency: int,
                  intent_filter: str = None) -> list:
    out = [k for k in keywords
           if k["best_rank"] <= max_rank
           and k["frequency"] >= min_frequency]
    if intent_filter and intent_filter != "All":
        out = [k for k in out if k["intent"] == intent_filter]
    return out


async def fetch_and_enrich(seed: str, depth: str) -> list:
    cache_hash = db.cache_key("ac", {"seed": seed.lower().strip(), "depth": depth})
    cached = db.get_cached(cache_hash)
    if cached:
        return cached
    results = await ac.mine(seed, depth)
    if results:
        db.set_cache(cache_hash, seed, results)
    return results


# ─── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "engine": "autocomplete"}


@app.post("/api/research")
async def research(req: ResearchRequest):
    try:
        keywords = await fetch_and_enrich(req.seed, req.depth)
        filtered = apply_filters(keywords, req.max_rank, req.min_frequency, req.intent_filter)
        db.add_history(req.seed, "Research", len(filtered))
        return {"keywords": filtered, "total": len(filtered)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/gaps")
async def gaps(req: GapRequest):
    try:
        keywords = await fetch_and_enrich(req.seed, req.depth)
        # Gaps: confirmed by BOTH sources but low frequency = real demand, underexploited
        filtered = [k for k in keywords
                    if k["source_count"] == 2
                    and k["best_rank"] <= req.max_rank
                    and k["frequency"] <= 4]
        filtered.sort(key=lambda x: x["opportunity_score"], reverse=True)
        db.add_history(req.seed, "Gap Finder", len(filtered))
        return {"keywords": filtered, "total": len(filtered)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/longtail")
async def longtail(req: LongTailRequest):
    try:
        keywords = await fetch_and_enrich(req.seed, req.depth)
        filtered = [k for k in keywords if len(k["keyword"].split()) >= req.min_words]
        for k in filtered:
            k["longtail_score"] = round(len(k["keyword"].split()) * k["opportunity_score"], 1)
        filtered.sort(key=lambda x: x["longtail_score"], reverse=True)
        db.add_history(req.seed, "Long-tail", len(filtered))
        return {"keywords": filtered, "total": len(filtered)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/history")
async def history():
    return db.get_history()


@app.get("/api/lists")
async def get_lists():
    return db.get_lists()


@app.get("/api/lists/{list_id}")
async def get_list(list_id: int):
    return {"keywords": db.get_list_keywords(list_id)}


@app.post("/api/lists")
async def create_list(req: SaveRequest):
    list_id = db.create_list(req.list_name)
    db.add_keywords_to_list(list_id, req.keywords)
    return {"id": list_id, "name": req.list_name}


@app.delete("/api/lists/{list_id}")
async def delete_list(list_id: int):
    db.delete_list(list_id)
    return {"ok": True}


@app.post("/api/lists/{list_id}/keywords")
async def add_to_list(list_id: int, req: AddToListRequest):
    db.add_keywords_to_list(list_id, req.keywords)
    return {"ok": True, "added": len(req.keywords)}


@app.delete("/api/lists/keywords/{keyword_id}")
async def delete_keyword(keyword_id: int):
    db.delete_list_keyword(keyword_id)
    return {"ok": True}


@app.get("/api/lists/{list_id}/export")
async def export_list(list_id: int):
    keywords = db.get_list_keywords(list_id)
    output = io.StringIO()
    fields = ["keyword", "google_rank", "ddg_rank", "best_rank",
              "source_count", "frequency", "intent", "opportunity_score"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for kw in keywords:
        writer.writerow({f: kw.get(f, "") for f in fields})
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=ssdi-list-{list_id}.csv"},
    )


@app.post("/api/match-adgroup")
async def match_adgroup(req: MatchAdviceRequest):
    try:
        import match as m
        if not req.keywords:
            raise HTTPException(status_code=400, detail="No keywords provided")
        result = await asyncio.to_thread(m.consolidate, req.keywords[:50])
        return result
    except KeyError:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set in environment")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/match-advice")
async def match_advice(req: MatchAdviceRequest):
    try:
        import match as m
        if not req.keywords:
            raise HTTPException(status_code=400, detail="No keywords provided")
        kws = req.keywords[:30]  # cap at 30 — negatives per kw make responses large
        enriched = await asyncio.to_thread(m.advise, kws, req.quick)
        return {"keywords": enriched}
    except KeyError:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set in environment")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/volume-estimate")
async def volume_estimate(req: VolumeEstimateRequest):
    try:
        import volume as vol
        if not req.keywords:
            raise HTTPException(status_code=400, detail="No keywords provided")
        # Cap at 50 — Trends batching takes ~1s/batch, 50 kws ≈ 13 batches ≈ 15s
        kws = req.keywords[:50]
        enriched = await asyncio.to_thread(vol.estimate_volumes, kws)
        return {"keywords": enriched}
    except KeyError:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set in environment")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/search-terms/analyze")
async def analyze_search_terms(file: UploadFile = File(...), target_cpl: float = Form(20.0)):
    try:
        content = await file.read()
        text = content.decode("utf-8-sig", errors="ignore")
        result = st.analyze(text, target_cpl=target_cpl)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html", encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print(f"\n  SSDIHunter starting → http://localhost:{port}\n")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
