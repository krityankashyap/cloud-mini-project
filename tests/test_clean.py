"""Unit tests for the Task 1 cleaning logic."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))

from clean import clean, extract_json_object, slot_ok  # noqa: E402


def test_extract_handles_prose_wrapped_json():
    obj = extract_json_object('Here: {"intent": "weather_query", "date": null, "time": null, "place": "london"}')
    assert obj["place"] == "london"


def test_slot_ok_requires_text_in_command():
    assert slot_ok("london", "what is the weather in london") is True
    assert slot_ok("berlin", "what is the weather in london") is False
    assert slot_ok(None, "anything") is True


def test_clean_reconciles_and_dedupes():
    records = [
        {"id": "C1", "text": "set an alarm for seven am",
         "teacher_raw": '{"intent": "alarm_set", "date": null, "time": "seven am", "place": null}'},
        {"id": "C2", "text": "weather in london",
         "teacher_raw": '{"intent": "weather_query", "date": null, "time": null, "place": "london"}'},
        {"id": "C3", "text": "no json here", "teacher_raw": "nonsense"},                       # (a)
        {"id": "C4", "text": "extra", "teacher_raw": '{"intent":"alarm_set","date":null,"time":null,"place":null,"x":1}'},  # (b)
        {"id": "C5", "text": "bad intent", "teacher_raw": '{"intent":"x","date":null,"time":null,"place":null}'},          # (c)
        {"id": "C6", "text": "weather in paris", "teacher_raw": '{"intent":"weather_query","date":null,"time":null,"place":"rome"}'},  # (d)
        {"id": "C7", "text": "weather in london",                                              # duplicate of C2
         "teacher_raw": '{"intent": "weather_query", "date": null, "time": null, "place": "london"}'},
    ]
    counts, lines = clean(records)
    assert counts["input"] == 7
    assert counts["a_invalid_json"] == 1
    assert counts["b_wrong_keys"] == 1
    assert counts["c_bad_intent"] == 1
    assert counts["d_bad_slot"] == 1
    assert counts["duplicates"] == 1
    assert counts["kept"] == 2
    # reconciliation identity
    assert counts["input"] == (counts["a_invalid_json"] + counts["b_wrong_keys"]
                               + counts["c_bad_intent"] + counts["d_bad_slot"]
                               + counts["duplicates"] + counts["kept"])
    assert len(lines) == 2


def test_clean_is_deterministic():
    records = [
        {"id": "C2", "text": "weather in london",
         "teacher_raw": '{"intent": "weather_query", "date": null, "time": null, "place": "london"}'},
        {"id": "C1", "text": "set an alarm for seven am",
         "teacher_raw": '{"intent": "alarm_set", "date": null, "time": "seven am", "place": null}'},
    ]
    _, lines1 = clean(records)
    _, lines2 = clean(records)
    assert lines1 == lines2
    # kept records are emitted in id order
    assert '"id": "C1"' in lines1[0] and '"id": "C2"' in lines1[1]
