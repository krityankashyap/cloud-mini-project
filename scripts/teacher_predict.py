"""Generate teacher predictions on validation.jsonl for Task 2 step 6.

Uses the SAME prompt and settings as Task 1 (phi3.5, temperature 0, seed 42) so the
teacher score report (results/teacher_validation.json) is comparable.

  python scripts/teacher_predict.py --out results/teacher_preds.jsonl

Then score with:
  python tools/score.py results/teacher_preds.jsonl data/validation.jsonl \
      --out results/teacher_validation.json
"""
import argparse
import json
import os

import requests

TARGET_KEYS = ("intent", "date", "time", "place")


def extract_json(text):
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
                            return json.dumps(obj, ensure_ascii=False)
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return text.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/validation.jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--prompt", default="prompts/teacher_prompt.txt")
    ap.add_argument("--host", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    ap.add_argument("--model", default=os.environ.get("TEACHER_MODEL", "phi3.5"))
    a = ap.parse_args()

    template = open(a.prompt, encoding="utf-8").read()
    gold = [json.loads(l) for l in open(a.input, encoding="utf-8") if l.strip()]
    with open(a.out, "w", encoding="utf-8") as fout:
        for g in gold:
            prompt = template.replace("{command}", g["text"])
            r = requests.post(f"{a.host.rstrip('/')}/api/generate",
                              json={"model": a.model, "prompt": prompt, "stream": False,
                                    "options": {"temperature": 0, "seed": 42,
                                                "num_predict": 80,
                                                "stop": ["\n", "\nCommand:", "Command:"]}},
                              timeout=300)
            r.raise_for_status()
            prediction = extract_json(r.json()["response"].strip())
            fout.write(json.dumps({"id": g["id"], "prediction": prediction}, ensure_ascii=False) + "\n")
    print(f"wrote {a.out} ({len(gold)} predictions)")


if __name__ == "__main__":
    main()
