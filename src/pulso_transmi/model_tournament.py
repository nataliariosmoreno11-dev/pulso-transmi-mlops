"""Torneo temporal de modelos y promoción segura del mejor candidato."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import psycopg

from ml.train_lightgbm import FEATURES, make_features, score
from pulso_transmi.submit_current_cycle import db_url, load_env

CANDIDATES = (
    ("l2_balanced", {"objective": "regression", "n_estimators": 600, "learning_rate": 0.03, "num_leaves": 127, "min_child_samples": 40}),
    ("l1_robust", {"objective": "regression_l1", "n_estimators": 700, "learning_rate": 0.03, "num_leaves": 127, "min_child_samples": 30}),
    ("l1_compact", {"objective": "regression_l1", "n_estimators": 700, "learning_rate": 0.03, "num_leaves": 63, "min_child_samples": 30}),
    ("l2_compact", {"objective": "regression", "n_estimators": 700, "learning_rate": 0.025, "num_leaves": 63, "min_child_samples": 25}),
)


def load_observations() -> pd.DataFrame:
    with psycopg.connect(db_url()) as connection, connection.cursor() as cursor:
        cursor.execute("select id_estacion, instante, demanda from public.observaciones order by id_estacion, instante")
        rows = cursor.fetchall()
    frame = pd.DataFrame(rows, columns=["station_id", "observed_at", "demand"])
    frame["station_id"] = frame["station_id"].astype("string")
    frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True)
    return frame


def prepare(raw: pd.DataFrame) -> pd.DataFrame:
    frame = make_features(raw)
    categories = (("horizon_steps", [1, 2, 3, 4]), ("slot", list(range(96))), ("day_of_week", list(range(7))), ("is_weekend", [0, 1]))
    for column, values in categories:
        frame[column] = pd.Categorical(frame[column], categories=values)
    return frame


def accuracy(frame: pd.DataFrame, prediction: np.ndarray) -> float:
    return score(frame, np.clip(prediction, 0, None))["accuracy_mean_12_stations"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, default=Path("artifacts/lightgbm_demand.joblib"))
    parser.add_argument("--report", type=Path, default=Path("artifacts/tournament.latest.json"))
    parser.add_argument("--validation-days", type=int, default=7)
    parser.add_argument("--minimum-improvement", type=float, default=0.10, help="Puntos porcentuales")
    args = parser.parse_args()
    load_env()
    raw = load_observations()
    if raw.empty or raw.station_id.nunique() != 12:
        raise RuntimeError("El torneo requiere observaciones de las 12 estaciones")
    frame = prepare(raw)
    cutoff = raw.observed_at.max() - pd.Timedelta(days=args.validation_days)
    train = frame[frame.observed_at <= cutoff].copy()
    valid = frame[frame.observed_at > cutoff].copy()
    if train.empty or valid.empty:
        raise RuntimeError("No hay historia suficiente para la validación temporal")

    incumbent = joblib.load(args.model)
    incumbent_prediction = incumbent["model"].predict(valid[incumbent["features"]])
    incumbent_accuracy = accuracy(valid, incumbent_prediction)
    results = [{"name": "incumbent", "accuracy": incumbent_accuracy, "promoted": False}]
    trained: dict[str, lgb.LGBMRegressor] = {}

    station_totals = train.groupby("station_id", observed=True).demand.sum()
    equal_station_weight = train.station_id.map(1 / station_totals).astype(float)
    equal_station_weight /= equal_station_weight.mean()

    for name, params in CANDIDATES:
        model = lgb.LGBMRegressor(**params, random_state=42, n_jobs=2, verbosity=-1)
        weights = equal_station_weight if name == "l1_robust" else None
        model.fit(train[FEATURES], train.demand, sample_weight=weights)
        candidate_accuracy = accuracy(valid, model.predict(valid[FEATURES]))
        results.append({"name": name, "accuracy": candidate_accuracy, "parameters": params, "promoted": False})
        trained[name] = model

    winner = max(results[1:], key=lambda item: item["accuracy"])
    promoted = winner["accuracy"] >= incumbent_accuracy + args.minimum_improvement
    model_version = incumbent.get("model_version", "lightgbm-demand:2.0")
    if promoted:
        winner_name = winner["name"]
        winner_params = dict(next(params for name, params in CANDIDATES if name == winner_name))
        champion = lgb.LGBMRegressor(**winner_params, random_state=42, n_jobs=2, verbosity=-1)
        full_totals = frame.groupby("station_id", observed=True).demand.sum()
        full_weight = frame.station_id.map(1 / full_totals).astype(float)
        full_weight /= full_weight.mean()
        champion.fit(frame[FEATURES], frame.demand, sample_weight=full_weight if winner_name == "l1_robust" else None)
        model_version = "lightgbm-tournament:" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        winner["promoted"] = True
        metrics = {
            "source_rows": int(len(raw)), "station_count": int(raw.station_id.nunique()),
            "train_targets": int(len(train)), "validation_targets": int(len(valid)),
            "train_through": cutoff.isoformat(), "validation_through": raw.observed_at.max().isoformat(),
            "lightgbm": score(valid, np.clip(trained[winner_name].predict(valid[FEATURES]), 0, None)),
            "tournament_winner": winner_name, "model_version": model_version,
        }
        joblib.dump({"model": champion, "features": FEATURES, "metrics": metrics, "model_version": model_version}, args.model)
        args.model.with_suffix(".metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "calculated_at": datetime.now(timezone.utc).isoformat(), "data_through": raw.observed_at.max().isoformat(),
        "validation_start": cutoff.isoformat(), "minimum_improvement_points": args.minimum_improvement,
        "incumbent_accuracy": incumbent_accuracy, "winner": winner["name"], "winner_accuracy": winner["accuracy"],
        "promoted": promoted, "model_version": model_version, "results": results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
