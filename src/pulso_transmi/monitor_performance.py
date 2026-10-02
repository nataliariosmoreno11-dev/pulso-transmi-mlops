"""Monitorea cobertura, accuracy reciente y drift sin reaccionar a un solo ciclo."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import psycopg

from pulso_transmi.submit_current_cycle import db_url, load_env


def station_accuracy(rows):
    values = []
    for station in sorted({r[1] for r in rows}):
        selected = [r for r in rows if r[1] == station and r[5] is not None]
        error = sum(abs(float(r[5]) - float(r[4])) for r in selected)
        actual = sum(float(r[5]) for r in selected)
        values.append(max(0.0, 100 * (1 - error / actual)) if actual else 0.0)
    return float(np.mean(values)) if values else None


def choose_decision(coverage, recent_six, previous_six, drift, target):
    if coverage < 0.90 or recent_six is None:
        return "esperar"
    deteriorating = previous_six is not None and recent_six <= previous_six - 3.0
    if recent_six < target or (drift is not None and drift > 0.20 and deteriorating):
        return "reentrenar"
    return "conservar"


def main():
    load_env()
    target_accuracy = float(os.getenv("PULSO_TARGET_ACCURACY", "90"))
    metrics_path = Path("artifacts/lightgbm_demand.metrics.json")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
    model_version = metrics.get("model_version", os.getenv("PULSO_MODEL_VERSION", "unknown"))

    with psycopg.connect(db_url()) as conn, conn.cursor() as db:
        db.execute("""select p.id_ciclo,p.id_estacion,p.horizonte_minutos,p.instante_objetivo,
          p.demanda_predicha,o.demanda from public.predicciones_api p left join public.observaciones o
          on o.id_estacion=p.id_estacion and o.instante=p.instante_objetivo""")
        rows = db.fetchall()
        total = len(rows)
        evaluated = [r for r in rows if r[5] is not None]
        evaluation_coverage = len(evaluated) / total if total else 0.0

        db.execute("""select coalesce(sum(predicciones_recibidas)::float8 /
          nullif(sum(predicciones_esperadas),0),0) from public.entregas_api where estado='accepted'""")
        submission_coverage = float(db.fetchone()[0] or 0.0)

        stations = {}
        errors, actuals = [], []
        for station in sorted({r[1] for r in evaluated}):
            selected = [r for r in evaluated if r[1] == station]
            error = sum(abs(float(r[5]) - float(r[4])) for r in selected)
            actual = sum(float(r[5]) for r in selected)
            accuracy = max(0.0, 100 * (1 - error / actual)) if actual else 0.0
            stations[station] = {"wape": error / actual if actual else 0.0, "accuracy": accuracy, "n": len(selected)}
            errors.append(error); actuals.append(actual)
        mean_accuracy = float(np.mean([v["accuracy"] for v in stations.values()])) if stations else None
        global_wape = sum(errors) / sum(actuals) if actuals and sum(actuals) else None

        horizons = {}
        for horizon in sorted({r[2] for r in evaluated}):
            selected = [r for r in evaluated if r[2] == horizon]
            horizons[str(horizon)] = {"accuracy": station_accuracy(selected), "n": len(selected)}

        cycle_rows = []
        for cycle_id in {r[0] for r in rows}:
            selected = [r for r in rows if r[0] == cycle_id]
            if selected and all(r[5] is not None for r in selected):
                cycle_rows.append((max(r[3] for r in selected), cycle_id, station_accuracy(selected)))
        cycle_rows.sort(reverse=True)
        recent = cycle_rows[:6]
        previous = cycle_rows[6:12]
        recent_six = float(np.mean([r[2] for r in recent])) if len(recent) == 6 else None
        previous_six = float(np.mean([r[2] for r in previous])) if len(previous) == 6 else None

        db.execute("""with recent as (
            select avg(demanda)::float8 value from observaciones where instante >=
              (select max(instante)-interval '1 day' from observaciones)
          ), reference as (
            select avg(demanda)::float8 value from observaciones where instante <
              (select max(instante)-interval '1 day' from observaciones) and instante >=
              (select max(instante)-interval '8 days' from observaciones)
          ) select abs(recent.value-reference.value)/nullif(reference.value,0) from recent,reference""")
        drift = db.fetchone()[0]
        db.execute("select max(instante) from public.observaciones")
        latest_observation = db.fetchone()[0]

        decision = choose_decision(submission_coverage, recent_six, previous_six, drift, target_accuracy)
        details = {
            "stations": stations,
            "accuracy_by_horizon": horizons,
            "accuracy_last_6": recent_six,
            "accuracy_previous_6": previous_six,
            "complete_cycles": len(cycle_rows),
            "evaluation_coverage": evaluation_coverage,
            "policy": "six_complete_cycles_with_three_point_hysteresis",
        }
        db.execute("""insert into public.monitoreo_modelo(version_modelo,ciclos_totales,predicciones_totales,
          predicciones_evaluadas,cobertura,wape_global,accuracy_promedio_estaciones,drift_demanda,decision,detalle)
          values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
          (model_version,len({r[0] for r in rows}),total,len(evaluated),submission_coverage,
           global_wape,mean_accuracy,drift,decision,json.dumps(details)))
        conn.commit()

    trained_through = None
    trained_value = metrics.get("validation_through")
    if trained_value:
        trained_through = datetime.fromisoformat(trained_value.replace("Z", "+00:00"))
    data_advanced = trained_through is None or (latest_observation is not None and latest_observation > trained_through)
    retrain_needed = decision == "reentrenar" and data_advanced
    output_path = os.getenv("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"decision={decision}\n")
            output.write(f"drift={drift if drift is not None else ''}\n")
            output.write(f"retrain_needed={str(retrain_needed).lower()}\n")
    print(f"Monitoreo: cobertura_submission={submission_coverage:.1%}, accuracy_acumulada={mean_accuracy}, "
          f"accuracy_ultimos_6={recent_six}, accuracy_6_anteriores={previous_six}, drift={drift}, decisión={decision}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
