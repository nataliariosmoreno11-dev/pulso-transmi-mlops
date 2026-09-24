"""Calcula cobertura, accuracy y una señal simple de drift desde Supabase."""
import json
import os
import numpy as np
import psycopg
from pulso_transmi.submit_current_cycle import MODEL_VERSION, db_url, load_env

def main():
    load_env()
    target_accuracy=float(os.getenv("PULSO_TARGET_ACCURACY", "90"))
    with psycopg.connect(db_url()) as conn, conn.cursor() as db:
        db.execute("""select p.id_ciclo,p.id_estacion,p.horizonte_minutos,p.demanda_predicha,o.demanda
          from public.predicciones_api p left join public.observaciones o
          on o.id_estacion=p.id_estacion and o.instante=p.instante_objetivo""")
        rows=db.fetchall()
        total=len(rows); evaluated=[r for r in rows if r[4] is not None]
        coverage=len(evaluated)/total if total else 0.0
        details={}; station_accuracy=[]; errors=[]; actuals=[]
        for station in sorted({r[1] for r in evaluated}):
            station_rows=[r for r in evaluated if r[1]==station]
            error=sum(abs(float(r[4])-float(r[3])) for r in station_rows)
            actual=sum(float(r[4]) for r in station_rows)
            wape=error/actual if actual else 0.0
            accuracy=max(0.0,100*(1-wape))
            details[station]={"wape":wape,"accuracy":accuracy,"n":len(station_rows)}
            station_accuracy.append(accuracy); errors.append(error); actuals.append(actual)
        global_wape=sum(errors)/sum(actuals) if actuals and sum(actuals) else None
        mean_accuracy=float(np.mean(station_accuracy)) if station_accuracy else None
        db.execute("""with recent as (
            select avg(demanda)::float8 value from observaciones where instante >= (select max(instante)-interval '1 day' from observaciones)
          ), reference as (
            select avg(demanda)::float8 value from observaciones
            where instante < (select max(instante)-interval '1 day' from observaciones)
              and instante >= (select max(instante)-interval '8 days' from observaciones)
          ) select abs(recent.value-reference.value)/nullif(reference.value,0) from recent,reference""")
        drift=db.fetchone()[0]
        if len(evaluated)<48: decision="esperar"
        elif (mean_accuracy is not None and mean_accuracy<target_accuracy) or (drift is not None and drift>0.20): decision="reentrenar"
        else: decision="conservar"
        db.execute("""insert into public.monitoreo_modelo(version_modelo,ciclos_totales,predicciones_totales,
          predicciones_evaluadas,cobertura,wape_global,accuracy_promedio_estaciones,drift_demanda,decision,detalle)
          values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
          (MODEL_VERSION,len({r[0] for r in rows}),total,len(evaluated),coverage,global_wape,mean_accuracy,drift,decision,json.dumps(details)))
        conn.commit()
    print(f"Monitoreo: evaluadas={len(evaluated)}/{total}, cobertura={coverage:.1%}, accuracy={mean_accuracy}, meta={target_accuracy}%, drift={drift}, decisión={decision}.")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
