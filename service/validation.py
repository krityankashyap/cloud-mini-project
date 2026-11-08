"""Pure output-validation helpers for the service (no heavy deps, so unit-testable)."""
import json

TARGET_KEYS = ("intent", "date", "time", "place")


def extract_json(text):
    """Return the first balanced {...} parsed as a dict, or None."""
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def valid_output(obj, valid_intents):
    """True iff obj has exactly the four keys, a valid intent, and str|null slots."""
    if not isinstance(obj, dict) or set(obj.keys()) != set(TARGET_KEYS):
        return False
    if obj["intent"] not in valid_intents:
        return False
    for k in ("date", "time", "place"):
        if obj[k] is not None and not isinstance(obj[k], str):
            return False
    return True
