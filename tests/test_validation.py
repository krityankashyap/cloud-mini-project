"""Unit tests for the service output-validation logic (Task 3 / Task 6)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "service"))

from validation import extract_json, valid_output  # noqa: E402

INTENTS = {"alarm_set", "alarm_query", "calendar_set", "calendar_query",
           "calendar_remove", "weather_query", "datetime_query", "recommendation_events"}


def test_extract_json_from_noisy_text():
    obj = extract_json('Sure! {"intent": "weather_query", "date": null, "time": null, "place": "london"} done')
    assert obj == {"intent": "weather_query", "date": None, "time": None, "place": "london"}


def test_extract_json_returns_none_for_non_json():
    assert extract_json("not json") is None


def test_valid_output_accepts_good_object():
    obj = {"intent": "alarm_set", "date": "tomorrow", "time": "seven am", "place": None}
    assert valid_output(obj, INTENTS) is True


def test_valid_output_rejects_bad_intent():
    obj = {"intent": "make_coffee", "date": None, "time": None, "place": None}
    assert valid_output(obj, INTENTS) is False


def test_valid_output_rejects_wrong_keys():
    obj = {"intent": "alarm_set", "date": None, "time": None}  # missing place
    assert valid_output(obj, INTENTS) is False


def test_fault_inject_path_is_invalid():
    # FAULT_INJECT replaces output with "not json"; the service must treat it as invalid.
    assert extract_json("not json") is None
