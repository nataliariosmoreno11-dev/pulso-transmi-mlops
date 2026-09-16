"""Download a fixed snapshot and generate the exploratory report and SVG charts.

Run: python3 eda/run_eda.py --refresh
Then: python3 eda/run_eda.py  (rebuild from the saved snapshot)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import statistics as stats
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SNAPSHOT = ROOT / "snapshot"
FIGURES = ROOT / "figures"
BASE = "https://pulso-transmi.72-60-245-2.sslip.io"
FILES = ("stations.csv", "observations.csv", "context.csv")
BLUE, TEAL, ORANGE, INK, GRID = "#2563eb", "#0f766e", "#ea580c", "#17212f", "#dce4ed"


def fetch() -> None:
    SNAPSHOT.mkdir(exist_ok=True)
    for name in FILES:
        with urllib.request.urlopen(f"{BASE}/v1/downloads/{name}", timeout=45) as response:
            (SNAPSHOT / name).write_bytes(response.read())
    with urllib.request.urlopen(f"{BASE}/v1/meta", timeout=20) as response:
        (SNAPSHOT / "meta.json").write_bytes(response.read())


def rows(name: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO((SNAPSHOT / name).read_text(encoding="utf-8-sig"))))


def quantile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    low = int(position)
    return ordered[low] + (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]) * (position - low)


def svg_start(width: int, height: int, title: str) -> list[str]:
    return [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">',
            '<style>text{font-family:Arial,sans-serif;fill:#17212f} .small{font-size:12px;fill:#475569} .title{font-size:19px;font-weight:bold}</style>',
            f'<rect width="{width}" height="{height}" fill="white"/>',
            f'<text x="30" y="34" class="title">{html.escape(title)}</text>']


def text(x: float, y: float, value: object, cls: str = "small", anchor: str = "start") -> str:
    return f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" text-anchor="{anchor}">{html.escape(str(value))}</text>'


def save(name: str, parts: list[str]) -> None:
    FIGURES.mkdir(exist_ok=True)
    (FIGURES / name).write_text("\n".join(parts + ["</svg>"]) + "\n", encoding="utf-8")


def bars(name: str, title: str, labels: list[str], values: list[float], *, horizontal: bool = False) -> None:
    if horizontal:
        width, height = 980, 95 + len(labels) * 34
        out = svg_start(width, height, title)
        left, chart_w = 330, 570
        maxval = max(values) * 1.08
        for i, (label, value) in enumerate(zip(labels, values)):
            y = 64 + i * 34
            out += [text(left - 10, y + 17, label, anchor="end"),
                    f'<rect x="{left}" y="{y}" width="{chart_w * value / maxval:.1f}" height="22" fill="{BLUE}" rx="3"/>',
                    text(left + chart_w * value / maxval + 7, y + 16, f"{value:.1f}")]
    else:
        width, height = 980, 430
        out = svg_start(width, height, title)
        left, top, chart_w, chart_h = 65, 65, 875, 290
        maxval = max(values) * 1.1
        for tick in range(5):
            y = top + chart_h * (1 - tick / 4)
            out += [f'<line x1="{left}" y1="{y:.1f}" x2="{left + chart_w}" y2="{y:.1f}" stroke="{GRID}"/>', text(left - 8, y + 4, f"{maxval * tick / 4:.0f}", anchor="end")]
        step = chart_w / len(labels)
        for i, (label, value) in enumerate(zip(labels, values)):
            x = left + i * step + step * .13
            h = chart_h * value / maxval
            out += [f'<rect x="{x:.1f}" y="{top + chart_h - h:.1f}" width="{step * .74:.1f}" height="{h:.1f}" fill="{BLUE}" rx="2"/>',
                    text(x + step * .37, top + chart_h + 19, label, anchor="middle")]
    save(name, out)


def lines(name: str, title: str, series: list[tuple[str, list[float], str]], labels: list[str], *, y_label: str = "Demanda media") -> None:
    width, height = 1020, 440
    out = svg_start(width, height, title)
    left, top, chart_w, chart_h = 75, 75, 885, 285
    maxval = max(max(v) for _, v, _ in series) * 1.1
    for tick in range(5):
        y = top + chart_h * (1 - tick / 4)
        out += [f'<line x1="{left}" y1="{y:.1f}" x2="{left + chart_w}" y2="{y:.1f}" stroke="{GRID}"/>', text(left - 8, y + 4, f"{maxval * tick / 4:.0f}", anchor="end")]
    for i, label in enumerate(labels):
        if len(labels) <= 24 or i % 5 == 0:
            out.append(text(left + i * chart_w / max(1, len(labels) - 1), top + chart_h + 20, label, anchor="middle"))
    for j, (label, values, color) in enumerate(series):
        points = " ".join(f"{left + i * chart_w / max(1, len(values) - 1):.1f},{top + chart_h * (1 - v / maxval):.1f}" for i, v in enumerate(values))
        out += [f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.5"/>',
                f'<line x1="{left + j * 210}" y1="{height - 22}" x2="{left + j * 210 + 24}" y2="{height - 22}" stroke="{color}" stroke-width="3"/>',
                text(left + j * 210 + 31, height - 18, label)]
    out.append(text(9, 59, y_label))
    save(name, out)


def heatmap(name: str, matrix: dict[str, dict[int, list[int]]], names: dict[str, str]) -> None:
    width, height = 1120, 505
    out = svg_start(width, height, "Demanda media por estación y hora")
    ids = sorted(names)
    values = [stats.mean(matrix[k][h]) for k in ids for h in range(24)]
    low, high = min(values), max(values)
    left, top, cw, ch = 320, 75, 31, 28
    for i, k in enumerate(ids):
        out.append(text(left - 9, top + i * ch + 19, names[k], anchor="end"))
        for h in range(24):
            v = stats.mean(matrix[k][h]); t = (v - low) / (high - low)
            color = f'rgb({int(239 - 207*t)},{int(246 - 153*t)},{int(255 - 78*t)})'
            out.append(f'<rect x="{left + h*cw}" y="{top + i*ch}" width="{cw-1}" height="{ch-1}" fill="{color}"/>')
    for h in range(24):
        out.append(text(left + h*cw + cw/2, top + len(ids)*ch + 19, h, anchor="middle"))
    out += [text(left, height - 31, f"Claro: {low:.0f}  |  Oscuro: {high:.0f} pasajeros por intervalo")]
    save(name, out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Replace the local CSV snapshot from the public API")
    args = parser.parse_args()
    if args.refresh:
        fetch()
    if not all((SNAPSHOT / f).exists() for f in FILES):
        parser.error("No snapshot found. Run with --refresh first.")

    station_rows, observation_rows, context_rows = (rows(f) for f in FILES)
    names = {r["station_id"]: r["station_name"] for r in station_rows}
    missing = {file: {field: sum(not r[field].strip() for r in records) for field in records[0]}
               for file, records in (("stations", station_rows), ("observations", observation_rows), ("context", context_rows))}
    obs_keys = [(r["observed_at"], r["station_id"]) for r in observation_rows]
    context_keys = [r["observed_at"] for r in context_rows]
    by_station: dict[str, list[int]] = defaultdict(list)
    by_hour: dict[int, list[int]] = defaultdict(list)
    by_weekday: dict[int, list[int]] = defaultdict(list)
    by_day: dict[str, list[int]] = defaultdict(list)
    by_hour_weektype: dict[tuple[str, int], list[int]] = defaultdict(list)
    matrix: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    timestamps = set()
    values = []
    first_week: dict[str, list[int]] = defaultdict(list)
    last_week: dict[str, list[int]] = defaultdict(list)
    for r in observation_rows:
        timestamp = datetime.fromisoformat(r["observed_at"])
        value = int(r["demand"])
        station = r["station_id"]
        timestamps.add(timestamp)
        values.append(value)
        by_station[station].append(value)
        by_hour[timestamp.hour].append(value)
        by_weekday[timestamp.weekday()].append(value)
        by_day[timestamp.date().isoformat()].append(value)
        by_hour_weektype[("Laborable" if timestamp.weekday() < 5 else "Fin de semana", timestamp.hour)].append(value)
        matrix[station][timestamp.hour].append(value)
    ordered_times = sorted(timestamps)
    first_day, last_day = ordered_times[0].date(), ordered_times[-1].date()
    for r in observation_rows:
        day = datetime.fromisoformat(r["observed_at"]).date()
        if day < first_day + timedelta(days=7):
            first_week[r["station_id"]].append(int(r["demand"]))
        if day > last_day - timedelta(days=7):
            last_week[r["station_id"]].append(int(r["demand"]))

    gaps = sum(b - a != timedelta(minutes=15) for a, b in zip(ordered_times, ordered_times[1:]))
    expected = len(names) * len(ordered_times)
    absent_pairs = expected - len(set(obs_keys))
    context_times = {datetime.fromisoformat(t) for t in context_keys}
    context_numeric = {field: [float(r[field]) for r in context_rows] for field in context_rows[0] if field != "observed_at"}
    station_means = {k: stats.mean(v) for k, v in by_station.items()}
    weekday_means = [stats.mean(by_weekday[d]) for d in range(7)]
    hour_means = [stats.mean(by_hour[h]) for h in range(24)]

    bars("stations.svg", "Demanda media por estación", [names[k] for k in sorted(names)], [station_means[k] for k in sorted(names)], horizontal=True)
    bars("weekday.svg", "Demanda media por día de la semana", ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"], weekday_means)
    lines("hours.svg", "Perfil horario: laborables y fines de semana",
          [(kind, [stats.mean(by_hour_weektype[(kind, h)]) for h in range(24)], color)
           for kind, color in (("Laborable", BLUE), ("Fin de semana", ORANGE))], [str(h) for h in range(24)])
    days = sorted(by_day)
    lines("daily.svg", "Evolución diaria de la demanda media", [("Media diaria", [stats.mean(by_day[d]) for d in days], TEAL)],
          [d[5:] for d in days])
    heatmap("station_hour.svg", matrix, names)
    bins = [(0, 100), (100, 200), (200, 400), (400, 600), (600, 1000), (1000, 1500), (1500, 2500)]
    counts = [sum(low <= v < high for v in values) for low, high in bins]
    bars("distribution.svg", "Distribución de la demanda (conteo de registros)",
         [f"{low}–{high-1}" for low, high in bins], counts)

    meta_path = SNAPSHOT / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    sha = {name: hashlib.sha256((SNAPSHOT / name).read_bytes()).hexdigest() for name in FILES}
    declared_hashes = meta.get("dataset", {}).get("files", {})
    for filename in FILES:
        expected_hash = declared_hashes.get(filename, {}).get("sha256")
        if expected_hash and sha[filename] != expected_hash:
            raise ValueError(f"SHA-256 mismatch for {filename}: snapshot does not match API metadata")
    peak_table = "\n".join(
        f"| {names[k]} | {max(range(24), key=lambda h: stats.mean(matrix[k][h])):02d}:00 | "
        f"{max(stats.mean(matrix[k][h]) for h in range(24)):.1f} |" for k in sorted(names))
    station_table = "\n".join(
        f"| {k} | {names[k]} | {len(by_station[k]):,} | {station_means[k]:.1f} | {quantile(by_station[k], .5):.0f} | {quantile(by_station[k], .95):.0f} | {max(by_station[k]):,} | {100*(stats.mean(last_week[k])/stats.mean(first_week[k])-1):+.1f}% |"
        for k in sorted(names))
    missing_table = "\n".join(f"| {file} | {len(records):,} | " + ", ".join(f"{field}: {count}" for field, count in missing[file].items()) + " |"
                              for file, records in (("stations", station_rows), ("observations", observation_rows), ("context", context_rows)))
    max_station = max(station_means, key=station_means.get)
    min_station = min(station_means, key=station_means.get)
    highest_hour = max(range(24), key=lambda h: hour_means[h])
    lowest_hour = min(range(24), key=lambda h: hour_means[h])
    report = f"""# Análisis exploratorio — Pulso TransMi

[**Abrir dashboard interactivo**](dashboard.html): filtra por estación y consulta los ejes, unidades y valores al pasar el cursor. Para regenerarlo: `python3 eda/build_dashboard.py`.

Fuente: API pública `{BASE}`. Snapshot local de la respuesta, versión `{meta.get('api_version', 'desconocida')}`; fecha declarada de generación `{meta.get('dataset', {}).get('generated_at', 'desconocida')}`. Para rehacer los gráficos con este snapshot: `python3 eda/run_eda.py`. Para descargar una versión nueva: `python3 eda/run_eda.py --refresh`. Se usa solo la biblioteca estándar de Python.

## Tamaño, esquema y faltantes

| Archivo | Filas | Faltantes por campo |
|---|---:|---|
{missing_table}

**Total de celdas vacías:** {sum(sum(x.values()) for x in missing.values()):,}. Los CSV contienen {len(observation_rows):,} observaciones (`observed_at`, `station_id`, `demand`), {len(context_rows):,} filas de contexto y {len(station_rows)} estaciones. Los IDs se conservan como texto para no perder ceros iniciales.

La ventana va de **{ordered_times[0].isoformat()}** a **{ordered_times[-1].isoformat()}**. Hay {len(ordered_times):,} instantes distintos, {gaps} saltos de frecuencia, {len(obs_keys)-len(set(obs_keys))} claves `(hora, estación)` duplicadas y {absent_pairs} combinaciones estación–hora ausentes dentro de la grilla observada. Contexto: {len(context_keys)-len(set(context_keys))} horas duplicadas; {len(timestamps-context_times)} horas de demanda sin contexto; {len(context_times-timestamps)} horas de contexto sin demanda. {len(set(names)-set(by_station))} estaciones del catálogo carecen de observaciones y {len(set(by_station)-set(names))} IDs de observación no figuran en el catálogo.

Hashes SHA-256 del snapshot: `stations.csv` `{sha['stations.csv']}`, `observations.csv` `{sha['observations.csv']}`, `context.csv` `{sha['context.csv']}`.

## Distribución de la demanda

La demanda es entera, con mínimo **{min(values):,}**, mediana **{quantile(values,.5):,.0f}**, media **{stats.mean(values):,.1f}**, percentil 95 **{quantile(values,.95):,.0f}**, percentil 99 **{quantile(values,.99):,.0f}** y máximo **{max(values):,}**. Hay **{sum(v==0 for v in values):,} ceros** y **{sum(v<0 for v in values):,} negativos**. El total acumulado es **{sum(values):,}**. La media supera la mediana y hay una cola de intervalos con demanda alta.

**Ejes:** X = rangos de pasajeros por estación en un intervalo de 15 minutos; Y = número de registros. Los rangos tienen amplitudes distintas.

![Distribución de la demanda](figures/distribution.svg)

## Diferencias entre estaciones

La estación con mayor media es **{names[max_station]}** ({station_means[max_station]:.1f}); la menor es **{names[min_station]}** ({station_means[min_station]:.1f}). Esta variación exige mirar métricas por estación y no solo una media global.

| ID | Estación | Registros | Media | Mediana | P95 | Máximo | Cambio primera vs. última semana |
|---|---|---:|---:|---:|---:|---:|---:|
{station_table}

El cambio compara dos ventanas completas de siete días, con la misma mezcla de días de la semana. Es descriptivo; no prueba una deriva estadística ni explica su causa.

**Ejes:** X = pasajeros promedio por intervalo de 15 minutos; Y = estación.

![Media por estación](figures/stations.svg)

## Ciclos horarios y semanales

La hora de mayor media global es **{highest_hour:02d}:00–{highest_hour:02d}:59** ({hour_means[highest_hour]:.1f} por intervalo); la menor es **{lowest_hour:02d}:00–{lowest_hour:02d}:59** ({hour_means[lowest_hour]:.1f}). El perfil tiene picos de mañana y tarde, pero la forma cambia entre estaciones: algunas concentran demanda al mediodía o en la noche.

**Ejes:** X = hora local de Bogotá; Y = pasajeros promedio por estación e intervalo de 15 minutos.

![Perfil horario](figures/hours.svg)

Las medias por día (lunes a domingo) son: {', '.join(f'{v:.1f}' for v in weekday_means)}. Los fines de semana son claramente más bajos; para modelar conviene conservar hora, día de la semana y estación.

**Ejes:** X = día de la semana; Y = pasajeros promedio por estación e intervalo de 15 minutos.

![Día de la semana](figures/weekday.svg)

**Ejes:** X = hora local; Y = estación. Color más oscuro = mayor demanda promedio por intervalo de 15 minutos.

![Mapa de calor por estación y hora](figures/station_hour.svg)

Horas con mayor demanda media por estación (cada hora agrupa cuatro intervalos):

| Estación | Hora pico | Media por intervalo en esa hora |
|---|---:|---:|
{peak_table}

El promedio laborable es **{stats.mean(v for d in range(5) for v in by_weekday[d]):.1f}** y el de fin de semana es **{stats.mean(v for d in (5, 6) for v in by_weekday[d]):.1f}**: una diferencia de **{100*(1-stats.mean(v for d in (5, 6) for v in by_weekday[d])/stats.mean(v for d in range(5) for v in by_weekday[d])):.1f}%**.

## Evolución temporal y contexto

La serie diaria muestra variación cíclica. Las medias de la primera y última semana son **{stats.mean(v for rows in first_week.values() for v in rows):.1f}** y **{stats.mean(v for rows in last_week.values() for v in rows):.1f}**, respectivamente. Se requiere validación temporal para saber si esta diferencia altera el error predictivo.

**Ejes:** X = fecha local; Y = pasajeros promedio por estación e intervalo de 15 minutos en ese día.

![Evolución diaria](figures/daily.svg)

Las variables de contexto tienen cobertura completa. Resumen:

| Variable | Mínimo | Media | P95 | Máximo | Ceros |
|---|---:|---:|---:|---:|---:|
"""
    for field, series in context_numeric.items():
        report += f"| `{field}` | {min(series):.3f} | {stats.mean(series):.3f} | {quantile(series,.95):.3f} | {max(series):.3f} | {sum(v==0 for v in series):,} |\n"
    report += """
La coincidencia temporal permite unir contexto y demanda por `observed_at`. Cualquier relación entre lluvia, temperatura o eventos y demanda debe evaluarse controlando estación, hora y día; una comparación simple puede reflejar esos ciclos. Para evitar filtración temporal, usar en predicción solo variables que estén disponibles al momento de pronosticar (por ejemplo, pronósticos, no mediciones futuras). Según el README, demanda, clima y eventos son sintéticos.

## Implicaciones para el proyecto

1. Empezar con un baseline que tenga estación, hora y día de la semana, y medir WAPE por estación.
2. Separar entrenamiento y validación cronológicamente; el README propone 38 días y 7 días.
3. Registrar fecha de corte y hash del snapshot. La API puede liberar nuevas observaciones, así que este informe describe solo el snapshot indicado.
4. Investigar los intervalos de demanda extrema y las variables de contexto antes de definir reglas de reentrenamiento.
"""
    (ROOT / "README.md").write_text(report, encoding="utf-8")
    print(f"EDA listo: {ROOT / 'README.md'}; {len(observation_rows)} observaciones; {sum(sum(x.values()) for x in missing.values())} celdas vacías")


if __name__ == "__main__":
    main()
