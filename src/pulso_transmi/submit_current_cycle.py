"""Pipeline idempotente de sincronización, inferencia y submission."""
from __future__ import annotations
import hashlib, json, math, os, time, urllib.error, urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
import joblib, numpy as np, pandas as pd, psycopg

BASE=os.getenv("PULSO_API_URL","https://pulso-transmi.72-60-245-2.sslip.io").rstrip("/")
MODEL_PATH=Path(os.getenv("PULSO_MODEL_PATH","artifacts/lightgbm_demand.joblib"))
MODEL_VERSION=os.getenv("PULSO_MODEL_VERSION","lightgbm-demand:2.0")

def load_env():
    p=Path(".env")
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k,v=line.split("=",1); os.environ.setdefault(k.strip(),v.strip().strip("'").strip('"'))

def db_url():
    uri=os.getenv("PULSO_DATABASE_URL") or os.getenv("DATABASE_URL")
    password=os.getenv("PULSO_DB_PASSWORD")
    if not uri: raise RuntimeError("Falta PULSO_DATABASE_URL")
    if password and "[YOUR-PASSWORD]" in uri: uri=uri.replace("[YOUR-PASSWORD]",quote(password,safe=""))
    elif password:
        p=urlsplit(uri)
        host=p.hostname+(f":{p.port}" if p.port else "")
        uri=urlunsplit((p.scheme,f"{quote(p.username or '',safe='.')}:{quote(password,safe='')}@{host}",p.path,p.query,p.fragment))
    if "[YOUR-PASSWORD]" in uri: raise RuntimeError("Falta PULSO_DB_PASSWORD")
    return uri

def api(path,payload=None,key=None):
    token=os.getenv("PULSO_API_KEY")
    if not token: raise RuntimeError("Falta PULSO_API_KEY")
    body=None if payload is None else json.dumps(payload,separators=(",",":"),allow_nan=False).encode()
    headers={"Authorization":f"Bearer {token}","User-Agent":"pulso-transmi-mlops/1.0"}
    if body is not None: headers["Content-Type"]="application/json"
    if key: headers["Idempotency-Key"]=key
    req=urllib.request.Request(BASE+path,data=body,headers=headers,method="POST" if body else "GET")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req,timeout=30) as response: return response.status,json.load(response)
        except urllib.error.HTTPError as exc:
            raw=exc.read().decode(errors="replace")
            try: detail=json.loads(raw)
            except json.JSONDecodeError: detail={"detail":raw[:300]}
            if exc.code==404 and path=="/v1/forecast-cycles/current": return 404,detail
            if exc.code not in (429,500,502,503,504) or attempt==3:
                raise RuntimeError(f"API {path} HTTP {exc.code}: {detail}") from exc
            time.sleep(2**attempt)
        except (TimeoutError,urllib.error.URLError) as exc:
            if attempt==3: raise RuntimeError(f"API {path} no respondió") from exc
            time.sleep(2**attempt)
    raise AssertionError

def sync_stream(conn):
    with conn.cursor() as db:
        db.execute("select cursor from public.estado_ingesta where recurso='observations'")
        state = db.fetchone()
    cursor = state[0] if state else None
    total=0
    for _ in range(1000):
        path="/v1/stream/observations?limit=500"+(f"&cursor={quote(cursor,safe='')}" if cursor else "")
        _,page=api(path); rows=page.get("data",[])
        with conn.cursor() as db:
            db.executemany("""insert into public.observaciones(id_estacion,instante,demanda) values(%s,%s,%s)
              on conflict(id_estacion,instante) do update set demanda=excluded.demanda,recibido_en=now()
              where observaciones.demanda is distinct from excluded.demanda""",
              [(r["station_id"],r["observed_at"],r["demand"]) for r in rows])
        conn.commit(); total+=len(rows)
        nxt=page.get("next_cursor")
        if nxt is None:
            with conn.cursor() as db:
                db.execute("""insert into public.estado_ingesta(recurso,ultimo_instante,cursor,actualizado_en)
                  select 'observations',max(instante),%s,now() from public.observaciones
                  on conflict(recurso) do update set ultimo_instante=excluded.ultimo_instante,
                  cursor=excluded.cursor,actualizado_en=excluded.actualizado_en""", (cursor,))
            conn.commit()
            return total
        if nxt==cursor: raise RuntimeError("Cursor repetido por la API")
        cursor=nxt
    raise RuntimeError("Stream demasiado extenso")

def load_history(conn,cutoff):
    with conn.cursor() as db:
        db.execute("select id_estacion,instante,demanda from public.observaciones where instante<=%s order by id_estacion,instante",(cutoff,))
        rows=db.fetchall()
    f=pd.DataFrame(rows,columns=["station_id","observed_at","demand"])
    f["station_id"]=f["station_id"].astype("string"); f["observed_at"]=pd.to_datetime(f["observed_at"],utc=True)
    return f

def build_features(history,cycle,names):
    values={(str(r.station_id),pd.Timestamp(r.observed_at)):float(r.demand) for r in history.itertuples(index=False)}
    stations=sorted(history.station_id.astype(str).unique()); rows=[]
    for target in cycle["targets"]:
        station=str(target["station_id"]); target_at=pd.Timestamp(target["target_at"])
        horizon=int(target.get("horizon_minutes") or (target_at-pd.Timestamp(cycle["origin_at"])).total_seconds()/60); h=int(horizon//15)
        if horizon not in (15,30,45,60): raise RuntimeError(f"Horizonte no soportado: {horizon}")
        times={"lag_available":target_at-pd.Timedelta(minutes=15*(h+2)),"lag_15m":target_at-pd.Timedelta(minutes=15*(h+3)),
               "lag_1h":target_at-pd.Timedelta(minutes=15*(h+6)),"lag_2h":target_at-pd.Timedelta(minutes=15*(h+10)),
               "lag_day":target_at-pd.Timedelta(days=1),"lag_2days":target_at-pd.Timedelta(days=2),"lag_week":target_at-pd.Timedelta(days=7)}
        missing=[n for n,t in times.items() if (station,t) not in values]
        if missing: raise RuntimeError(f"Faltan rezagos para {station} {target_at}: {missing}")
        rows.append({"station_id":station,"horizon_steps":h,"slot":target_at.hour*4+target_at.minute//15,
          "day_of_week":target_at.dayofweek,"is_weekend":int(target_at.dayofweek>=5),
          **{n:values[(station,t)] for n,t in times.items()},"target_at":target_at,"horizon_minutes":horizon})
    frame=pd.DataFrame(rows); frame["station_id"]=pd.Categorical(frame["station_id"],categories=stations)
    for column, categories in (("horizon_steps", [1, 2, 3, 4]), ("slot", list(range(96))), ("day_of_week", list(range(7))), ("is_weekend", [0, 1])):
        frame[column] = pd.Categorical(frame[column], categories=categories)
    if set(names)-set(frame): raise RuntimeError(f"Variables desconocidas: {sorted(set(names)-set(frame))}")
    return frame

def validate_predictions(cycle,predictions):
    expected={(str(t["station_id"]),pd.Timestamp(t["target_at"])) for t in cycle["targets"]}
    actual={(p["station_id"],pd.Timestamp(p["target_at"])) for p in predictions}
    if len(predictions)!=int(cycle["expected_predictions"]) or len(actual)!=len(predictions) or actual!=expected:
        raise RuntimeError("Las predicciones no coinciden exactamente con los targets")
    if any(not math.isfinite(float(p["value"])) or float(p["value"])<0 for p in predictions):
        raise RuntimeError("Predicción negativa, NaN o infinita")

def already_sent(conn,cycle_id):
    with conn.cursor() as db:
        db.execute("select 1 from public.entregas_api where id_ciclo=%s and estado='accepted'",(cycle_id,))
        return db.fetchone() is not None

def save(conn,cycle,predictions,receipt,run_id,key):
    with conn.transaction(),conn.cursor() as db:
        db.execute("""insert into public.entregas_api(id_ciclo,version_modelo,id_submission,id_ejecucion_cliente,clave_idempotencia,
          estado,intento,corte_datos,cierre_ciclo,predicciones_recibidas,predicciones_esperadas,hash_payload,recibo)
          values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
          on conflict(id_ciclo) do update set id_submission=excluded.id_submission,estado=excluded.estado,intento=excluded.intento,
          predicciones_recibidas=excluded.predicciones_recibidas,hash_payload=excluded.hash_payload,recibo=excluded.recibo,aceptada_en=now()""",
          (cycle["cycle_id"],MODEL_VERSION,receipt["submission_id"],run_id,key,receipt["status"],receipt["attempt"],cycle["data_cutoff"],
           receipt.get("closes_at") or cycle.get("closes_at"),receipt["predictions_received"],receipt["expected_predictions"],
           receipt.get("payload_hash"),json.dumps(receipt)))
        db.executemany("""insert into public.predicciones_api(id_ciclo,id_estacion,instante_objetivo,horizonte_minutos,demanda_predicha)
          values(%s,%s,%s,%s,%s) on conflict(id_ciclo,id_estacion,instante_objetivo) do update set
          horizonte_minutos=excluded.horizonte_minutos,demanda_predicha=excluded.demanda_predicha""",
          [(cycle["cycle_id"],p["station_id"],p["target_at"],p["horizon_minutes"],p["value"]) for p in predictions])

def main():
    load_env()
    if not MODEL_PATH.exists(): raise RuntimeError(f"No existe el modelo: {MODEL_PATH}")
    _,identity=api("/v1/me")
    with psycopg.connect(db_url()) as conn:
        synced=0 if os.getenv("PULSO_SKIP_SYNC")=="1" else sync_stream(conn); status,cycle=api("/v1/forecast-cycles/current")
        if status==404:
            print(f"Sin ciclo abierto; stream sincronizado ({synced} filas)."); return 0
        if already_sent(conn,cycle["cycle_id"]):
            print(f"Ciclo {cycle['cycle_id']} ya entregado; sin POST."); return 0
        artifact=joblib.load(MODEL_PATH); history=load_history(conn,cycle["data_cutoff"])
        frame=build_features(history,cycle,artifact["features"])
        output=np.clip(artifact["model"].predict(frame[artifact["features"]]),0,None)
        predictions=[{"station_id":str(r.station_id),"target_at":r.target_at.isoformat(),"horizon_minutes":int(r.horizon_minutes),"value":round(float(v),3)}
                     for r,v in zip(frame.itertuples(index=False),output,strict=True)]
        validate_predictions(cycle,predictions)
        api_predictions=[{k:p[k] for k in ("station_id","target_at","value")} for p in predictions]
        digest=hashlib.sha256(json.dumps(api_predictions,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        run_id=f"gha-{cycle['cycle_id']}-{digest[:12]}"[-128:]
        key="ptm-"+hashlib.sha256(f"{cycle['cycle_id']}:{MODEL_VERSION}:{digest}".encode()).hexdigest()
        trained_end=artifact.get("metrics",{}).get("validation_through")
        payload={"schema_version":"1.0","cycle_id":cycle["cycle_id"],"client_run_id":run_id,"data_cutoff":cycle["data_cutoff"],
          "model":{"version":MODEL_VERSION,"training_data_end":trained_end,"git_commit":os.getenv("GITHUB_SHA","595220d")[:40]},
          "predictions":api_predictions}
        payload["model"]={k:v for k,v in payload["model"].items() if v}
        if os.getenv("PULSO_DRY_RUN") == "1":
            print(f"Simulación válida: {cycle['cycle_id']}, {len(predictions)} targets, sin POST.")
            return 0
        print(f"Enviando {cycle['cycle_id']}: {len(predictions)} targets.")
        _,receipt=api("/v1/submissions",payload,key)
        if receipt.get("status")!="accepted" or receipt.get("predictions_received")!=cycle["expected_predictions"]:
            raise RuntimeError(f"Recibo inesperado: {receipt}")
        save(conn,cycle,predictions,receipt,run_id,key)
        print(f"Entrega aceptada: {receipt['submission_id']} ({receipt['predictions_received']}/{receipt['expected_predictions']}); {identity.get('display_name','identidad verificada')}.")
    return 0

if __name__=="__main__": raise SystemExit(main())
