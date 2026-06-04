import httpx
import asyncio
from classifier import is_ssdi_relevant, classify_intent

GOOGLE_URL = "https://suggestqueries.google.com/complete/search"
DDG_URL = "https://duckduckgo.com/ac/"

LETTERS = list("abcdefghijklmnopqrstuvwxyz")

QUESTION_PREFIXES = [
    "how to", "how do i", "how long does", "how much does",
    "what is", "what happens if", "what are the",
    "can i get", "can i still get", "can you get",
    "do i qualify for", "do i need a",
    "where to", "when does", "why was my",
    "who can help with", "what to do if",
]

INTENT_PREFIXES = [
    "apply for", "file for", "appeal",
    "attorney for", "lawyer for", "help with",
    "denied", "qualify for", "requirements for",
    "how to win", "how to get approved for",
    "how to appeal", "steps to apply for",
]

# Limit concurrent requests to avoid rate limiting
_SEM = asyncio.Semaphore(8)


async def _google(query: str, client: httpx.AsyncClient) -> list[tuple[str, int]]:
    async with _SEM:
        try:
            r = await client.get(
                GOOGLE_URL,
                params={"q": query, "client": "firefox", "hl": "en", "gl": "us"},
                timeout=8,
            )
            data = r.json()
            raw = data[1] if len(data) > 1 else []
            return [(s.lower().strip(), i + 1) for i, s in enumerate(raw) if s]
        except Exception:
            return []


async def _ddg(query: str, client: httpx.AsyncClient) -> list[tuple[str, int]]:
    async with _SEM:
        try:
            r = await client.get(DDG_URL, params={"q": query}, timeout=8)
            raw = r.json()  # [{"phrase": "..."}, ...]
            return [(item.get("phrase", "").lower().strip(), i + 1)
                    for i, item in enumerate(raw) if item.get("phrase")]
        except Exception:
            return []


def _build_queries(seed: str, depth: str) -> list[str]:
    s = seed.lower().strip()
    queries = [s]
    if depth in ("standard", "deep"):
        queries += [f"{s} {l}" for l in LETTERS]
        queries += [f"{l} {s}" for l in LETTERS]
    if depth == "deep":
        queries += [f"{p} {s}" for p in QUESTION_PREFIXES]
        queries += [f"{p} {s}" for p in INTENT_PREFIXES]
    return list(dict.fromkeys(queries))  # deduplicate while preserving order


def _score(freq: int, best_rank: int, source_count: int, intent: str) -> float:
    commercial = {"New Application", "Appeal / ALJ", "Denial Recovery", "Rep / Attorney", "Eligibility"}
    freq_score  = min(freq * 4, 40)
    rank_score  = (11 - min(best_rank, 10)) * 4
    source_score = source_count * 10
    intent_bonus = 10 if intent in commercial else 0
    return round(min(100.0, freq_score + rank_score + source_score + intent_bonus), 1)


async def mine(seed: str, depth: str = "standard") -> list[dict]:
    queries = _build_queries(seed, depth)

    # {keyword: {google_ranks, ddg_ranks, freq}}
    seen: dict[str, dict] = {}

    async with httpx.AsyncClient() as client:
        g_tasks = [_google(q, client) for q in queries]
        d_tasks = [_ddg(q, client) for q in queries]
        all_results = await asyncio.gather(*g_tasks, *d_tasks)

    g_results = all_results[:len(queries)]
    d_results = all_results[len(queries):]

    def _record(kw: str, rank: int, source: str):
        if not kw or not is_ssdi_relevant(kw):
            return
        if kw not in seen:
            seen[kw] = {"google_ranks": [], "ddg_ranks": [], "freq": 0}
        seen[kw][f"{source}_ranks"].append(rank)
        seen[kw]["freq"] += 1

    for suggestions in g_results:
        for kw, rank in suggestions:
            _record(kw, rank, "google")

    for suggestions in d_results:
        for kw, rank in suggestions:
            _record(kw, rank, "ddg")

    results = []
    for kw, data in seen.items():
        g_rank = min(data["google_ranks"]) if data["google_ranks"] else None
        d_rank = min(data["ddg_ranks"])    if data["ddg_ranks"]    else None
        best   = min(r for r in [g_rank, d_rank] if r is not None)
        sources = (["google"] if data["google_ranks"] else []) + \
                  (["ddg"]    if data["ddg_ranks"]    else [])
        intent = classify_intent(kw)
        results.append({
            "keyword":          kw,
            "google_rank":      g_rank,
            "ddg_rank":         d_rank,
            "best_rank":        best,
            "sources":          sources,
            "source_count":     len(sources),
            "frequency":        data["freq"],
            "intent":           intent,
            "opportunity_score": _score(data["freq"], best, len(sources), intent),
        })

    results.sort(key=lambda x: x["opportunity_score"], reverse=True)
    return results
