import csv
import io
import math
import re

# Terms that are clearly about SSI (need-based) rather than SSDI (work-credit based)
# should not be promoted into an SSDI campaign as exact-match keywords.
SSI_SPECIFIC_PATTERN = re.compile(r'\bssi\b|\bsupplemental security\b', re.IGNORECASE)
SSDI_PATTERN = re.compile(r'\bssdi\b|\bsocial security disability\b', re.IGNORECASE)


def _is_ssi_specific(term: str) -> bool:
    return bool(SSI_SPECIFIC_PATTERN.search(term)) and not SSDI_PATTERN.search(term)


def _num(s: str) -> float:
    s = (s or "").strip().replace("$", "").replace(",", "").replace("%", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _find_header_row(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        if line.startswith("Search term,") and "Match type" in line:
            return i
    return 0


def analyze(csv_text: str, target_cpl: float = 20.0, kill_multiplier: float = 2.0,
            significance_p: float = 0.15) -> dict:
    lines = csv_text.splitlines()
    header_idx = _find_header_row(lines)
    reader = csv.DictReader(lines[header_idx:])

    terms: dict[str, dict] = {}
    account_totals = None
    total_clicks = total_impr = total_cost = total_conv = 0.0

    for row in reader:
        term = (row.get("Search term") or "").strip()
        if term.lower() == "total: account":
            account_totals = {
                "clicks": _num(row.get("Clicks")),
                "impr": _num(row.get("Impr.")),
                "cost": _num(row.get("Cost")),
                "conv": _num(row.get("Conversions")),
            }
        if not term or term.lower().startswith("total:"):
            continue
        clicks = _num(row.get("Clicks"))
        impr = _num(row.get("Impr."))
        cost = _num(row.get("Cost"))
        conv = _num(row.get("Conversions"))
        status = (row.get("Added/Excluded") or "None").strip()

        total_clicks += clicks
        total_impr += impr
        total_cost += cost
        total_conv += conv

        agg = terms.setdefault(term, {
            "search_term": term, "clicks": 0.0, "impr": 0.0, "cost": 0.0,
            "conversions": 0.0, "ad_groups": set(), "statuses": set(),
        })
        agg["clicks"] += clicks
        agg["impr"] += impr
        agg["cost"] += cost
        agg["conversions"] += conv
        agg["ad_groups"].add(row.get("Ad group") or "")
        agg["statuses"].add(status)

    # Prefer the report's "Total: Account" row for benchmarks — it reflects true
    # account-wide CTR/conv rate, including traffic not broken out into individual
    # search term rows (e.g. low-volume "other search terms", non-Search campaigns).
    if account_totals and account_totals["impr"]:
        avg_ctr = account_totals["clicks"] / account_totals["impr"] * 100
        avg_conv_rate = (account_totals["conv"] / account_totals["clicks"]) if account_totals["clicks"] else 0.0
        overall_cpl = (account_totals["cost"] / account_totals["conv"]) if account_totals["conv"] else None
    else:
        avg_ctr = (total_clicks / total_impr * 100) if total_impr else 0.0
        avg_conv_rate = (total_conv / total_clicks) if total_clicks else 0.0
        overall_cpl = (total_cost / total_conv) if total_conv else None

    # Clicks needed before a zero-conversion term is statistically meaningful,
    # i.e. P(zero conversions by chance | avg conv rate) <= significance_p
    if 0 < avg_conv_rate < 1:
        min_clicks_for_significance = math.ceil(math.log(significance_p) / math.log(1 - avg_conv_rate))
    else:
        min_clicks_for_significance = 999999
    kill_cpl = target_cpl * kill_multiplier

    negatives, watch_list, promotions = [], [], []

    for agg in terms.values():
        if "Added" in agg["statuses"] or "Excluded" in agg["statuses"]:
            continue  # already its own keyword, or already excluded — not a candidate

        clicks, impr, cost, conv = agg["clicks"], agg["impr"], agg["cost"], agg["conversions"]
        cpl = (cost / conv) if conv else None
        ctr = (clicks / impr * 100) if impr else 0.0
        ad_group = sorted(agg["ad_groups"], key=lambda g: -cost if g else 0)[0] if agg["ad_groups"] else ""

        entry = {
            "search_term": agg["search_term"], "ad_group": ad_group,
            "clicks": clicks, "impr": impr, "cost": round(cost, 2),
            "conversions": conv, "cpl": round(cpl, 2) if cpl else None, "ctr": round(ctr, 2),
        }

        if conv >= 2 and cpl is not None and cpl >= kill_cpl:
            negatives.append({**entry, "tier": "Confirmed waste",
                               "reason": f"{int(conv)} conversions at ${cpl:.2f}/conv — well above ${kill_cpl:.0f} kill threshold"})
        elif conv == 0 and clicks >= min_clicks_for_significance and cost > 0:
            negatives.append({**entry, "tier": "Probable waste",
                               "reason": f"{int(clicks)} clicks, ${cost:.2f} spent, 0 conversions — statistically unlikely to convert at {avg_conv_rate*100:.1f}% account avg rate"})
        elif conv == 0 and impr >= min_clicks_for_significance * 3 and avg_ctr > 0 and ctr < avg_ctr * 0.5:
            watch_list.append({**entry, "tier": "Weak signal",
                                "reason": f"CTR {ctr:.1f}% vs {avg_ctr:.1f}% account avg — low relevance, monitor"})
        elif conv >= 2 and cpl is not None and cpl <= target_cpl and not _is_ssi_specific(agg["search_term"]):
            promotions.append({**entry,
                                "reason": f"{int(conv)} conversions at ${cpl:.2f}/conv — at or below ${target_cpl:.0f} target"})

    negatives.sort(key=lambda x: x["cost"], reverse=True)
    watch_list.sort(key=lambda x: x["impr"], reverse=True)
    promotions.sort(key=lambda x: x["cpl"])

    return {
        "stats": {
            "rows_analyzed": len(terms),
            "avg_ctr": round(avg_ctr, 2),
            "avg_conv_rate": round(avg_conv_rate * 100, 2),
            "overall_cpl": round(overall_cpl, 2) if overall_cpl else None,
            "min_clicks_for_significance": min_clicks_for_significance,
            "kill_cpl": kill_cpl,
            "target_cpl": target_cpl,
        },
        "negatives": negatives,
        "watch_list": watch_list,
        "promotions": promotions,
    }
