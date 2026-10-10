#!/usr/bin/env bash
# Qayta o'qitish + aniqlik tekshiruvi
set -euo pipefail
cd "$(dirname "$0")/.."
echo "=== Train ==="
python3 train_model.py
echo ""
echo "=== Evaluate ==="
python3 check_prediction_accuracy.py
echo ""
echo "=== Tests ==="
python3 -m pytest tests/ -q
