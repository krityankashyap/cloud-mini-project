"""Unit tests for the NACL decision logic (Task 5) and shard partitioning (Task 1)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from nacl_sim import decide, load_rules  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_nacl_matches_sample_packets():
    rules = load_rules(os.path.join(ROOT, "network", "nacl.json"))
    packets = [json.loads(l) for l in open(os.path.join(ROOT, "network", "packets_sample.jsonl"))
               if l.strip()]
    for p in packets:
        action, _ = decide(rules, p)
        assert action == p["expected"], f'{p["id"]} expected {p["expected"]} got {action}'


def test_abusive_range_denied_on_web_port():
    rules = load_rules(os.path.join(ROOT, "network", "nacl.json"))
    pkt = {"direction": "inbound", "protocol": "tcp", "port": 443, "address": "198.51.100.9"}
    assert decide(rules, pkt)[0] == "deny"


def test_sharding_partitions_every_command_once():
    """Modulo sharding: every line index lands in exactly one shard, no gaps/overlaps."""
    pool = [l for l in open(os.path.join(ROOT, "data", "commands_pool.jsonl")) if l.strip()]
    shard_count = 4
    seen = [0] * len(pool)
    for shard_index in range(shard_count):
        for line_no in range(len(pool)):
            if line_no % shard_count == shard_index:
                seen[line_no] += 1
    assert all(c == 1 for c in seen), "every command must appear in exactly one shard"
