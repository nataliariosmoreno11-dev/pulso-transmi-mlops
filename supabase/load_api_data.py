"""Carga reproducible de la API Pulso TransMi a PostgreSQL/Supabase.

Uso: PULSO_DATABASE_URL='postgresql://...' python3 supabase/load_api_data.py
No imprime ni almacena la cadena de conexión. Requiere psql en PATH.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = os.getenv("PULSO_API_URL", "https://pulso-transmi.72-60-245-2.sslip.io").rstrip("/")
FILES = ("stations.csv", "context.csv", "observations.csv")


def request(path: str) -> bytes:
    headers = {"User-Agent": "pulso-transmi-student-ingest/1.0"}
    api_key = os.getenv("PULSO_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(BASE_URL + path, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read()


def download_snapshot(directory: Path) -> dict[str, int]:
    meta = json.loads(request("/v1/meta"))
    declared = meta.get("dataset", {}).get("files", {})
    counts = {}
    for name in FILES:
        content = request(f"/v1/downloads/{name}")
        expected = declared.get(name, {}).get("sha256")
        if expected and hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"SHA-256 incorrecto para {name}; se detiene la carga")
        path = directory / name
        path.write_bytes(content)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            counts[name] = sum(1 for _ in csv.DictReader(handle))
    return counts


def download_stream(directory: Path) -> int:
    """Descarga páginas liberadas además del snapshot inicial."""
    output = directory / "stream_observations.csv"
    cursor = None
    seen = set()
    total = 0
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["observed_at", "station_id", "demand"])
        writer.writeheader()
        for _ in range(1000):
            query = {"limit": 5000}
            if cursor is not None:
                query["cursor"] = cursor
            payload = json.loads(request("/v1/stream/observations?" + urllib.parse.urlencode(query)))
            for row in payload["data"]:
                writer.writerow({key: row[key] for key in writer.fieldnames})
                total += 1
            next_cursor = payload.get("next_cursor")
            if next_cursor is None:
                return total
            if next_cursor == cursor or next_cursor in seen:
                raise ValueError("La API repitió un cursor del stream")
            seen.add(next_cursor)
            cursor = next_cursor
    raise ValueError("El stream superó 1.000 páginas; se detiene la carga")


def sql_path(path: Path) -> str:
    # TemporaryDirectory creates these paths; quoting keeps psql \copy valid.
    return "'" + str(path).replace("'", "''") + "'"


def load_sql(directory: Path) -> str:
    stations = sql_path(directory / "stations.csv")
    context = sql_path(directory / "context.csv")
    observations = sql_path(directory / "observations.csv")
    stream = sql_path(directory / "stream_observations.csv")
    return f"""begin;
create temp table carga_estaciones (station_id text, station_name text, corridor text, latitude numeric, longitude numeric) on commit drop;
create temp table carga_contexto (observed_at timestamptz, rain_mm numeric, rain_forecast numeric, temperature_c numeric, temperature_forecast numeric, event_intensity numeric) on commit drop;
create temp table carga_observaciones (observed_at timestamptz, station_id text, demand integer) on commit drop;
create temp table carga_stream (observed_at timestamptz, station_id text, demand integer) on commit drop;
\\copy carga_estaciones from {stations} with (format csv, header true)
\\copy carga_contexto from {context} with (format csv, header true)
\\copy carga_observaciones from {observations} with (format csv, header true)
\\copy carga_stream from {stream} with (format csv, header true)

insert into public.estaciones (id_estacion, nombre, corredor, latitud, longitud)
select station_id, station_name, corridor, latitude, longitude from carga_estaciones
on conflict (id_estacion) do update set
 nombre=excluded.nombre, corredor=excluded.corredor,
 latitud=excluded.latitud, longitud=excluded.longitud
where (estaciones.nombre, estaciones.corredor, estaciones.latitud, estaciones.longitud)
 is distinct from (excluded.nombre, excluded.corredor, excluded.latitud, excluded.longitud);

insert into public.contexto (instante, lluvia_mm, pronostico_lluvia, temperatura_c, pronostico_temperatura, intensidad_evento)
select observed_at, rain_mm, rain_forecast, temperature_c, temperature_forecast, event_intensity from carga_contexto
on conflict (instante) do update set
 lluvia_mm=excluded.lluvia_mm, pronostico_lluvia=excluded.pronostico_lluvia,
 temperatura_c=excluded.temperatura_c, pronostico_temperatura=excluded.pronostico_temperatura,
 intensidad_evento=excluded.intensidad_evento, recibido_en=now()
where (contexto.lluvia_mm, contexto.pronostico_lluvia, contexto.temperatura_c,
       contexto.pronostico_temperatura, contexto.intensidad_evento)
 is distinct from (excluded.lluvia_mm, excluded.pronostico_lluvia, excluded.temperatura_c,
                   excluded.pronostico_temperatura, excluded.intensidad_evento);

insert into public.observaciones (instante, id_estacion, demanda)
select observed_at, station_id, demand from carga_observaciones
on conflict (id_estacion, instante) do update set demanda=excluded.demanda, recibido_en=now()
where observaciones.demanda is distinct from excluded.demanda;

insert into public.observaciones (instante, id_estacion, demanda)
select distinct on (station_id, observed_at) observed_at, station_id, demand
from carga_stream order by station_id, observed_at, ctid desc
on conflict (id_estacion, instante) do update set demanda=excluded.demanda, recibido_en=now()
where observaciones.demanda is distinct from excluded.demanda;

insert into public.estado_ingesta (recurso, ultimo_instante, cursor)
select 'observations', max(instante), null from public.observaciones
on conflict (recurso) do update set ultimo_instante=excluded.ultimo_instante,
 cursor=null, actualizado_en=now();
insert into public.estado_ingesta (recurso, ultimo_instante, cursor)
select 'context', max(instante), null from public.contexto
on conflict (recurso) do update set ultimo_instante=excluded.ultimo_instante,
 cursor=null, actualizado_en=now();

select 'estaciones=' || (select count(*) from public.estaciones) ||
       ' contexto=' || (select count(*) from public.contexto) ||
       ' observaciones=' || (select count(*) from public.observaciones) as resumen;
commit;
"""


def main() -> None:
    database_url = os.getenv("PULSO_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("Falta PULSO_DATABASE_URL o DATABASE_URL; no se conectó a PostgreSQL")
    with tempfile.TemporaryDirectory(prefix="pulso-ingest-") as temp:
        directory = Path(temp)
        counts = download_snapshot(directory)
        stream_count = download_stream(directory)
        print(f"API descargada y verificada: {counts}; stream={stream_count}")
        result = subprocess.run(
            ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "--dbname", database_url],
            input=load_sql(directory), text=True, capture_output=True, check=False,
        )
        if result.returncode:
            # PostgreSQL error text may contain table/column names, never the URL argument.
            sys.stderr.write(result.stderr)
            raise SystemExit("Falló la transacción de carga; no se confirmó ningún cambio")
        print(result.stdout.strip())


if __name__ == "__main__":
    main()
