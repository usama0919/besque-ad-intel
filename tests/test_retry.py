import pytest
from src import retry


def test_with_retry_succeeds_first_try():
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        return "ok"
    assert retry.with_retry(fn, attempts=3, delay=0) == "ok"
    assert calls["n"] == 1


def test_with_retry_succeeds_after_failures():
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "recovered"
    assert retry.with_retry(fn, attempts=3, delay=0) == "recovered"
    assert calls["n"] == 3


def test_with_retry_raises_after_all_attempts():
    def fn():
        raise RuntimeError("always fails")
    with pytest.raises(RuntimeError):
        retry.with_retry(fn, attempts=3, delay=0)


def test_try_or_skip_returns_default_on_failure():
    def fn():
        raise RuntimeError("boom")
    assert retry.try_or_skip(fn, default=[]) == []


def test_try_or_skip_returns_value_on_success():
    assert retry.try_or_skip(lambda: "value", default=None) == "value"
