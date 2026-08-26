#!/usr/bin/env python3
"""Quick parser smoke test (no camera required)."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.pos_parser import parse_pos_file

path = Path(__file__).resolve().parents[1] / "data/pos/demo_transactions.csv"
txs = parse_pos_file(path)
assert len(txs) == 5, txs
assert txs[0].external_id == "B-1001"
assert txs[0].article_count == 5
assert txs[0].timestamp.hour == 22 and txs[0].timestamp.minute == 22
print("OK", len(txs), "transactions parsed")
