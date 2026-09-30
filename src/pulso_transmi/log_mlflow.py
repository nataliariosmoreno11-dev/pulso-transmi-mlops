"""Registra el resultado del torneo como una ejecución reproducible de MLflow."""
from __future__ import annotations

import json
import os
from pathlib import Path

import mlflow


def main() -> int:
    report_path = Path("artifacts/tournament.latest.json")
    metrics_path = Path("artifacts/lightgbm_demand.metrics.json")
    if not report_path.exists():
        raise RuntimeError("No existe el reporte del torneo")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
    tracking = os.getenv("MLFLOW_TRACKING_URI", "sqlite:///artifacts/mlflow.db")
    mlflow.set_tracking_uri(tracking)
    client = mlflow.MlflowClient()
    name = os.getenv("MLFLOW_EXPERIMENT_NAME", "pulso-transmi-tournament")
    experiment = client.get_experiment_by_name(name)
    if experiment is None:
        experiment_id = client.create_experiment(name)
    else:
        experiment_id = experiment.experiment_id
    with mlflow.start_run(experiment_id=experiment_id, run_name=report.get("model_version")):
        mlflow.set_tags({"git_commit": os.getenv("GITHUB_SHA", "local")[:40], "data_through": str(report.get("data_through")), "winner": str(report.get("winner"))})
        mlflow.log_params({"validation_start": str(report.get("validation_start")), "minimum_improvement_points": report.get("minimum_improvement_points"), "promoted": report.get("promoted"), "refreshed": report.get("refreshed")})
        values = {"incumbent_accuracy": report.get("incumbent_accuracy"), "winner_accuracy": report.get("winner_accuracy"), "validation_accuracy": metrics.get("lightgbm", {}).get("accuracy_mean_12_stations"), "validation_wape": metrics.get("lightgbm", {}).get("wape_global"), "source_rows": metrics.get("source_rows")}
        mlflow.log_metrics({key: float(value) for key, value in values.items() if value is not None})
    print(f"Ejecución registrada en MLflow: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
