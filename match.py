import os
import json
from anthropic import Anthropic

_client = None

MATCH_RULES = """MATCH TYPE RULES FOR SSDI:
- EXACT: keywords ≤2 words, any keyword containing "attorney/lawyer/advocate/representative",
  "Rep / Attorney" intent, "Denial Recovery" intent with ≤3 words, or any keyword where
  broad/phrase would likely match VA disability, workers comp, ADA accommodations, or
  private insurance queries
- PHRASE: 3-4 word keywords with commercial intent (New Application, Appeal/ALJ, Eligibility),
  or moderate-specificity informational terms where phrase gives reach without chaos
- BROAD: 5+ word long-tail keywords with strong signals where the phrase itself is
  self-filtering, informational intent (General SSDI, Benefits/Amount, Condition-Specific)"""

NEGATIVE_CONTEXT = """NEGATIVE KEYWORD CONTEXT — always consider blocking:
- VA/veterans disability (va disability, veterans benefits, service connected)
- Workers compensation (workers comp, workplace injury, on the job)
- Private/long-term disability insurance (ltd, short term disability insurance, aflac)
- ADA/FMLA/workplace accommodations (ada, accommodation, fmla, leave of absence)
- Learning disabilities / special education (iep, 504 plan, special ed)
- Physical accessibility (wheelchair ramp, disability parking, handicap placard)
- Unrelated government programs (medicaid, snap, food stamps, unemployment)
- Personal injury / car accident
- Keyword-specific negatives that are unique to each term"""


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def advise(keywords: list[dict], quick: bool = False) -> list[dict]:
    """
    Use Claude to recommend match types for SSDI keywords.
    quick=True returns match type + rationale only (no negatives).
    quick=False returns full analysis including 10-20 negatives per keyword.
    """
    kw_data = [
        {
            "keyword": kw["keyword"],
            "intent": kw.get("intent", "General SSDI"),
            "word_count": len(kw["keyword"].split()),
            "best_rank": kw.get("best_rank"),
            "frequency": kw.get("frequency"),
            "source_count": kw.get("source_count"),
        }
        for kw in keywords
    ]

    if quick:
        prompt = f"""You are a Google Ads specialist for Quikaid, an SSDI/SSI disability claims representative in the US.

{MATCH_RULES}

KEYWORDS TO ANALYZE:
{json.dumps(kw_data, indent=2)}

Return ONLY a JSON array, no markdown:
[
  {{"keyword": "...", "match_type": "Exact|Phrase|Broad", "rationale": "1 sentence why"}},
  ...
]"""
    else:
        prompt = f"""You are a Google Ads specialist for Quikaid, an SSDI/SSI disability claims representative in the US.

{MATCH_RULES}

{NEGATIVE_CONTEXT}

KEYWORDS TO ANALYZE:
{json.dumps(kw_data, indent=2)}

For each keyword return:
- keyword: exact string
- match_type: "Exact" | "Phrase" | "Broad"
- rationale: 1 concise sentence explaining why
- negatives: array of 10-20 negative keyword strings — universal SSDI negatives AND keyword-specific ones

Return ONLY a JSON array, no markdown:
[
  {{
    "keyword": "...",
    "match_type": "Exact|Phrase|Broad",
    "rationale": "...",
    "negatives": ["...", "..."]
  }},
  ...
]"""

    message = _get_client().messages.create(
        model="claude-opus-4-8",
        max_tokens=8096,
        messages=[{"role": "user", "content": prompt}],
    )

    results = json.loads(message.content[0].text.strip())
    result_map = {r["keyword"]: r for r in results}

    for kw in keywords:
        advice = result_map.get(kw["keyword"], {})
        kw["match_type"]      = advice.get("match_type", "Exact")
        kw["match_rationale"] = advice.get("rationale", "")
        if not quick:
            kw["negatives"] = advice.get("negatives", [])

    return keywords


def consolidate(keywords: list[dict]) -> dict:
    """
    Analyze a full keyword set as a single ad group.
    Returns one deduplicated master negative list for the group
    plus any term-specific negatives that only apply to individual keywords.
    All negatives are formatted as [exact match] brackets.
    """
    kw_data = [
        {
            "keyword": kw["keyword"],
            "intent": kw.get("intent", "General SSDI"),
            "word_count": len(kw["keyword"].split()),
        }
        for kw in keywords
    ]

    prompt = f"""You are a Google Ads specialist for Quikaid, an SSDI/SSI disability claims representative in the US.

The following keywords will all go into a SINGLE ad group. Your job is to produce one
consolidated negative keyword list for the entire ad group — not separate lists per keyword.

{NEGATIVE_CONTEXT}

KEYWORD SET:
{json.dumps([k["keyword"] for k in kw_data], indent=2)}

INSTRUCTIONS:
1. Identify all negatives that apply to the group as a whole (master list)
2. Identify any negatives that are specific to only one or two keywords in the set
   (term-specific — e.g. "retirement" only matters if social security or age is mentioned)
3. Deduplicate ruthlessly — if a negative covers the same intent as another, keep only the
   most specific one
4. All negative strings should be lowercase, no brackets (you will add those in output format)

Return ONLY valid JSON, no markdown:
{{
  "match_type_summary": "1-2 sentences on the recommended match types across this ad group",
  "master_negatives": ["phrase one", "phrase two", ...],
  "term_specific": [
    {{"keyword": "...", "negatives": ["...", "..."]}}
  ]
}}"""

    message = _get_client().messages.create(
        model="claude-opus-4-8",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    result = json.loads(message.content[0].text.strip())

    # Wrap all negatives in [exact match] brackets
    result["master_negatives"] = [f"[{n}]" for n in result.get("master_negatives", [])]
    for ts in result.get("term_specific", []):
        ts["negatives"] = [f"[{n}]" for n in ts.get("negatives", [])]

    return result
