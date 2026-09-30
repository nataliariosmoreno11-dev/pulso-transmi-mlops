"""Endpoint de solo lectura para el dashboard operativo de Vercel."""
from __future__ import annotations

import json
import os
from datetime import datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler

import psycopg


def encode(value):
    if isinstance(value, (datetime, Decimal)):
        return value.isoformat() if isinstance(value, datetime) else float(value)
    raise TypeError


def dashboard_data() -> dict:
    uri = os.getenv("PULSO_DATABASE_URL")
    if not uri:
        raise RuntimeError("PULSO_DATABASE_URL no está configurada en Vercel")
    with psycopg.connect(uri, connect_timeout=10) as conn, conn.cursor() as db:
        db.execute("""select calculado_en,version_modelo,ciclos_totales,predicciones_totales,
          predicciones_evaluadas,cobertura,wape_global,accuracy_promedio_estaciones,
          drift_demanda,decision,detalle from public.monitoreo_modelo
          order by calculado_en desc limit 1""")
        row = db.fetchone()
        if not row:
            raise RuntimeError("Todavía no existen métricas de monitoreo")
        columns = [item.name for item in db.description]
        monitor = dict(zip(columns, row, strict=True))
        db.execute("""with scored as (
          select p.id_ciclo,max(p.instante_objetivo) target_at,count(o.demanda) evaluated,
          100*(1-sum(abs(p.demanda_predicha-o.demanda))/nullif(sum(o.demanda),0)) accuracy
          from public.predicciones_api p left join public.observaciones o
          on o.id_estacion=p.id_estacion and o.instante=p.instante_objetivo
          group by p.id_ciclo)
          select id_ciclo,target_at,evaluated,accuracy from scored
          where evaluated>0 order by target_at desc limit 12""")
        cycles = [dict(zip([item.name for item in db.description], item, strict=True)) for item in db.fetchall()]
        db.execute("select max(instante),count(*),count(distinct id_estacion) from public.observaciones")
        latest, observations, stations = db.fetchone()
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "monitor": monitor,
        "data": {"latest_observation": latest, "observations": observations, "stations": stations},
        "recent_cycles": cycles,
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            body = json.dumps(dashboard_data(), default=encode, ensure_ascii=False).encode()
            status = 200
        except Exception as exc:
            print(f"Dashboard API error: {type(exc).__name__}: {exc}")
            body = json.dumps({"error": "Monitoreo temporalmente no disponible"}, ensure_ascii=False).encode()
            status = 503
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)
