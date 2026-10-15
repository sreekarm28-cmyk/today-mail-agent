"""Tests for privacy-conscious backend timing events."""

import json
import logging

from backend import diagnostics


def test_timing_log_includes_request_id_and_safe_metadata(caplog):
    request_token = diagnostics.set_request_id("test-request-123")
    try:
        with caplog.at_level(logging.INFO, logger="backend.diagnostics"):
            diagnostics.log_timing("gmail.message_list", 0.0, candidate_count=4)
    finally:
        diagnostics.reset_request_id(request_token)

    record = next(record for record in caplog.records if "APP_TIMING" in record.message)
    event = json.loads(record.message.removeprefix("APP_TIMING "))
    assert event["request_id"] == "test-request-123"
    assert event["step"] == "gmail.message_list"
    assert event["candidate_count"] == 4
    assert event["duration_ms"] >= 0
