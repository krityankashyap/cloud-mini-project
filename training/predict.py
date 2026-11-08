"""Generate predictions.jsonl for tools/score.py from a HuggingFace causal LM.

Used in Task 2 step 6 for two of the three models:
  - the original untrained Pythia-70M   (--model EleutherAI/pythia-70m)
  - the trained student                 (--model training/artifacts/v1)

For each gold command it builds the prompt, generates greedily, extracts the first
balanced JSON object from the completion, and writes
  {"id": "...", "prediction": "<clean json or raw text>"}
exactly as tools/score.py expects (score.py itself does no extraction).

Usage:
  python training/predict.py --model training/artifacts/v1 \
      --input data/validation.jsonl --out results/student_v1_preds.jsonl
"""
import argparse
import json

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

TARGET_KEYS = ("intent", "date", "time", "place")
TEMPLATE = "Command: {command}\nJSON: "


def extract_json(text):
    """First balanced {...} re-serialised canonically, or the raw text if none parses."""
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
    ap.add_argument("--model", required=True)
    ap.add_argument("--input", default="data/validation.jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-new-tokens", type=int, default=64)
    a = ap.parse_args()

    tok = AutoTokenizer.from_pretrained(a.model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(a.model)
    model.eval()

    gold = [json.loads(l) for l in open(a.input, encoding="utf-8") if l.strip()]
    with open(a.out, "w", encoding="utf-8") as fout:
        for g in gold:
            prompt = TEMPLATE.format(command=g["text"])
            ids = tok(prompt, return_tensors="pt")
            with torch.no_grad():
                out = model.generate(**ids, max_new_tokens=a.max_new_tokens,
                                     do_sample=False, pad_token_id=tok.pad_token_id,
                                     eos_token_id=tok.eos_token_id)
            completion = tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)
            prediction = extract_json(completion)
            fout.write(json.dumps({"id": g["id"], "prediction": prediction}, ensure_ascii=False) + "\n")
    print(f"wrote {a.out} ({len(gold)} predictions)")


if __name__ == "__main__":
    main()
