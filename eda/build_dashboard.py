"""Build the standalone dashboard from the saved CSV snapshot.

Run: python3 eda/build_dashboard.py
"""
from __future__ import annotations

import csv
import html
import io
import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SNAPSHOT = ROOT / "snapshot"


def read(name: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO((SNAPSHOT / name).read_text(encoding="utf-8-sig"))))


def mean(values: list[int]) -> float | None:
    return round(statistics.mean(values), 2) if values else None


def summarize(rows: list[dict[str, str]]) -> dict:
    vals: list[int] = []
    hourly = defaultdict(list)
    weekday = defaultdict(list)
    daily = defaultdict(list)
    for row in rows:
        time = datetime.fromisoformat(row["observed_at"])
        value = int(row["demand"])
        vals.append(value)
        hourly[("laborable" if time.weekday() < 5 else "fin_semana", time.hour)].append(value)
        weekday[time.weekday()].append(value)
        daily[time.date().isoformat()].append(value)
    bins = [(0, 100), (100, 200), (200, 400), (400, 600), (600, 1000), (1000, 1500), (1500, 2500)]
    return {
        "n": len(vals), "mean": mean(vals), "median": statistics.median(vals),
        "max": max(vals), "min": min(vals),
        "hourly": {kind: [mean(hourly[kind, hour]) for hour in range(24)] for kind in ("laborable", "fin_semana")},
        "weekday": [mean(weekday[day]) for day in range(7)],
        "daily": [[day, mean(daily[day])] for day in sorted(daily)],
        "histogram": [sum(low <= v < high for v in vals) for low, high in bins],
    }


def main() -> None:
    observations = read("observations.csv")
    station_rows = read("stations.csv")
    station_names = {row["station_id"]: row["station_name"] for row in station_rows}
    station_geo = {row["station_id"]: {"lat": float(row["latitude"]), "lon": float(row["longitude"]), "corridor": row["corridor"]} for row in station_rows}
    grouped = defaultdict(list)
    for row in observations:
        grouped[row["station_id"]].append(row)
    payload = {
        "names": station_names,
        "geo": station_geo,
        "stats": {"all": summarize(observations), **{key: summarize(rows) for key, rows in grouped.items()}},
        "range": [min(row["observed_at"] for row in observations), max(row["observed_at"] for row in observations)],
        "station_means": [[key, mean([int(row["demand"]) for row in grouped[key]])] for key in sorted(grouped)],
    }
    template = (ROOT / "dashboard_template.html").read_text(encoding="utf-8")
    page = template.replace("__EDA_DATA__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    (ROOT / "dashboard.html").write_text(page, encoding="utf-8")
    print(f"Dashboard listo: {ROOT / 'dashboard.html'}")


if __name__ == "__main__":
    main()
