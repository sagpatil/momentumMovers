"""Classify the catalyst behind a momentum move from recent headlines.

Two-stage, per the plan: deterministic keyword rules first; only headlines the
rules can't confidently bucket are sent to an LLM (Claude). The LLM stage is
optional — if `ANTHROPIC_API_KEY` is unset or the SDK is missing, we run
rules-only so a forked CI without the secret still produces a full snapshot.

Output per ticker:
  {
    "strength": "strong" | "weak" | "none",
    "score": 0.0..1.0,          # feeds MQS catalyst component
    "label": short human tag,   # e.g. "FDA approval", "Earnings beat"
    "reason": one-line explanation,
    "source": "rules" | "llm",
  }
"""
from __future__ import annotations

import os
import re

# Strong, multi-day catalysts (earnings/FDA/M&A are the strongest per the spec).
_STRONG = [
    (r"\b(fda|phase\s*[123]|approval|breakthrough therapy|clinical|trial results)\b", "FDA / clinical"),
    (r"\b(earnings|beat|tops estimates|guidance (raise|hike|boost)|raises (guidance|outlook)|record (revenue|quarter))\b", "Earnings / guidance"),
    (r"\b(acqui|merger|buyout|takeover|to be acquired|strategic (alternatives|review)|deal to)\b", "M&A"),
    (r"\b(contract|awarded|wins? .* (deal|order|contract)|partnership with|agreement (with|to)|secures)\b", "Contract / partnership"),
    (r"\b(upgrade[sd]?|price target (raise|hike|increase)|initiates? .* (buy|outperform)|reiterates? buy)\b", "Analyst upgrade"),
    (r"\b(insider (buy|purchase)|ceo buys|director buys)\b", "Insider buying"),
]

# Weak / noise headlines — present but not a real single-stock driver.
_WEAK = [
    (r"\b(stocks? to watch|movers|trending|why is .* (up|surging|soaring)|52-week high|hits? new high)\b", "Momentum chatter"),
    (r"\b(sector|peers|sympathy|rally|market|index|s&p|nasdaq|dow)\b", "Sector / macro"),
    (r"\b(reddit|wallstreetbets|retail|social|meme)\b", "Retail buzz"),
]

_STRONG_RE = [(re.compile(p, re.I), tag) for p, tag in _STRONG]
_WEAK_RE = [(re.compile(p, re.I), tag) for p, tag in _WEAK]


def _rule_classify_one(title: str) -> tuple[str, str] | None:
    for rx, tag in _STRONG_RE:
        if rx.search(title):
            return "strong", tag
    for rx, tag in _WEAK_RE:
        if rx.search(title):
            return "weak", tag
    return None


def _rules(news: list[dict]) -> dict:
    """Classify from headlines using rules alone. May return strength 'unknown'
    (no headlines matched), signalling the LLM stage to take over."""
    if not news:
        return {"strength": "none", "score": 0.15, "label": "No recent news",
                "reason": "No headlines in the last few days.", "source": "rules"}

    best = None  # prefer the strongest match across all headlines
    for item in news:
        res = _rule_classify_one(item.get("title", ""))
        if res is None:
            continue
        strength, tag = res
        if strength == "strong":
            best = ("strong", tag, item["title"])
            break
        if best is None:
            best = ("weak", tag, item["title"])

    if best is None:
        return {"strength": "unknown", "score": 0.4, "label": "Unclassified",
                "reason": "Headlines present but no rule matched.", "source": "rules"}

    strength, tag, title = best
    score = 0.9 if strength == "strong" else 0.45
    return {"strength": strength, "score": score, "label": tag,
            "reason": title[:160], "source": "rules"}


# ------------------------------- LLM fallback -------------------------------

_LLM_MODEL = "claude-haiku-4-5"  # fast + cheap; headline triage doesn't need Opus


def _llm_classify(ticker: str, news: list[dict], client) -> dict | None:
    headlines = "\n".join(f"- {n['title']}" for n in news[:6] if n.get("title"))
    prompt = (
        f"You classify the catalyst behind a stock's momentum move. Ticker: {ticker}.\n"
        f"Recent headlines:\n{headlines}\n\n"
        "Reply with ONE line of compact JSON: "
        '{"strength":"strong|weak|none","label":"<=4 words","reason":"<=15 words"}. '
        "strong = earnings beat, guidance raise, FDA/clinical, M&A, major contract, "
        "analyst upgrade, insider buying. weak = sector sympathy, retail buzz, "
        "generic 'stocks to watch'. none = nothing material."
    )
    try:
        msg = client.messages.create(
            model=_LLM_MODEL,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        import json

        text = msg.content[0].text.strip()
        text = text[text.find("{") : text.rfind("}") + 1]
        data = json.loads(text)
        strength = data.get("strength", "weak")
        score = {"strong": 0.9, "weak": 0.45, "none": 0.15}.get(strength, 0.4)
        return {
            "strength": strength,
            "score": score,
            "label": str(data.get("label", "Unclassified"))[:40],
            "reason": str(data.get("reason", ""))[:160],
            "source": "llm",
        }
    except Exception:  # noqa: BLE001 — LLM is best-effort; fall back to rules result
        return None


def _get_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic

        return anthropic.Anthropic()
    except Exception:  # noqa: BLE001
        return None


def classify_catalysts(enriched: dict[str, dict]) -> dict[str, dict]:
    """Classify every ticker. Rules first; LLM only for 'unknown' rule results."""
    client = _get_client()
    out: dict[str, dict] = {}
    for ticker, rec in enriched.items():
        news = rec.get("news", [])
        res = _rules(news)
        if res["strength"] == "unknown" and client is not None:
            llm = _llm_classify(ticker, news, client)
            if llm is not None:
                res = llm
        out[ticker] = res
    return out


if __name__ == "__main__":
    sample = {
        "ABC": {"news": [{"title": "ABC Therapeutics announces FDA approval of lead drug"}]},
        "XYZ": {"news": [{"title": "Why is XYZ stock soaring today? Retail piles in"}]},
        "QQQ": {"news": []},
    }
    import json

    print(json.dumps(classify_catalysts(sample), indent=2))
