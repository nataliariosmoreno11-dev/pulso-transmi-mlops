"""Entrena LightGBM con validación temporal para 15, 30, 45 y 60 minutos."""
import argparse
import json
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

FEATURES = ["station_id", "horizon_steps", "slot", "day_of_week", "is_weekend", "lag_available", "lag_15m", "lag_1h", "lag_2h", "lag_day", "lag_2days", "lag_week"]


def score(frame, prediction):
    scored = frame[["station_id", "demand"]].copy()
    scored["error"] = np.abs(scored["demand"].to_numpy() - prediction)
    grouped = scored.groupby("station_id", observed=True).agg(error=("error", "sum"), actual=("demand", "sum"))
    accuracy = (100 * (1 - grouped["error"] / grouped["actual"])).clip(lower=0)
    return {
        "accuracy_mean_12_stations": round(float(accuracy.mean()), 4),
        "accuracy_by_station": {str(k): round(float(v), 4) for k, v in accuracy.items()},
        "wape_global": round(float(scored["error"].sum() / scored["demand"].sum()), 6),
    }


def make_features(observations):
    observations = observations.sort_values(["station_id", "observed_at"]).copy()
    observations["station_id"] = observations["station_id"].astype("category")
    grouped = observations.groupby("station_id", observed=True)["demand"]
    chunks = []
    for horizon in range(1, 5):
        part = observations[["observed_at", "station_id", "demand"]].copy()
        part["horizon_steps"] = horizon
        local_time = part["observed_at"].dt
        part["slot"] = local_time.hour * 4 + local_time.minute // 15
        part["day_of_week"] = local_time.dayofweek
        part["is_weekend"] = (part["day_of_week"] >= 5).astype("int8")
        # La última observación usada es anterior al origen por 30 minutos.
        for name, periods in (("lag_available", horizon + 2), ("lag_15m", horizon + 3), ("lag_1h", horizon + 6), ("lag_2h", horizon + 10), ("lag_day", 96), ("lag_2days", 192), ("lag_week", 672)):
            part[name] = grouped.shift(periods).to_numpy()
        chunks.append(part)
    return pd.concat(chunks, ignore_index=True).dropna(subset=FEATURES)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("eda/snapshot/observations.csv"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/lightgbm_demand.joblib"))
    args = parser.parse_args()
    raw = pd.read_csv(args.input, dtype={"station_id": "string"}, parse_dates=["observed_at"])
    if raw.duplicated(["station_id", "observed_at"]).any():
        raise ValueError("Hay observaciones duplicadas por estación y fecha")
    if raw[["observed_at", "station_id", "demand"]].isna().any().any():
        raise ValueError("Hay datos faltantes en columnas requeridas")
    frame = make_features(raw)
    cutoff = raw["observed_at"].max() - pd.Timedelta(days=7)
    train = frame.loc[frame["observed_at"] <= cutoff]
    valid = frame.loc[frame["observed_at"] > cutoff]
    model = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.05, num_leaves=31, min_child_samples=60, random_state=42, n_jobs=2, verbosity=-1)
    model.fit(train[FEATURES], train["demand"])
    prediction = np.clip(model.predict(valid[FEATURES]), 0, None)
    metrics = {
        "input": str(args.input), "source_rows": int(len(raw)), "station_count": int(raw["station_id"].nunique()),
        "train_targets": int(len(train)), "validation_targets": int(len(valid)),
        "train_through": cutoff.isoformat(), "validation_through": raw["observed_at"].max().isoformat(),
        "horizons_minutes": [15, 30, 45, 60], "assumed_observation_delay_minutes": 30,
        "lightgbm": score(valid, prediction), "previous_day_baseline": score(valid, valid["lag_day"].to_numpy()),
        "lightgbm_accuracy_by_horizon_minutes": {str(h * 15): score(valid.loc[valid["horizon_steps"] == h], prediction[valid["horizon_steps"].to_numpy() == h])["accuracy_mean_12_stations"] for h in range(1, 5)},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "features": FEATURES, "metrics": metrics}, args.output)
    args.output.with_suffix(".metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
