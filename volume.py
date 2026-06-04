import os
import json
from anthropic import Anthropic
from trends import get_trends_scores, ANCHOR, ANCHOR_MONTHLY

_client = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _volume_bracket(trends_score: float | None, freq: int, rank: int) -> str:
    """Rough bracket from Trends score alone, used as Claude's starting point."""
    if trends_score is None:
        return "unknown"
    monthly = (trends_score / 100) * ANCHOR_MONTHLY
    if monthly < 500:
        return "<500/mo"
    if monthly < 2_000:
        return "500–2K/mo"
    if monthly < 10_000:
        return "2K–10K/mo"
    if monthly < 50_000:
        return "10K–50K/mo"
    if monthly < 200_000:
        return "50K–200K/mo"
    return ">200K/mo"


def estimate_volumes(keywords: list[dict]) -> list[dict]:
    """
    Fetch live Google Trends scores, then use Claude to reason over
    those scores + autocomplete signals and return volume bracket estimates.
    Modifies keyword dicts in place, returns them.
    """
    kw_strings = [k["keyword"] for k in keywords]
    trends_scores = get_trends_scores(kw_strings)

    # Build structured data payload — Claude reasons FROM this, not from memory
    kw_data = [
        {
            "keyword": kw["keyword"],
            "trends_score_vs_ssdi": trends_scores.get(kw["keyword"]),
            "autocomplete_rank": kw.get("best_rank"),
            "frequency": kw.get("frequency"),
            "source_count": kw.get("source_count"),
            "intent": kw.get("intent"),
        }
        for kw in keywords
    ]

    prompt = f"""You are estimating US monthly search volumes for SSDI (Social Security Disability Insurance) keywords.

GROUND TRUTH ANCHOR:
- Keyword: "{ANCHOR}" = approximately {ANCHOR_MONTHLY:,} monthly US searches
- All trends_score_vs_ssdi values are relative to this anchor (ssdi = 100)
- A trends_score of 50 means roughly half as many searches as ssdi (~100K/mo)
- A trends_score of 200 means roughly double (~400K/mo)

SECONDARY SIGNALS (use to adjust Trends estimate ±1 bracket):
- autocomplete_rank 1–3 = top of suggestions = toward upper end of bracket
- autocomplete_rank 7–10 = lower volume within bracket
- frequency ≥ 15 = high demand signal, nudge up
- source_count = 2 (both Google + DDG) = confirmed demand, higher confidence
- source_count = 1 = single source, lower confidence

KEYWORD DATA (live Google Trends data fetched today):
{json.dumps(kw_data, indent=2)}

Instructions:
1. For each keyword with a trends_score, scale linearly from the anchor, then adjust ±1 bracket based on secondary signals
2. For keywords with no trends_score (null), use autocomplete signals only and mark confidence "low"
3. Be conservative — round down when uncertain

Return ONLY a JSON array, no explanation, no markdown:
[
  {{"keyword": "...", "volume_bracket": "...", "confidence": "high|medium|low"}},
  ...
]

Volume bracket values must be exactly one of: "<500/mo", "500–2K/mo", "2K–10K/mo", "10K–50K/mo", "50K–200K/mo", ">200K/mo"
Confidence: high = Trends score present + strong autocomplete, medium = Trends score present, low = no Trends score"""

    message = _get_client().messages.create(
        model="claude-opus-4-8",
        max_tokens=8096,
        messages=[{"role": "user", "content": prompt}],
    )

    estimates = json.loads(message.content[0].text.strip())
    estimate_map = {e["keyword"]: e for e in estimates}

    for kw in keywords:
        est = estimate_map.get(kw["keyword"], {})
        kw["volume_bracket"] = est.get("volume_bracket", "—")
        kw["volume_confidence"] = est.get("confidence", "low")
        kw["trends_score"] = trends_scores.get(kw["keyword"])

    return keywords
