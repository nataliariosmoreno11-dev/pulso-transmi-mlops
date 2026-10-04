"""Pipeline idempotente de sincronización, inferencia y submission."""
from __future__ import annotations
import hashlib, json, math, os, re, time, urllib.error, urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
import joblib, numpy as np, pandas as pd, psycopg

BASE=os.getenv("PULSO_API_URL","https://pulso-transmi.72-60-245-2.sslip.io").rstrip("/")
MODEL_PATH=Path(os.getenv("PULSO_MODEL_PATH","artifacts/lightgbm_demand.joblib"))
MODEL_VERSION=os.getenv("PULSO_MODEL_VERSION","lightgbm-demand:2.0")
MODEL_VERSION_PATTERN=re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

def validate_model_version(version):
    if not MODEL_VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"Versión de modelo incompatible con la API: {version!r}")
    return version

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

def observation_demand(row):
    """Lee demanda tanto del contrato v1 como del sobre v2 del stream."""
    if "demand" in row:
        value=row["demand"]
    else:
        measurement=row.get("measurement")
        if not isinstance(measurement,dict) or "value" not in measurement:
            raise RuntimeError(f"Observación sin demanda compatible; campos={sorted(row)}")
        value=measurement["value"]
    if value is None:
        return None
    try:
        parsed=Decimal(str(value))
    except InvalidOperation as exc:
        raise RuntimeError(f"Demanda no numérica: {value!r}") from exc
    if not parsed.is_finite() or parsed < 0 or parsed != parsed.to_integral_value():
        raise RuntimeError(f"Demanda incompatible con conteo entero: {value!r}")
    return int(parsed)

def sync_stream(conn):
    with conn.cursor() as db:
        db.execute("select cursor from public.estado_ingesta where recurso='observations'")
        state = db.fetchone()
    cursor = state[0] if state else None
    total=0
    changed=0
    skipped=0
    for _ in range(1000):
        path="/v1/stream/observations?limit=500"+(f"&cursor={quote(cursor,safe='')}" if cursor else "")
        _,page=api(path); rows=page.get("data",[])
        parsed=[]
        for row in rows:
            demand=observation_demand(row)
            if demand is None:
                skipped+=1
                continue
            parsed.append((row["station_id"],row["observed_at"],demand))
        with conn.cursor() as db:
            db.executemany("""insert into public.observaciones(id_estacion,instante,demanda) values(%s,%s,%s)
              on conflict(id_estacion,instante) do update set demanda=excluded.demanda,recibido_en=now()
              where observaciones.demanda is distinct from excluded.demanda""",
              parsed)
            changed += max(db.rowcount, 0)
        conn.commit(); total+=len(rows)
        nxt=page.get("next_cursor")
        if nxt is None:
            with conn.cursor() as db:
                db.execute("""insert into public.estado_ingesta(recurso,ultimo_instante,cursor,actualizado_en)
                  select 'observations',max(instante),%s,now() from public.observaciones
                  on conflict(recurso) do update set ultimo_instante=excluded.ultimo_instante,
                  cursor=excluded.cursor,actualizado_en=excluded.actualizado_en""", (cursor,))
            conn.commit()
            print(f"Stream: {total} filas recorridas; {changed} nuevas o modificadas; {skipped} sin medición omitidas.")
            return changed
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
    stations=sorted(history.station_id.astype(str).unique()); rows=[]; imputed=0
    for target in cycle["targets"]:
        station=str(target["station_id"]); target_at=pd.Timestamp(target["target_at"])
        horizon=int(target.get("horizon_minutes") or (target_at-pd.Timestamp(cycle["origin_at"])).total_seconds()/60); h=int(horizon//15)
        if horizon not in (15,30,45,60): raise RuntimeError(f"Horizonte no soportado: {horizon}")
        times={"lag_available":target_at-pd.Timedelta(minutes=15*(h+2)),"lag_15m":target_at-pd.Timedelta(minutes=15*(h+3)),
               "lag_1h":target_at-pd.Timedelta(minutes=15*(h+6)),"lag_2h":target_at-pd.Timedelta(minutes=15*(h+10)),
               "lag_4h":target_at-pd.Timedelta(hours=4),"lag_8h":target_at-pd.Timedelta(hours=8),"lag_12h":target_at-pd.Timedelta(hours=12),"lag_16h":target_at-pd.Timedelta(hours=16),"lag_day":target_at-pd.Timedelta(days=1),"lag_2days":target_at-pd.Timedelta(days=2),"lag_week":target_at-pd.Timedelta(days=7)}
        feature_values={}
        missing=[]
        for name,instant in times.items():
            key=(station,instant)
            if key in values:
                feature_values[name]=values[key]
                continue
            # El stream v2 puede marcar una medición aislada como no disponible.
            # Usamos solo el último dato anterior, nunca información futura.
            replacement=None
            for step in range(1,5):
                previous=(station,instant-pd.Timedelta(minutes=15*step))
                if previous in values:
                    replacement=values[previous]
                    break
            if replacement is None:
                missing.append(name)
            else:
                feature_values[name]=replacement
                imputed+=1
        if missing: raise RuntimeError(f"Faltan rezagos para {station} {target_at}: {missing}")
        rows.append({"station_id":station,"horizon_steps":h,"slot":target_at.hour*4+target_at.minute//15,
          "day_of_week":target_at.dayofweek,"is_weekend":int(target_at.dayofweek>=5),
          **feature_values,"target_at":target_at,"horizon_minutes":horizon})
    frame=pd.DataFrame(rows); frame["station_id"]=pd.Categorical(frame["station_id"],categories=stations)
    for column, categories in (("horizon_steps", [1, 2, 3, 4]), ("slot", list(range(96))), ("day_of_week", list(range(7))), ("is_weekend", [0, 1])):
        frame[column] = pd.Categorical(frame[column], categories=categories)
    if set(names)-set(frame): raise RuntimeError(f"Variables desconocidas: {sorted(set(names)-set(frame))}")
    if imputed: print(f"Rezagos ausentes imputados causalmente: {imputed}.")
    return frame

def weighted_median(values, weights):
    order=np.argsort(values); ordered_values=np.asarray(values,dtype=float)[order]; ordered_weights=np.asarray(weights,dtype=float)[order]
    return float(ordered_values[np.searchsorted(np.cumsum(ordered_weights),ordered_weights.sum()/2)])

def calibration_factors(conn,base_version,cutoff,minimum_rows=24):
    versions=(base_version,base_version+"-station-cal-v1")
    with conn.cursor() as db:
        db.execute("""select p.id_estacion,p.demanda_predicha,o.demanda from public.predicciones_api p
          join public.entregas_api e using(id_ciclo) join public.observaciones o
          on o.id_estacion=p.id_estacion and o.instante=p.instante_objetivo
          where e.version_modelo=any(%s) and o.instante<=%s and p.demanda_predicha>0""",(list(versions),cutoff))
        rows=db.fetchall()
    grouped={}
    for station,predicted,actual in rows:
        grouped.setdefault(str(station),[]).append((float(actual)/float(predicted),float(predicted)))
    return {station:float(np.clip(weighted_median([x[0] for x in data],[x[1] for x in data]),.8,1.2))
            for station,data in grouped.items() if len(data)>=minimum_rows}

def recent_meta_adjustments(conn,cutoff,minimum_rows=24,lookback_rows=96,lag_advantage=5.0):
    with conn.cursor() as db:
        db.execute("""with evaluated as (
          select p.id_estacion,p.demanda_predicha::float predicted,o.demanda::float actual,
                 lag_o.demanda::float recent_lag,
                 ((lag4_o.demanda+lag8_o.demanda+lag12_o.demanda+lag16_o.demanda)/4.0)::float lag_4h,
                 row_number() over(partition by p.id_estacion order by p.instante_objetivo desc) as rn
          from public.predicciones_api p join public.observaciones o
          on o.id_estacion=p.id_estacion and o.instante=p.instante_objetivo
          join public.observaciones lag_o on lag_o.id_estacion=p.id_estacion
            and lag_o.instante=p.instante_objetivo-make_interval(mins => p.horizonte_minutos+30)
          join public.observaciones lag4_o on lag4_o.id_estacion=p.id_estacion
            and lag4_o.instante=p.instante_objetivo-interval '4 hours'
          join public.observaciones lag8_o on lag8_o.id_estacion=p.id_estacion and lag8_o.instante=p.instante_objetivo-interval '8 hours'
          join public.observaciones lag12_o on lag12_o.id_estacion=p.id_estacion and lag12_o.instante=p.instante_objetivo-interval '12 hours'
          join public.observaciones lag16_o on lag16_o.id_estacion=p.id_estacion and lag16_o.instante=p.instante_objetivo-interval '16 hours'
          where p.instante_objetivo<=%s and p.demanda_predicha>0)
          select id_estacion,predicted,actual,recent_lag,lag_4h,rn from evaluated where rn<=%s""",
          (cutoff,lookback_rows))
        rows=db.fetchall()
    grouped={}
    for station,predicted,actual,recent_lag,lag_4h,recency in rows:
        grouped.setdefault(str(station),[]).append((int(recency),float(actual),float(predicted),float(recent_lag),float(lag_4h)))
    adjustments={}
    for station,data in grouped.items():
        if len(data)<minimum_rows: continue
        data=sorted(data,key=lambda x:x[0])
        actual_total=sum(x[1] for x in data)
        if actual_total<=0: continue
        selection=data[:8]
        selection_actual=sum(x[1] for x in selection)
        model_accuracy=100*(1-sum(abs(x[1]-x[2]) for x in selection)/selection_actual)
        lag_accuracy=100*(1-sum(abs(x[1]-x[3]) for x in selection)/selection_actual)
        lag4_accuracy=100*(1-sum(abs(x[1]-x[4]) for x in selection)/selection_actual)
        # Bajo drift severo las predicciones guardadas ya contienen ajustes de
        # ciclos anteriores. Volver a aplicar un factor sobre ellas crea
        # realimentación y puede hacer crecer el error en cada entrega.
        # Si las predicciones guardadas ya eran el ciclo de 4 h, ambas métricas
        # empatan. Preferimos conservar esa estrategia para evitar oscilar de
        # vuelta al modelo que no produjo realmente ese buen resultado.
        lag4_near_best=lag4_accuracy>=max(model_accuracy,lag_accuracy)-1.0
        use_lag4=lag4_near_best or (lag4_accuracy>=max(model_accuracy,lag_accuracy)+lag_advantage) or (model_accuracy<60.0 and lag4_accuracy>lag_accuracy)
        use_lag=(not use_lag4) and ((model_accuracy<60.0) or (lag_accuracy>=model_accuracy+lag_advantage))
        bases=[x[4] if use_lag4 else x[3] if use_lag else x[2] for x in data]
        predicted_total=sum(bases)
        long_factor=1.0 if predicted_total<=0 else actual_total/predicted_total
        recent=data[:8]
        recent_actual=sum(x[1] for x in recent)
        recent_predicted=sum(x[4] if use_lag4 else x[3] if use_lag else x[2] for x in recent)
        short_factor=long_factor if recent_predicted<=0 else recent_actual/recent_predicted
        regime_shift=(long_factor>0 and short_factor>0 and abs(math.log(short_factor/long_factor))>=.15)
        # La persistencia no se recalibra con predicciones ya ajustadas.
        factor=1.0 if (use_lag or use_lag4) else float(np.clip(short_factor if regime_shift else long_factor,.85,1.15))
        adjustments[station]=(use_lag,use_lag4,factor)
    return adjustments

def latest_complete_cycle_accuracy(conn):
    """Accuracy oficial media por estación del ciclo completo más reciente."""
    with conn.cursor() as db:
        db.execute("""with station_scores as (
          select p.id_ciclo,p.id_estacion,count(*) targets,max(p.instante_objetivo) target_at,
                 greatest(0,100*(1-sum(abs(p.demanda_predicha-o.demanda))::float8/
                   nullif(sum(o.demanda),0))) accuracy
          from public.predicciones_api p join public.observaciones o
          on o.id_estacion=p.id_estacion and o.instante=p.instante_objetivo
          group by p.id_ciclo,p.id_estacion), cycles as (
          select id_ciclo,max(target_at) target_at,sum(targets) targets,avg(accuracy) accuracy
          from station_scores group by id_ciclo)
          select accuracy from cycles where targets=48
          order by target_at desc limit 1""")
        row=db.fetchone()
    return float(row[0]) if row and row[0] is not None else None

def use_multivariate_fallback(latest_accuracy,artifact,target=75.0):
    validation=(artifact.get("metrics",{}).get("lightgbm",{})
                .get("accuracy_mean_12_stations"))
    if latest_accuracy is None:
        return False
    return latest_accuracy < target or validation is None or float(validation) < target

def local_autoregressive_predictions(history, frame, order=2, window=24, ridge=1.0):
    """Pronóstico local y causal para reaccionar a cambios bruscos de régimen."""
    series={}
    for station,group in history.groupby("station_id",sort=False):
        ordered=group.sort_values("observed_at").set_index("observed_at")["demand"].astype(float)
        grid=pd.date_range(ordered.index.min(),ordered.index.max(),freq="15min",tz="UTC")
        series[str(station)]=ordered.reindex(grid).ffill(limit=4)
    output=[]
    for row in frame.itertuples(index=False):
        steps=int(row.horizon_steps)+2
        available_at=pd.Timestamp(row.target_at)-pd.Timedelta(minutes=15*steps)
        values=series[str(row.station_id)].loc[:available_at].dropna().tail(window).to_numpy()
        if len(values)<order+5:
            output.append(max(0.0,2*float(row.lag_available)-float(row.lag_15m)))
            continue
        scale=max(float(np.mean(np.abs(values))),1.0)
        normalized=values/scale
        design=np.array([normalized[i-order:i][::-1] for i in range(order,len(normalized))])
        target=normalized[order:]
        design=np.column_stack([np.ones(len(design)),design])
        penalty=np.eye(order+1)*ridge
        penalty[0,0]=0
        coefficients=np.linalg.solve(design.T@design+penalty,design.T@target)
        generated=list(normalized)
        for _ in range(steps):
            generated.append(max(0.0,float(coefficients[0]+coefficients[1:]@generated[-order:][::-1])))
        output.append(generated[-1]*scale)
    return np.asarray(output,dtype=float)

def multivariate_autoregressive_predictions(history,frame,order=3,window=32,ridge=1.0,log_transform=False):
    """VAR corto para capturar transferencias recientes entre estaciones."""
    station_series={}
    for station,group in history.groupby("station_id",sort=False):
        ordered=group.sort_values("observed_at").set_index("observed_at")["demand"].astype(float)
        grid=pd.date_range(ordered.index.min(),ordered.index.max(),freq="15min",tz="UTC")
        station_series[str(station)]=ordered.reindex(grid).ffill(limit=4)
    matrix=pd.DataFrame(station_series).dropna().sort_index()
    positions={station:index for index,station in enumerate(matrix.columns)}
    cache={}
    output=[]
    for row in frame.itertuples(index=False):
        steps=int(row.horizon_steps)+2
        available_at=pd.Timestamp(row.target_at)-pd.Timedelta(minutes=15*steps)
        key=(available_at,steps)
        if key not in cache:
            values=matrix.loc[:available_at].tail(window+order).to_numpy(dtype=float)
            if log_transform:
                values=np.log1p(values)
            if len(values)<window:
                cache[key]=None
            else:
                scale=np.maximum(np.mean(np.abs(values),axis=0),1.0)
                normalized=values/scale
                design=np.array([normalized[i-order:i][::-1].reshape(-1) for i in range(order,len(normalized))])
                target=normalized[order:]
                design=np.column_stack([np.ones(len(design)),design])
                penalty=np.eye(design.shape[1])*ridge
                penalty[0,0]=0
                coefficients=np.linalg.solve(design.T@design+penalty,design.T@target)
                generated=list(normalized)
                for _ in range(steps):
                    inputs=np.r_[1,np.asarray(generated[-order:][::-1]).reshape(-1)]
                    generated.append(np.maximum(0,inputs@coefficients))
                prediction=generated[-1]*scale
                if log_transform:
                    prediction=np.expm1(prediction)
                cache[key]=np.clip(prediction,0,100000)
        prediction=cache[key]
        if prediction is None or str(row.station_id) not in positions:
            fallback=max(0.0,2*float(row.lag_available)-float(row.lag_15m))
            output.append(fallback)
        else:
            output.append(float(prediction[positions[str(row.station_id)]]))
    return np.asarray(output,dtype=float)

def validate_predictions(cycle,predictions):
    expected={(str(t["station_id"]),pd.Timestamp(t["target_at"])) for t in cycle["targets"]}
    actual={(p["station_id"],pd.Timestamp(p["target_at"])) for p in predictions}
    if len(predictions)!=int(cycle["expected_predictions"]) or len(actual)!=len(predictions) or actual!=expected:
        raise RuntimeError("Las predicciones no coinciden exactamente con los targets")
    if any(not math.isfinite(float(p["value"])) or float(p["value"])<0 for p in predictions):
        raise RuntimeError("Predicción negativa, NaN o infinita")

def accepted_submission(conn,cycle_id):
    with conn.cursor() as db:
        db.execute("select version_modelo,intento from public.entregas_api where id_ciclo=%s and estado='accepted'",(cycle_id,))
        return db.fetchone()

def save(conn,cycle,predictions,receipt,run_id,key,model_version):
    with conn.transaction(),conn.cursor() as db:
        db.execute("""insert into public.entregas_api(id_ciclo,version_modelo,id_submission,id_ejecucion_cliente,clave_idempotencia,
          estado,intento,corte_datos,cierre_ciclo,predicciones_recibidas,predicciones_esperadas,hash_payload,recibo)
          values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
          on conflict(id_ciclo) do update set id_submission=excluded.id_submission,estado=excluded.estado,intento=excluded.intento,
          version_modelo=excluded.version_modelo,predicciones_recibidas=excluded.predicciones_recibidas,
          hash_payload=excluded.hash_payload,recibo=excluded.recibo,aceptada_en=now()""",
          (cycle["cycle_id"],model_version,receipt["submission_id"],run_id,key,receipt["status"],receipt["attempt"],cycle["data_cutoff"],
           receipt.get("closes_at") or cycle.get("closes_at"),receipt["predictions_received"],receipt["expected_predictions"],
           receipt.get("payload_hash"),json.dumps(receipt)))
        db.executemany("""insert into public.predicciones_api(id_ciclo,id_estacion,instante_objetivo,horizonte_minutos,demanda_predicha)
          values(%s,%s,%s,%s,%s) on conflict(id_ciclo,id_estacion,instante_objetivo) do update set
          horizonte_minutos=excluded.horizonte_minutos,demanda_predicha=excluded.demanda_predicha""",
          [(cycle["cycle_id"],p["station_id"],p["target_at"],p["horizon_minutes"],p["value"]) for p in predictions])

def main():
    load_env()
    if not MODEL_PATH.exists(): raise RuntimeError(f"No existe el modelo: {MODEL_PATH}")
    with psycopg.connect(db_url()) as conn:
        synced=0
        if os.getenv("PULSO_SKIP_SYNC")!="1":
            try:
                synced=sync_stream(conn)
            except RuntimeError as exc:
                print(f"Advertencia: stream no disponible; se continúa con Supabase: {exc}")
        status,cycle=api("/v1/forecast-cycles/current")
        if status==404:
            print(f"Sin ciclo abierto; stream sincronizado ({synced} filas)."); return 0
        artifact=joblib.load(MODEL_PATH); base_model_version=artifact.get("model_version", MODEL_VERSION); history=load_history(conn,cycle["data_cutoff"])
        frame=build_features(history,cycle,artifact["features"])
        periodic_blend=frame[["lag_4h","lag_8h","lag_12h","lag_16h"]].mean(axis=1)
        output=np.clip(artifact["model"].predict(frame[artifact["features"]]),0,None)
        factors=calibration_factors(conn,base_model_version,cycle["data_cutoff"])
        if factors:
            output=np.array([value*factors.get(str(station),1.0) for value,station in zip(output,frame["station_id"],strict=True)])
            model_version=base_model_version+"-station-cal-v1"
            print(f"Calibración aplicada a {len(factors)} estaciones.")
        else:
            model_version=base_model_version
        adjustments=recent_meta_adjustments(conn,cycle["data_cutoff"])
        if adjustments:
            output=np.array([
              (float(lag4) if adjustments.get(str(station),(False,False,1.0))[1] else
               float(recent) if adjustments.get(str(station),(False,False,1.0))[0] else value)
              * adjustments.get(str(station),(False,False,1.0))[2]
              for value,recent,lag4,station in zip(output,frame["lag_available"],periodic_blend,frame["station_id"],strict=True)])
            fallback_stations=sorted(station for station,(use_lag,_,_) in adjustments.items() if use_lag)
            lag4_stations=sorted(station for station,(_,use_lag4,_) in adjustments.items() if use_lag4)
            model_version += "-meta-v2"
            print(f"Selector reciente aplicado a {len(adjustments)} estaciones; persistencia: {fallback_stations}; mezcla periódica: {lag4_stations}.")
        latest_accuracy=latest_complete_cycle_accuracy(conn)
        adaptation_target=float(os.getenv("PULSO_ADAPTATION_TARGET","75"))
        if use_multivariate_fallback(latest_accuracy,artifact,adaptation_target):
            output=multivariate_autoregressive_predictions(history,frame)
            model_version += "-var-v2"
            validation=artifact.get("metrics",{}).get("lightgbm",{}).get("accuracy_mean_12_stations")
            validation_text="sin métrica" if validation is None else f"{float(validation):.2f}%"
            print(f"Modo adaptativo: VAR v2 activo; último ciclo={latest_accuracy:.2f}%, "
                  f"LightGBM validado={validation_text}, meta={adaptation_target:.2f}%.")
        model_version=validate_model_version(model_version)
        accepted=accepted_submission(conn,cycle["cycle_id"])
        if accepted and accepted[0] == model_version:
            print(f"Ciclo {cycle['cycle_id']} ya entregado con {model_version}; sin POST."); return 0
        if accepted and int(accepted[1]) >= 3:
            print(f"Ciclo {cycle['cycle_id']} agotó sus tres intentos; se conserva {accepted[0]}."); return 0
        if accepted:
            print(f"Se reemplazará {accepted[0]} por {model_version} en el intento {int(accepted[1])+1}.")
        predictions=[{"station_id":str(r.station_id),"target_at":r.target_at.isoformat(),"horizon_minutes":int(r.horizon_minutes),"value":round(float(v),3)}
                     for r,v in zip(frame.itertuples(index=False),output,strict=True)]
        validate_predictions(cycle,predictions)
        api_predictions=[{k:p[k] for k in ("station_id","target_at","value")} for p in predictions]
        digest=hashlib.sha256(json.dumps(api_predictions,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        run_id=f"gha-{cycle['cycle_id']}-{digest[:12]}"[-128:]
        key="ptm-"+hashlib.sha256(f"{cycle['cycle_id']}:{model_version}:{digest}".encode()).hexdigest()
        trained_end=artifact.get("metrics",{}).get("validation_through")
        payload={"schema_version":"1.0","cycle_id":cycle["cycle_id"],"client_run_id":run_id,"data_cutoff":cycle["data_cutoff"],
          "model":{"version":model_version,"training_data_end":trained_end,"git_commit":os.getenv("GITHUB_SHA","595220d")[:40]},
          "predictions":api_predictions}
        payload["model"]={k:v for k,v in payload["model"].items() if v}
        if os.getenv("PULSO_DRY_RUN") == "1":
            print(f"Simulación válida: {cycle['cycle_id']}, {len(predictions)} targets, sin POST.")
            return 0
        print(f"Enviando {cycle['cycle_id']}: {len(predictions)} targets.")
        _,receipt=api("/v1/submissions",payload,key)
        if receipt.get("status")!="accepted" or receipt.get("predictions_received")!=cycle["expected_predictions"]:
            raise RuntimeError(f"Recibo inesperado: {receipt}")
        save(conn,cycle,predictions,receipt,run_id,key,model_version)
        print(f"Entrega aceptada: {receipt['submission_id']} ({receipt['predictions_received']}/{receipt['expected_predictions']}).")
    return 0

if __name__=="__main__": raise SystemExit(main())
