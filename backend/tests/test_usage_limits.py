"""Small offline unit tests for request limits; no Gmail or OpenRouter calls."""

import unittest

from fastapi import Request
from limits import RateLimitItemPerDay, RateLimitItemPerMinute
from pydantic import ValidationError

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

    def test_chat_text_is_limited_to_4000_characters(self):
        web.UserRequest(text="x" * web.MAX_CHAT_TEXT_LENGTH)
        with self.assertRaises(ValidationError):
            web.UserRequest(text="x" * (web.MAX_CHAT_TEXT_LENGTH + 1))

    def test_user_can_make_two_model_requests_per_minute(self):
        user_key = web._rate_limit_key(_request_for_user("user-a"))
        minute_limit = RateLimitItemPerMinute(web.MODEL_REQUESTS_PER_MINUTE)

        self.assertTrue(web.limiter.limiter.hit(minute_limit, user_key, "model-requests-per-minute"))
        self.assertTrue(web.limiter.limiter.hit(minute_limit, user_key, "model-requests-per-minute"))
        self.assertFalse(web.limiter.limiter.hit(minute_limit, user_key, "model-requests-per-minute"))

    def test_user_can_make_ten_model_requests_per_day(self):
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
