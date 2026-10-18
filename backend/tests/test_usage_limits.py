"""Small offline unit tests for request limits; no Gmail or OpenRouter calls."""

import unittest

from fastapi import Request
from fastapi.testclient import TestClient
from limits import RateLimitItemPerDay, RateLimitItemPerMinute

from backend import web


def _request_for_user(user_id):
    request = Request({"type": "http", "headers": [], "method": "POST", "path": "/"})
    request.state.app_user_id = user_id
    return request


class UsageLimitTests(unittest.TestCase):
    def setUp(self):
        web.limiter.reset()

    def tearDown(self):
        web.limiter.reset()

    def test_only_email_summary_endpoint_is_available(self):
        paths = {route.path for route in web.app.routes}
        self.assertNotIn("/api/summarize", paths)
        self.assertIn("/api/dashboard/emails/{email_id}/summary", paths)
        self.assertIn("/api/model/diagnostic", paths)

    def test_user_can_make_two_email_summaries_per_minute(self):
        user_key = web._rate_limit_key(_request_for_user("user-a"))
        minute_limit = RateLimitItemPerMinute(web.MODEL_REQUESTS_PER_MINUTE)

        self.assertTrue(web.limiter.limiter.hit(minute_limit, user_key, "model-requests-per-minute"))
        self.assertTrue(web.limiter.limiter.hit(minute_limit, user_key, "model-requests-per-minute"))
        self.assertFalse(web.limiter.limiter.hit(minute_limit, user_key, "model-requests-per-minute"))

    def test_user_can_make_ten_email_summaries_per_day(self):
        user_key = web._rate_limit_key(_request_for_user("user-daily"))
        day_limit = RateLimitItemPerDay(web.MODEL_REQUESTS_PER_DAY)

        for _ in range(web.MODEL_REQUESTS_PER_DAY):
            self.assertTrue(web.limiter.limiter.hit(day_limit, user_key, "model-requests-per-day"))
        self.assertFalse(web.limiter.limiter.hit(day_limit, user_key, "model-requests-per-day"))

    def test_each_user_has_a_separate_counter(self):
        minute_limit = RateLimitItemPerMinute(web.MODEL_REQUESTS_PER_MINUTE)
        user_a = web._rate_limit_key(_request_for_user("user-a"))
        user_b = web._rate_limit_key(_request_for_user("user-b"))

        for _ in range(web.MODEL_REQUESTS_PER_MINUTE):
            self.assertTrue(web.limiter.limiter.hit(minute_limit, user_a, "model-requests-per-minute"))
        self.assertFalse(web.limiter.limiter.hit(minute_limit, user_a, "model-requests-per-minute"))
        self.assertTrue(web.limiter.limiter.hit(minute_limit, user_b, "model-requests-per-minute"))


if __name__ == "__main__":
    unittest.main()


def test_summary_endpoint_allows_two_requests_then_returns_429(monkeypatch):
    web.limiter.reset()

    def fake_authenticated_user(request: Request):
        request.state.app_user_id = "endpoint-test-user"
        return {"id": "endpoint-test-user"}

    web.app.dependency_overrides[web._require_app_user] = fake_authenticated_user
    monkeypatch.setattr(
        web,
        "get_persistent_email_by_id",
        lambda user_id, email_id: {"id": email_id, "body": "An example email."},
    )
    monkeypatch.setattr(web, "summarize_one_email", lambda email: "A short test summary.")
    try:
        with TestClient(web.app) as client:
            results = [
                client.post(f"/api/dashboard/emails/test-{index}/summary")
                for index in range(3)
            ]
        assert [response.status_code for response in results] == [200, 200, 429]
        assert results[0].json() == {"summary": "A short test summary."}
        assert results[0].headers.get("x-request-id")
    finally:
        web.app.dependency_overrides.pop(web._require_app_user, None)
        web.limiter.reset()


def test_model_diagnostic_returns_synthetic_result(monkeypatch):
    monkeypatch.setattr(web, "run_model_classification_diagnostic", lambda: {
        "status": "success",
        "assignments": {"diagnostic-urgent-1": "Urgent Action"},
        "diagnostics": {
            "tool_call_count": 1,
            "assigned_count": 3,
            "missing_count": 0,
            "valid": True,
            "reason": "complete",
            "finish_reason": "tool_calls",
            "input_tokens": 200,
            "output_tokens": 80,
        },
        "duration_ms": 1000,
    })
    handler = web.model_classification_diagnostic
    while hasattr(handler, "__wrapped__"):
        handler = handler.__wrapped__
    request = Request({"type": "http", "headers": [], "method": "POST", "path": "/api/model/diagnostic"})

    response = handler(request, {"id": "diagnostic-test-user"})

    assert response["status"] == "success"
    assert response["diagnostics"]["tool_call_count"] == 1
