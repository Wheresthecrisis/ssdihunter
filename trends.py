import time
from pytrends.request import TrendReq

ANCHOR = "ssdi"
# Conservative US monthly search baseline for "ssdi" (~200K/mo)
ANCHOR_MONTHLY = 200_000


def _fetch_batch(keywords: list[str], anchor: str) -> dict[str, float]:
    """Fetch relative Trends scores for up to 4 keywords vs anchor."""
    pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25), retries=2, backoff_factor=0.5)
    kw_list = [anchor] + keywords[:4]
    try:
        pytrends.build_payload(kw_list, cat=0, timeframe="today 12-m", geo="US")
        df = pytrends.interest_over_time()
        if df.empty or anchor not in df.columns:
            return {}
        anchor_mean = df[anchor].mean()
        if anchor_mean == 0:
            return {}
        return {
            kw: round((df[kw].mean() / anchor_mean) * 100, 1)
            for kw in keywords[:4]
            if kw in df.columns
        }
    except Exception:
        return {}


def get_trends_scores(keywords: list[str], anchor: str = ANCHOR) -> dict[str, float]:
    """Get relative Trends scores for all keywords, batched 4 at a time."""
    kws = [k for k in keywords if k.lower() != anchor.lower()]
    scores = {}
    for i in range(0, len(kws), 4):
        batch = kws[i : i + 4]
        scores.update(_fetch_batch(batch, anchor))
        if i + 4 < len(kws):
            time.sleep(1.2)  # stay under Trends rate limit
    return scores
