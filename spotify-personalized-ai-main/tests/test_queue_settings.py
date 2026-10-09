"""Why this file exists
=====================

The queue connects without a login locally (Redpanda in Docker) and with a
username and password when deployed (Redpanda Cloud, Confluent Cloud).
Checks memory/queue.py `connection_settings` picks the right one from the
environment.
"""

from memory import queue


def test_no_username_means_no_login(monkeypatch):
    monkeypatch.delenv("KAFKA_USERNAME", raising=False)
    assert queue.connection_settings() == {}


def test_a_username_switches_on_a_secure_login(monkeypatch):
    monkeypatch.setenv("KAFKA_USERNAME", "memory-app")
    monkeypatch.setenv("KAFKA_PASSWORD", "secret")
    monkeypatch.delenv("KAFKA_SASL_MECHANISM", raising=False)

    assert queue.connection_settings() == {
        "security_protocol": "SASL_SSL",
        "sasl_mechanism": "SCRAM-SHA-256",
        "sasl_plain_username": "memory-app",
        "sasl_plain_password": "secret",
    }


def test_the_mechanism_can_be_changed_for_confluent(monkeypatch):
    monkeypatch.setenv("KAFKA_USERNAME", "key")
    monkeypatch.setenv("KAFKA_SASL_MECHANISM", "PLAIN")
    assert queue.connection_settings()["sasl_mechanism"] == "PLAIN"


# A hosted Kafka is slow to join over the internet, so a pass waits longer.
def test_a_pass_waits_longer_for_a_hosted_kafka(monkeypatch):
    seen = {}

    class FakeConsumer:
        def __init__(self, *topics, **settings):
            seen.update(settings)

    import kafka
    monkeypatch.setattr(kafka, "KafkaConsumer", FakeConsumer)

    monkeypatch.delenv("KAFKA_USERNAME", raising=False)
    queue.consumer()
    assert seen["consumer_timeout_ms"] == queue.LOCAL_WAIT_MS

    monkeypatch.setenv("KAFKA_USERNAME", "memory-app")
    queue.consumer()
    assert seen["consumer_timeout_ms"] == queue.HOSTED_WAIT_MS
