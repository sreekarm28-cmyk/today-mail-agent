"""Offline checks for email chunking and hybrid retrieval selection."""

from backend import runtime


def test_split_email_body_keeps_short_body_as_one_chunk():
    assert runtime._split_email_body("A short email body.") == ["A short email body."]


def test_split_email_body_overlaps_long_chunks():
    body = "word " * 700

    chunks = runtime._split_email_body(body)

    assert len(chunks) > 1
    assert all(len(chunk) <= runtime.EMAIL_EMBEDDING_CHUNK_CHARS for chunk in chunks)
    assert chunks[0][-runtime.EMAIL_EMBEDDING_CHUNK_OVERLAP_CHARS :].strip() in chunks[1]


def test_hybrid_search_merges_both_sources_and_keeps_best_five_emails():
    word_matches = [
        {"id": f"word-{index}", "subject": f"Word result {index}", "body": "full body"}
        for index in range(5)
    ]
    semantic_chunks = [
        {
            "email_id": "word-0",
            "chunk_text": "matching semantic passage",
            "internal_date": 1_797_000_000_000,
        },
        {
            "email_id": "semantic-only",
            "chunk_text": "meaning-only result",
            "internal_date": 1_797_000_000_000,
            "subject": "Semantic result",
            "sender": "sender@example.com",
        },
    ]

    results = runtime._merge_hybrid_email_matches(word_matches, semantic_chunks)

    assert len(results) == runtime.ASK_AI_MAX_SOURCES
    assert results[0]["id"] == "word-0"
    assert results[0]["body"] == "matching semantic passage"
    assert any(item["id"] == "semantic-only" for item in results)
    assert len({item["id"] for item in results}) == len(results)
