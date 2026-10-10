"""
Полная сборка: синтетические данные → обучение → метрики.

    python -m api.analytics.build            # данные + модель
    python -m api.analytics.build --train    # только переобучить модель
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time

from .config import DB_PATH
from .forecast import train_and_evaluate
from .generator import generate

log = logging.getLogger("analytics.build")


def main(argv: list) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
    t0 = time.time()
    if "--train" not in argv:
        log.info("Генерация синтетических данных → %s", DB_PATH)
        log.info("Итог: %s", generate())
    log.info("Обучение и оценка модели спроса")
    conn = sqlite3.connect(DB_PATH)
    metrics = train_and_evaluate(conn)
    conn.close()
    log.info("Метрики:\n%s", json.dumps(
        {k: metrics[k] for k in ("wape_pct", "interval_p10_p90_coverage_pct", "cold_start_24h", "best_iteration")},
        ensure_ascii=False, indent=2,
    ))
    log.info("Готово за %.0f с", time.time() - t0)


if __name__ == "__main__":
    main(sys.argv[1:])
