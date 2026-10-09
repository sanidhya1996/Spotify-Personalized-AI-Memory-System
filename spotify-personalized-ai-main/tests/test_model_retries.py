"""Why this file exists
=====================

Gemini often answers "503 - high demand". memory/model_client.py now tries
again a few times, waiting longer each time, so a short spike does not send
the event to the dead-letter queue. Other errors are not retried.
"""

import pytest

from memory import model_client


class FakeModels:
    """Fails with the given errors in order, then answers."""

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "answer"


class FakeClient:
    def __init__(self, errors):
        self.models = FakeModels(errors)


def test_a_busy_model_is_retried_until_it_answers():
    client = FakeClient([RuntimeError("503 UNAVAILABLE high demand")] * 2)
    waits = []
    assert model_client._ask_with_retries(client, "p", sleep=waits.append) == "answer"
    assert client.models.calls == 3 and waits == [2, 5]


# Both models busy through every retry: it gives up and the event is
# dead-lettered.
def test_it_gives_up_after_the_last_retry():
    client = FakeClient([RuntimeError("503 UNAVAILABLE")] * 20)
    with pytest.raises(RuntimeError):
        model_client._ask_with_retries(client, "p", sleep=lambda s: None)
    assert client.models.calls == 2 * (len(model_client.RETRY_WAITS_SECONDS) + 1)


def test_other_errors_are_not_retried():
    client = FakeClient([RuntimeError("400 INVALID_ARGUMENT bad key")])
    with pytest.raises(RuntimeError):
        model_client._ask_with_retries(client, "p", sleep=lambda s: None)
    assert client.models.calls == 1


# A model out of quota is not waited on: the fallback model is asked at once.
def test_out_of_quota_moves_to_the_fallback_model(monkeypatch):
    monkeypatch.setattr(model_client, "MODEL", "main-model")
    monkeypatch.setattr(model_client, "FALLBACK_MODEL", "spare-model")
    asked = []

    class Models:
        def generate_content(self, model, **kwargs):
            asked.append(model)
            if model == "main-model":
                raise RuntimeError("429 RESOURCE_EXHAUSTED You exceeded your current quota")
            return "answer"

    class Client:
        models = Models()

    waits = []
    assert model_client._ask_with_retries(Client(), "p", sleep=waits.append) == "answer"
    assert asked == ["main-model", "spare-model"] and waits == []


# Still busy after every retry: the fallback model is asked before giving up.
def test_a_model_busy_after_every_retry_moves_to_the_fallback(monkeypatch):
    monkeypatch.setattr(model_client, "MODEL", "main-model")
    monkeypatch.setattr(model_client, "FALLBACK_MODEL", "spare-model")
    asked = []

    class Models:
        def generate_content(self, model, **kwargs):
            asked.append(model)
            if model == "main-model":
                raise RuntimeError("503 UNAVAILABLE high demand")
            return "answer"

    class Client:
        models = Models()

    assert model_client._ask_with_retries(Client(), "p", sleep=lambda s: None) == "answer"
    assert asked.count("main-model") == len(model_client.RETRY_WAITS_SECONDS) + 1
    assert asked[-1] == "spare-model"
