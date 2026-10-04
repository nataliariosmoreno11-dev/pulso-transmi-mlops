"""Compare VAR v2 candidates on chronological cycles without future observations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from pulso_transmi.model_tournament import load_observations
from pulso_transmi.submit_current_cycle import load_env, multivariate_autoregressive_predictions


CANDIDATES = {
    "var-v2": (3, 32, 1.0),
    "short-2": (2, 16, 1.0),
    "short-3": (3, 16, 1.0),
    "medium-2": (2, 24, 1.0),
    "medium-3": (3, 24, 1.0),
    "smooth-2": (2, 32, 10.0),
    "smooth-3": (3, 32, 10.0),
    "long-2": (2, 48, 1.0),
    "long-3": (3, 48, 1.0),
    "long-4": (4, 48, 1.0),
    "weak-ridge": (3, 32, 0.1),
    "strong-ridge": (3, 32, 100.0),
}


def cycle_accuracy(actual, predicted):
    errors = np.abs(actual - predicted).sum(axis=0)
    totals = actual.sum(axis=0)
    values = np.maximum(0, 100 * (1 - np.divide(errors, totals, out=np.ones_like(errors), where=totals > 0)))
    return float(values.mean())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--holdout-hours", type=int, default=24)
    parser.add_argument("--report", type=Path, default=Path("artifacts/var-backtest.latest.json"))
    args = parser.parse_args()
    if not 0 < args.holdout_hours < args.hours:
        parser.error("holdout-hours must be between zero and hours")
    load_env()
    raw = pd.read_csv(args.input, dtype={"station_id": "string"}) if args.input else load_observations()
    raw["observed_at"] = pd.to_datetime(raw.observed_at, utc=True)
    raw["station_id"] = raw.station_id.astype("string")
    raw = raw.sort_values("observed_at")
    pivot = raw.pivot(index="observed_at", columns="station_id", values="demand").sort_index()
    stations = list(pivot.columns)
    end = raw.observed_at.max().floor("h") - pd.Timedelta(hours=1)
    split = end - pd.Timedelta(hours=args.holdout_hours)
    scores = {name: [] for name in CANDIDATES}
    skipped = []
    for origin in pd.date_range(end - pd.Timedelta(hours=args.hours - 1), end, freq="h"):
        targets = pd.date_range(origin + pd.Timedelta(minutes=15), periods=4, freq="15min")
        actual = pivot.reindex(targets).to_numpy(dtype=float)
        if len(stations) != 12 or not np.isfinite(actual).all():
            skipped.append(origin.isoformat())
            continue
        available = origin - pd.Timedelta(minutes=30)
        history = raw[(raw.observed_at <= available) & (raw.observed_at >= available - pd.Timedelta(days=2))]
        latest = history.groupby("station_id", observed=True).tail(1).set_index("station_id").demand
        if len(latest) != 12:
            skipped.append(origin.isoformat())
            continue
        frame = pd.DataFrame([
            {"station_id": station, "target_at": target, "horizon_steps": h,
             "lag_available": float(latest[station]), "lag_15m": float(latest[station])}
            for h, target in enumerate(targets, 1) for station in stations
        ])
        for name, (order, window, ridge) in CANDIDATES.items():
            predicted = multivariate_autoregressive_predictions(history, frame, order, window, ridge).reshape(4, 12)
            scores[name].append({"origin": origin.isoformat(), "partition": "holdout" if origin > split else "tuning",
                                "accuracy": cycle_accuracy(actual, predicted)})
        print(f"{origin.isoformat()} var-v2={scores['var-v2'][-1]['accuracy']:.2f}%", flush=True)
    results = {}
    for name, cycles in scores.items():
        results[name] = {"parameters": dict(zip(("order", "window", "ridge"), CANDIDATES[name])), "cycles": cycles}
        for partition in ("tuning", "holdout"):
            values = [r["accuracy"] for r in cycles if r["partition"] == partition]
            if not values:
                raise RuntimeError(f"No complete cycles in {partition}; no promotion permitted")
            results[name][partition] = {"cycles": len(values), "mean": float(np.mean(values)),
                                       "minimum": min(values), "cycles_below_80": sum(v < 80 for v in values)}
    winner = max(results, key=lambda name: results[name]["tuning"]["mean"])
    candidate = results[winner]["holdout"]
    baseline = results["var-v2"]["holdout"]
    eligible = (winner != "var-v2" and candidate["cycles"] >= 6
                and candidate["mean"] >= max(80.0, baseline["mean"] + 0.5)
                and candidate["minimum"] >= baseline["minimum"]
                and candidate["cycles_below_80"] <= baseline["cycles_below_80"])
    report = {"data_end": raw.observed_at.max().isoformat(), "publication_lag_minutes": 30,
              "selection_partition": "tuning", "selected": winner, "skipped_incomplete_cycles": skipped,
              "results": results, "eligible_for_review": eligible, "production_changed": False,
              "scope": "Simulated hourly origins; complete observed targets only, not official leaderboard accuracy"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"selected_on_tuning": winner, "summary": {k: {p: v[p] for p in ("tuning", "holdout")} for k, v in results.items()}}, indent=2))


if __name__ == "__main__":
    main()
