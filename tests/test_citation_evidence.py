"""Unit tests for citation evidence (claim → quote alignment)."""

from backend.app.citation_evidence import (
    build_citation_evidences,
    build_snippet,
    client_source_payload,
    extract_citation_claims,
    find_quote_in_content,
)


def test_client_source_payload_omits_full_content_and_adds_snippet():
    sources = [
        {
            "id": 1,
            "title": "Introducing GPT-5",
            "url": "https://openai.com/index/introducing-gpt-5/",
            "date": "2025-08-07",
            "content": "OpenAI released GPT-5 on August 7, 2025. " * 40,
        }
    ]
    payload = client_source_payload(sources, query_hint="GPT-5 release date")
    assert len(payload) == 1
    item = payload[0]
    assert item["id"] == 1
    assert "snippet" in item and len(item["snippet"]) > 20
    assert "content" not in item
    assert item["domain"] == "openai.com"
    assert item["content_chars"] > 100


def test_extract_citation_claims_parses_markers_and_claims():
    answer = (
        "GPT-5 于 2025年8月7日正式发布 [1]。\n"
        "Claude Opus 4.8 发布于 2026年5月28日 [2][3]。"
    )
    claims = extract_citation_claims(answer)
    assert len(claims) >= 2
    first = claims[0]
    assert 1 in first["marker_ids"]
    assert "2025" in first["claim"] or "GPT-5" in first["claim"]
    markers = {mid for c in claims for mid in c["marker_ids"]}
    assert {1, 2, 3}.issubset(markers)


def test_find_quote_matches_date_anchor():
    content = (
        "Introducing GPT-5\n\n"
        "August 7, 2025\n\n"
        "Today we are releasing GPT-5, our best model yet. "
        "It improves coding and writing quality substantially."
    )
    hit = find_quote_in_content(content, "GPT-5 于 August 7, 2025 发布")
    assert hit["status"] == "matched"
    assert hit["method"] == "exact-anchor"
    assert "August 7, 2025" in hit["quote"] or "2025" in hit["quote"]


def test_find_quote_weak_when_unrelated():
    content = "This page is about cooking pasta recipes and tomato sauce."
    hit = find_quote_in_content(content, "Rust 1.97 symbol mangling v0 default on July 9, 2026")
    assert hit["status"] in {"weak", "missing"}


def test_build_citation_evidences_end_to_end():
    sources = [
        {
            "id": 1,
            "title": "GPT-5",
            "url": "https://openai.com/index/introducing-gpt-5/",
            "content": (
                "Introducing GPT-5. August 7, 2025. "
                "GPT-5 is our smartest model with built-in thinking."
            ),
        },
        {
            "id": 2,
            "title": "Claude Opus 4.8",
            "url": "https://www.anthropic.com/news/claude-opus-4-8",
            "content": (
                "Introducing Claude Opus 4.8. May 28, 2026. "
                "Pricing remains $5 per million input tokens."
            ),
        },
    ]
    answer = (
        "GPT-5 发布日期是 2025年8月7日 [1]。"
        "Claude Opus 4.8 于 2026年5月28日发布 [2]。"
    )
    evidences = build_citation_evidences(answer, sources)
    assert len(evidences) >= 2
    by_marker = {e["marker"]: e for e in evidences}
    assert by_marker[1]["status"] in {"matched", "weak"}
    assert by_marker[1]["quote"]
    assert "openai.com" in by_marker[1]["domain"]
    assert by_marker[2]["quote"]
    # Chinese date should still anchor if we also have May 28, 2026 in content
    # claim may use Chinese date - token overlap or number 2026 should help
    assert by_marker[2]["status"] in {"matched", "weak"}


def test_build_snippet_truncates_long_text():
    text = "word " * 500
    snip = build_snippet(text, max_len=80)
    assert len(snip) <= 90
    assert snip.endswith("…") or len(snip) <= 80
