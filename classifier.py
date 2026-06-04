import re

# Patterns ordered by priority — first match wins
INTENT_PATTERNS = [
    ("Appeal / ALJ", [
        r'\balj\b',
        r'\badministrative law judge\b',
        r'\b(appeal|appealing|appealed)\b',
        r'\breconsideration\b',
        r'\b(disability|ssdi|ssi).{0,20}(hearing|appeal)\b',
        r'\b(hearing|appeal).{0,20}(disability|ssdi|ssi)\b',
        r'\bdenied.{0,20}appeal\b',
        r'\bappeal.{0,20}denied\b',
    ]),
    ("Denial Recovery", [
        r'\b(denied|denial|reject(ed)?)\b.{0,25}(disability|ssdi|ssi)\b',
        r'\b(disability|ssdi|ssi)\b.{0,25}\b(denied|denial|rejected)\b',
        r'\bwhat (to do|happens) (if|when|after).{0,20}deni\w+\b',
        r'\bhow (many|long|often).{0,20}deni\w+\b',
        r'\bssi denied\b',
        r'\bssdi denied\b',
    ]),
    ("Rep / Attorney", [
        r'\b(attorney|lawyer|law firm|representative|rep|advocate)\b.{0,20}(disability|ssdi|ssi)\b',
        r'\b(disability|ssdi|ssi)\b.{0,20}\b(attorney|lawyer|representative|advocate)\b',
        r'\bhire.{0,15}(disability|ssdi)\b',
        r'\b(disability|ssdi|ssi) (help|assistance|firm)\b',
        r'\bno.win no.fee\b',
        r'\bclarkhill|allsup\b',
    ]),
    ("New Application", [
        r'\bhow to (apply|file|start|begin|submit)\b',
        r'\b(apply|applying|file|filing|submit|start)\b.{0,20}(disability|ssdi|ssi)\b',
        r'\b(disability|ssdi|ssi)\b.{0,20}\b(apply|application|form|process)\b',
        r'\bapplication for (disability|ssdi|ssi)\b',
        r'\bssdi (application|apply|form)\b',
        r'\bssi (application|apply|form)\b',
        r'\bstart (a|my).{0,10}(disability|ssdi|ssi)\b',
        r'\bfirst time\b.{0,20}(disability|ssdi)\b',
    ]),
    ("Eligibility", [
        r'\b(qualify|qualif\w+|eligible|eligibility|requirements|criteria|rules)\b.{0,20}(disability|ssdi|ssi)\b',
        r'\b(disability|ssdi|ssi)\b.{0,20}\b(qualify|eligible|requirements|criteria)\b',
        r'\bdo i (qualify|get|receive|need)\b',
        r'\bwork credits\b',
        r'\bsga\b',
        r'\bsubstantial gainful activity\b',
        r'\bhow (long|many years)\b.{0,20}(work|worked)\b',
        r'\b(can i|am i)\b.{0,20}(disability|ssdi|ssi)\b',
        r'\bssdi (rules|requirements|criteria|work)\b',
        r'\bblue book\b',
        r'\blisting of impairments\b',
        r'\brfc\b',
        r'\bresidual functional capacity\b',
    ]),
    ("Benefits / Amount", [
        r'\b(how much|amount|payment|pay|check|backpay|back pay|lump sum|retroactive)\b.{0,20}(disability|ssdi|ssi)\b',
        r'\b(disability|ssdi|ssi)\b.{0,20}\b(how much|amount|payment|backpay|check)\b',
        r'\b(monthly|average|maximum|max)\b.{0,15}(disability|ssdi|ssi)\b.{0,15}(payment|benefit|check|amount)\b',
        r'\bssdi (payment|benefit|check|amount|backpay|back pay)\b',
        r'\bssi (payment|benefit|check|amount)\b',
        r'\bdisability (income|payment|benefit amount)\b',
        r'\bhow long.{0,20}(receive|get|start)\b.{0,15}(ssdi|ssi|disability)\b',
    ]),
    ("Condition-Specific", [
        r'\b(depression|anxiety|ptsd|bipolar|schizophrenia|psychosis|personality disorder)\b',
        r'\b(diabetes|diabetic|type 1|type 2)\b',
        r'\b(cancer|tumor|leukemia|lymphoma|carcinoma)\b',
        r'\b(multiple sclerosis|ms disability|parkinsons|parkinson)\b',
        r'\b(fibromyalgia|lupus|rheumatoid|arthritis)\b',
        r'\b(copd|emphysema|asthma|pulmonary|respiratory)\b',
        r'\b(back pain|spine|spinal|herniated|degenerative disc|scoliosis|stenosis)\b',
        r'\b(neuropathy|nerve damage|peripheral nerve)\b',
        r'\b(epilepsy|seizure|seizures)\b',
        r'\b(autism|autistic|asd|adhd|intellectual disability|developmental)\b',
        r'\b(heart (disease|failure|condition)|cardiac|congestive)\b',
        r'\b(kidney (disease|failure)|renal)\b',
        r'\b(liver (disease|failure|cirrhosis))\b',
        r'\b(hiv|aids)\b',
        r'\b(stroke|tbi|traumatic brain injury|brain injury)\b',
        r'\b(als|amyotrophic lateral|muscular dystrophy)\b',
        r'\b(chronic pain|chronic fatigue|myalgic)\b',
        r'\b(crohns|ulcerative colitis|ibd|inflammatory bowel)\b',
        r'\b(bipolar disorder|manic depression)\b',
        r'\b(schizoaffective|schizoprenia)\b',
        r'\b(ocd|obsessive compulsive)\b',
        r'\b(addiction|substance use|alcoholism)\b.{0,20}(disability)\b',
    ]),
]

# Broad signals — any keyword containing these roots is a candidate
# Strategy: cast wide net, then exclude non-SSDI noise below
SSDI_SIGNALS = [
    r'\bdisabilit\w*\b',        # disability, disabilities, disabled
    r'\bdisabled\b',
    r'\bssdi\b',
    r'\bssi\b',
    r'\bsocial security\b',
    r'\bsupplemental security\b',
    r'\balj\b',
    r'\badministrative law judge\b',
    r'\bpoms\b',
    r'\brfc\b',
    r'\bresidual functional capacity\b',
    r'\bonset date\b',
    r'\bwork credits\b',
    r'\bsubstantial gainful activity\b',
    r'\bsga\b',
    r'\bblue book (listing|impairment)\b',
    r'\blisting of impairments\b',
    r'\bfive.step (sequential|evaluation|process)\b',
    r'\bssa \b',                 # SSA followed by anything
    r'\bsocial security administration\b',
]

# Exclude patterns — clearly not SSDI/SSI, strip these out
EXCLUDE_PATTERNS = [
    # Veterans / military
    r'\bva (disability|claim|benefit|rating|compensation)\b',
    r'\bveteran.{0,15}(disability|benefit|claim)\b',
    r'\bmilitary (disability|benefit)\b',
    r'\bservice.connected\b',
    # Workers comp
    r'\bworkers.? comp(ensation)?\b',
    r'\bworkplace (injury|accident)\b',
    # Private insurance products
    r'\blong.term disability (insurance|policy|coverage|plan|quote|carrier)\b',
    r'\bshort.term disability (insurance|policy|coverage|plan)\b',
    r'\bdisability insurance (policy|coverage|plan|quote|carrier|cost|premium|rate)\b',
    r'\blife insurance\b',
    r'\bprivate (disability|insurance)\b',
    # Workplace / ADA / HR
    r'\bada\b',
    r'\baccommodation(s)?\b',
    r'\bworkplace accommodation\b',
    r'\bfmla\b',
    r'\bleave of absence\b',
    r'\bemployer\b',
    r'\bhr department\b',
    # Personal injury / legal unrelated
    r'\bpersonal injury\b',
    r'\bcar accident\b',
    r'\bslip and fall\b',
    r'\bmedical malpractice\b',
    r'\bnegligence\b',
    # Education / developmental (non-claim context)
    r'\blearning disabilit\w*\b',
    r'\bspecial education\b',
    r'\biep\b',
    r'\b504 plan\b',
    r'\bdevelopmental disabilit\w*.{0,20}(school|education|child|program|service|center|support)\b',
    # Physical accessibility (non-claim)
    r'\bdisability (parking|placard|ramp|access|bathroom|restroom|elevator|tag)\b',
    r'\bwheelchair (ramp|lift|access)\b',
    r'\baccessibilit\w*\b',
    # Unrelated government programs
    r'\bmedicaid\b',
    r'\bmedicare (plan|advantage|supplement|part [abcd])\b',
    r'\bsnap\b',
    r'\bfood stamp\b',
    r'\bunemployment (insurance|benefit|claim|compensation)\b',
]


def _matches_any(text: str, patterns: list) -> bool:
    t = text.lower()
    return any(re.search(p, t) for p in patterns)


def is_ssdi_relevant(keyword: str) -> bool:
    kw = keyword.lower()
    if _matches_any(kw, EXCLUDE_PATTERNS):
        return False
    return _matches_any(kw, SSDI_SIGNALS)


def classify_intent(keyword: str) -> str:
    kw = keyword.lower()
    for intent_label, patterns in INTENT_PATTERNS:
        if _matches_any(kw, patterns):
            return intent_label
    return "General SSDI"


def opportunity_score(search_volume: int, cpc: float, competition_index: int) -> float:
    if not search_volume:
        return 0.0
    vol = max(search_volume, 0)
    cpc_val = max(cpc or 0.0, 0.01)
    comp = max(competition_index or 1, 1)
    # Rewards high volume, high CPC (commercial intent), low competition
    return round((vol * cpc_val) / comp, 2)


def enrich_keywords(raw_items: list) -> list:
    results = []
    for item in raw_items:
        kw = item.get("keyword", "").strip()
        if not kw:
            continue
        if not is_ssdi_relevant(kw):
            continue
        sv = item.get("search_volume") or 0
        cpc = item.get("cpc") or 0.0
        ci = item.get("competition_index") or 0
        results.append({
            "keyword": kw,
            "search_volume": sv,
            "cpc": round(cpc, 2),
            "competition_index": ci,
            "intent": classify_intent(kw),
            "opportunity_score": opportunity_score(sv, cpc, ci),
            "monthly_searches": item.get("monthly_searches", []),
        })
    return results


def normalize_scores(keywords: list) -> list:
    """Rescale opportunity_score to 0–100 within a result set."""
    if not keywords:
        return keywords
    max_score = max(k["opportunity_score"] for k in keywords) or 1
    for k in keywords:
        k["opportunity_score"] = round((k["opportunity_score"] / max_score) * 100, 1)
    return keywords
