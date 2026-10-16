"""Tests for batched Gmail metadata retrieval."""

from backend import email_tool


class FakeBatch:
    def __init__(self):
        self.requests = []

    def add(self, request, request_id, callback):
        self.requests.append((request_id, request, callback))

    def execute(self):
        for request_id, request, callback in self.requests:
            callback(request_id, request, None)


class FakeMessages:
    def get(self, **kwargs):
        return kwargs


class FakeUsers:
    def __init__(self):
        self.message_resource = FakeMessages()

    def messages(self):
        return self.message_resource


class FakeService:
    def __init__(self):
        self.user_resource = FakeUsers()
        self.batches = []

    def users(self):
        return self.user_resource

    def new_batch_http_request(self):
        batch = FakeBatch()
        self.batches.append(batch)
        return batch


def test_metadata_requests_are_sent_in_bounded_batches(monkeypatch):
    monkeypatch.setattr(email_tool, "GMAIL_BATCH_SIZE", 2)
    service = FakeService()
    message_ids = [{"id": f"message-{number}"} for number in range(5)]

    results = email_tool._fetch_message_metadata_in_batches(service, message_ids)

    assert [len(batch.requests) for batch in service.batches] == [2, 2, 1]
    assert list(results) == [f"message-{number}" for number in range(5)]
    assert all(result["format"] == "metadata" for result in results.values())
    assert all(result["metadataHeaders"] == ["From", "Subject"] for result in results.values())
