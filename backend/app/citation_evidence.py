"""Citation evidence: map answer [n] markers to source text fragments.

Phase-1 approach (no model schema change):
1. Enrich client-facing sources with short snippet/excerpt (no full content).
2. After the answer is complete, for each [n] claim find the best quote window
   in that source via date/number exact hits + token overlap.
3. Honest status: matched | weak | missing.
"""

from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import urlparse


# Non-whitespace length for "useful" snippet/excerpt targets.
_SNIPPET_TARGET = 480
_EXCERPT_TARGET = 720
_QUOTE_WINDOW = 320
_CLAIM_LOOKBACK = 160

_WS_RE = re.compile(r"\s+")
_CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
# Date-ish and number tokens used for hard evidence anchors.
_DATE_PATTERNS = [
    re.compile(r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?"),
    re.compile(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}"),
    re.compile(
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+\d{1,2},?\s+20\d{2}",
        re.I,
    ),
    re.compile(
        r"\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+20\d{2}",
        re.I,
    ),
]
_NUMBER_RE = re.compile(
    r"(?<![\w.])"  # not mid-token
    r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+%?|\d{4,}|\d+%|\d+\.\d+)"
    r"(?![\w.])"
)
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]{2,}", re.U)


def _clean_ws(text: str) -> str:
    return _WS_RE.sub(" ", (text or "")).strip()


def useful_char_count(text: str) -> int:
    if not text:
        return 0
    return len(re.sub(r"\s+", "", text))


def _domain_of(url: str) -> str:
    try:
        host = (urlparse(url or "").hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def build_snippet(content: str, max_len: int = _SNIPPET_TARGET) -> str:
    """First readable slice of page content for previews."""
    text = _clean_ws(content)
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    # Prefer break at sentence/space
    for sep in ("。", "！", "？", ". ", "; ", "；", " "):
        idx = cut.rfind(sep)
        if idx > max_len * 0.55:
            cut = cut[: idx + (0 if sep.startswith(" ") else len(sep))]
            break
    return cut.rstrip() + "…"


def build_excerpt(content: str, hints: Iterable[str] | None = None, max_len: int = _EXCERPT_TARGET) -> str:
    """Prefer a content window that covers hint keywords (query-ish), else snippet."""
    text = content or ""
    if not text.strip():
        return ""
    hint_tokens: list[str] = []
    for hint in hints or []:
        hint_tokens.extend(_TOKEN_RE.findall(str(hint).lower()))
    # unique preserve order
    seen: set[str] = set()
    tokens = []
    for t in hint_tokens:
        if t not in seen and len(t) >= 2:
            seen.add(t)
            tokens.append(t)
        if len(tokens) >= 12:
            break

    if not tokens:
        return build_snippet(text, max_len=max_len)

    lower = text.lower()
    best_i = 0
    best_score = -1
    step = max(40, max_len // 4)
    for i in range(0, max(1, len(text) - max_len + 1), step):
        window = lower[i : i + max_len]
        score = sum(1 for t in tokens if t in window)
        if score > best_score:
            best_score = score
            best_i = i
            if score >= min(4, len(tokens)):
                break
    excerpt = _clean_ws(text[best_i : best_i + max_len])
    if best_i > 0:
        excerpt = "…" + excerpt
    if best_i + max_len < len(text):
        excerpt = excerpt.rstrip() + "…"
    return excerpt


def client_source_payload(
    sources: list[dict[str, Any]] | None,
    *,
    query_hint: str | None = None,
) -> list[dict[str, Any]]:
    """Slim source objects for SSE / history UI (no full page content)."""
    payload: list[dict[str, Any]] = []
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        sid = source.get("id")
        if sid is None or sid == "":
            continue
        url = str(source.get("url") or "").strip()
        title = str(source.get("title") or url or f"Source {sid}").strip()
        content = str(source.get("content") or "")
        # Prefer existing slim fields (history reload) over recompute.
        snippet = str(source.get("snippet") or "").strip() or build_snippet(content)
        excerpt = str(source.get("excerpt") or "").strip()
        if not excerpt and content:
            excerpt = build_excerpt(content, hints=[query_hint or "", title])
        item: dict[str, Any] = {
            "id": sid if isinstance(sid, int) or str(sid).isdigit() else sid,
            "title": title,
        }
        if url:
            item["url"] = url
        date = source.get("date")
        if date not in (None, ""):
            item["date"] = date
        if snippet:
            item["snippet"] = snippet
        if excerpt:
            item["excerpt"] = excerpt
        chars = source.get("content_chars")
        if isinstance(chars, int):
            item["content_chars"] = chars
        elif content:
            item["content_chars"] = len(content)
        domain = _domain_of(url)
        if domain:
            item["domain"] = domain
        payload.append(item)
    return payload


def _claim_before_citation(answer: str, match_start: int) -> str:
    start = max(0, match_start - _CLAIM_LOOKBACK)
    chunk = answer[start:match_start]
    # Prefer text after last hard break
    for sep in ("\n", "。", "！", "？", ". ", "; ", "；"):
        idx = chunk.rfind(sep)
        if idx >= 0:
            chunk = chunk[idx + len(sep) :]
    claim = _clean_ws(chunk)
    # Strip trailing markdown emphasis crumbs
    claim = claim.strip(" *_`\"'「」『』")
    return claim


def extract_citation_claims(answer: str) -> list[dict[str, Any]]:
    """Return [{marker_ids: [int,...], claim: str, offset: int}, ...]."""
    text = answer or ""
    claims: list[dict[str, Any]] = []
    for match in _CITATION_RE.finditer(text):
        ids: list[int] = []
        for part in match.group(1).split(","):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
        if not ids:
            continue
        claim = _claim_before_citation(text, match.start())
        if not claim:
            # Fall back to a short window after the citation (rare)
            after = _clean_ws(text[match.end() : match.end() + 80])
            claim = after
        claims.append(
            {
                "marker_ids": ids,
                "claim": claim,
                "offset": match.start(),
            }
        )
    return claims


def _extract_anchors(claim: str) -> list[str]:
    anchors: list[str] = []
    for pat in _DATE_PATTERNS:
        anchors.extend(pat.findall(claim))
    anchors.extend(_NUMBER_RE.findall(claim))
    # Cross-language date expansions: 2025年8月7日 ↔ 2025-08-07 / August 7, 2025-ish keys
    for m in re.finditer(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?", claim):
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        anchors.append(f"{y}-{mo:02d}-{d:02d}")
        anchors.append(f"{y}/{mo}/{d}")
        anchors.append(f"{y}-{mo}-{d}")
        anchors.append(f"{mo}/{d}/{y}")
        # English month names for common research pages
        months = [
            "", "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
        if 1 <= mo <= 12:
            anchors.append(f"{months[mo]} {d}, {y}")
            anchors.append(f"{months[mo]} {d} {y}")
    # normalize whitespace in anchors
    cleaned = []
    seen = set()
    for a in anchors:
        key = _clean_ws(a)
        if key and key not in seen:
            seen.add(key)
            cleaned.append(key)
    return cleaned


def _normalize_for_search(text: str) -> str:
    # Keep digits/letters/CJK; drop most punctuation for looser matching
    t = text or ""
    t = t.replace("年", "-").replace("月", "-").replace("日", "")
    t = re.sub(r"\s+", "", t)
    return t.lower()


def find_quote_in_content(content: str, claim: str) -> dict[str, Any]:
    """Locate best quote window for a claim inside source content."""
    text = content or ""
    claim = claim or ""
    if not text.strip():
        return {
            "quote": "",
            "score": 0.0,
            "status": "missing",
            "method": "none",
            "char_start": None,
            "char_end": None,
        }

    anchors = _extract_anchors(claim)
    claim_tokens = [t.lower() for t in _TOKEN_RE.findall(claim)]
    claim_tokens = list(dict.fromkeys(claim_tokens))[:20]

    # 1) Exact / near-exact anchor windows (dates & numbers)
    for anchor in anchors:
        # try raw then whitespace-flexible
        idx = text.find(anchor)
        if idx < 0:
            # flexible: allow spaces between chars for CJK dates already normalized poorly
            pattern = re.escape(anchor)
            m = re.search(pattern, text)
            if m:
                idx = m.start()
                anchor = m.group(0)
        if idx < 0:
            # try normalized number without commas
            compact_anchor = anchor.replace(",", "")
            idx = text.replace(",", "").find(compact_anchor)
            if idx >= 0:
                # approximate position in original (best-effort)
                idx = max(0, text.find(compact_anchor[: min(4, len(compact_anchor))]) if compact_anchor else -1)
        if idx is None or idx < 0:
            continue
        start = max(0, idx - _QUOTE_WINDOW // 3)
        end = min(len(text), start + _QUOTE_WINDOW)
        # snap start forward a bit if mid-word
        quote = _clean_ws(text[start:end])
        if start > 0:
            quote = "…" + quote
        if end < len(text):
            quote = quote + "…"
        return {
            "quote": quote,
            "score": 0.92,
            "status": "matched",
            "method": "exact-anchor",
            "char_start": start,
            "char_end": end,
        }

    # 2) Sliding window token overlap
    if not claim_tokens:
        # no tokens — return leading snippet as weak
        snippet = build_snippet(text, max_len=_QUOTE_WINDOW)
        return {
            "quote": snippet,
            "score": 0.25 if snippet else 0.0,
            "status": "weak" if snippet else "missing",
            "method": "fallback-snippet",
            "char_start": 0 if snippet else None,
            "char_end": min(len(text), _QUOTE_WINDOW) if snippet else None,
        }

    lower = text.lower()
    best_score = -1.0
    best_start = 0
    window = _QUOTE_WINDOW
    step = max(40, window // 4)
    limit = max(1, len(text) - window + 1)
    for i in range(0, limit, step):
        win = lower[i : i + window]
        hits = sum(1 for t in claim_tokens if t in win)
        # density-ish
        score = hits / max(1, len(claim_tokens))
        if hits > 0 and score > best_score:
            best_score = score
            best_start = i

    if best_score < 0.15:
        snippet = build_snippet(text, max_len=_QUOTE_WINDOW)
        return {
            "quote": snippet,
            "score": round(max(best_score, 0.0), 3),
            "status": "weak" if snippet else "missing",
            "method": "overlap-weak",
            "char_start": 0 if snippet else None,
            "char_end": min(len(text), _QUOTE_WINDOW) if snippet else None,
        }

    end = min(len(text), best_start + window)
    quote = _clean_ws(text[best_start:end])
    if best_start > 0:
        quote = "…" + quote
    if end < len(text):
        quote = quote + "…"
    status = "matched" if best_score >= 0.35 else "weak"
    return {
        "quote": quote,
        "score": round(float(best_score), 3),
        "status": status,
        "method": "token-overlap",
        "char_start": best_start,
        "char_end": end,
    }


def build_citation_evidences(
    answer: str,
    sources: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Build per-marker evidence list for an assistant answer."""
    source_by_id: dict[str, dict[str, Any]] = {}
    for src in sources or []:
        if not isinstance(src, dict) or src.get("id") is None:
            continue
        source_by_id[str(src["id"])] = src

    evidences: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()

    for item in extract_citation_claims(answer):
        claim = item["claim"]
        for mid in item["marker_ids"]:
            key = (str(mid), claim[:80])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            src = source_by_id.get(str(mid))
            if not src:
                evidences.append(
                    {
                        "marker": mid,
                        "source_id": mid,
                        "claim": claim,
                        "quote": "",
                        "score": 0.0,
                        "status": "missing",
                        "method": "no-source",
                        "title": "",
                        "url": "",
                        "domain": "",
                    }
                )
                continue

            content = str(src.get("content") or "")
            # History may only have excerpt/snippet
            if not content:
                content = str(src.get("excerpt") or src.get("snippet") or "")

            hit = find_quote_in_content(content, claim)
            url = str(src.get("url") or "")
            evidences.append(
                {
                    "marker": mid,
                    "source_id": src.get("id", mid),
                    "claim": claim,
                    "quote": hit["quote"],
                    "score": hit["score"],
                    "status": hit["status"],
                    "method": hit["method"],
                    "char_start": hit.get("char_start"),
                    "char_end": hit.get("char_end"),
                    "title": str(src.get("title") or url or f"Source {mid}"),
                    "url": url,
                    "domain": _domain_of(url) or str(src.get("domain") or ""),
                    "date": src.get("date") or "",
                }
            )
    return evidences


def strip_source_content_for_storage(sources: list[dict[str, Any]] | None, query_hint: str | None = None) -> list[dict[str, Any]]:
    """Persist slim sources (snippet/excerpt) without full page bodies."""
    return client_source_payload(sources, query_hint=query_hint)
