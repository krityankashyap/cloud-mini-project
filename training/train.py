"""Task 2 — sequence-level knowledge distillation of EleutherAI/pythia-70m.

The teacher's generated JSON (from Task 1) is the training target. We fine-tune
the student to produce that JSON given the command, i.e. we train on sequences:

    Command: <command>\nJSON: <teacher JSON>

Loss is computed only on the JSON completion (the prompt tokens are masked), which
is standard supervised fine-tuning for a causal LM. CPU-only.

Reads ALL hyper-parameters from training/config.yaml. Writes one loss value per
epoch to training/loss.csv and saves the model + tokenizer to the output dir.

Usage:
  python training/train.py                       # v1, first 1500 records
  python training/train.py --subset 50           # quick smoke test (first 50)
  python training/train.py --version v2          # v2 overrides (seed/epochs)
  python training/train.py --output training/artifacts/v1
"""
import argparse
import csv
import json
import os
import random

import numpy as np
import torch
import yaml
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForCausalLM, AutoTokenizer

TARGET_KEYS = ("intent", "date", "time", "place")


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)


def load_config(path, version):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if version == "v2":
        for k, v in (cfg.get("v2") or {}).items():
            cfg[k] = v
    return cfg


def build_target_json(rec):
    """Canonical teacher JSON string, fixed key order."""
    return json.dumps({k: rec[k] for k in TARGET_KEYS}, ensure_ascii=False)


class CommandDataset(Dataset):
    def __init__(self, records, tokenizer, template, max_len):
        self.examples = []
        eos = tokenizer.eos_token
        for rec in records:
            prompt = template.format(command=rec["text"])
            completion = build_target_json(rec) + eos
            p_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
            c_ids = tokenizer(completion, add_special_tokens=False)["input_ids"]
            input_ids = (p_ids + c_ids)[:max_len]
            # mask prompt tokens so loss only covers the JSON completion
            labels = ([-100] * len(p_ids) + c_ids)[:max_len]
            self.examples.append((input_ids, labels))

    def __len__(self):
        return len(self.examples)

    def __getitem__(self, i):
        return self.examples[i]


def make_collate(pad_id):
    def collate(batch):
        maxlen = max(len(x[0]) for x in batch)
        input_ids, labels, attn = [], [], []
        for ids, lab in batch:
            pad = maxlen - len(ids)
            input_ids.append(ids + [pad_id] * pad)
            labels.append(lab + [-100] * pad)
            attn.append([1] * len(ids) + [0] * pad)
        return (torch.tensor(input_ids), torch.tensor(labels), torch.tensor(attn))
    return collate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="training/config.yaml")
    ap.add_argument("--version", default="v1", choices=["v1", "v2"])
    ap.add_argument("--subset", type=int, default=None, help="train on the first N records only")
    ap.add_argument("--output", default=None, help="output dir (default training/artifacts/<version>)")
    ap.add_argument("--loss-csv", default="training/loss.csv")
    a = ap.parse_args()

    cfg = load_config(a.config, a.version)
    set_seed(cfg["seed"])
    torch.set_num_threads(max(1, os.cpu_count() or 1))

    output_dir = a.output or f"training/artifacts/{a.version}"
    os.makedirs(output_dir, exist_ok=True)

    # data
    train_file = cfg["train_file"]
    records = []
    with open(train_file, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    limit = a.subset if a.subset is not None else cfg["max_train_records"]
    records = records[:limit]
    print(f"[train] version={a.version} records={len(records)} "
          f"seed={cfg['seed']} epochs={cfg['epochs']} lr={cfg['learning_rate']} "
          f"batch={cfg['batch_size']} max_len={cfg['max_seq_length']}")

    # model + tokenizer
    tokenizer = AutoTokenizer.from_pretrained(cfg["base_model"])
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(cfg["base_model"])
    model.train()

    ds = CommandDataset(records, tokenizer, cfg["prompt_template"], cfg["max_seq_length"])
    loader = DataLoader(ds, batch_size=cfg["batch_size"], shuffle=True,
                        collate_fn=make_collate(tokenizer.pad_token_id),
                        generator=torch.Generator().manual_seed(cfg["seed"]))
    optim = torch.optim.AdamW(model.parameters(), lr=float(cfg["learning_rate"]))

    rows = [("epoch", "loss")]
    for epoch in range(1, cfg["epochs"] + 1):
        total, nb = 0.0, 0
        for input_ids, labels, attn in loader:
            optim.zero_grad()
            out = model(input_ids=input_ids, attention_mask=attn, labels=labels)
            out.loss.backward()
            optim.step()
            total += out.loss.item()
            nb += 1
        avg = total / max(1, nb)
        rows.append((epoch, round(avg, 6)))
        print(f"[train] epoch {epoch}/{cfg['epochs']} loss={avg:.6f}")

    with open(a.loss_csv, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    print(f"[train] wrote {a.loss_csv}")

    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)
    print(f"[train] saved model + tokenizer to {output_dir}")


if __name__ == "__main__":
    main()
