# Entrenamiento de demanda con LightGBM

Desde la raíz del repositorio:

```bash
python -m pip install -e '.[ml]'
python ml/train_lightgbm.py
```

Usa `eda/snapshot/observations.csv` por defecto. Para otro CSV descargado de la API:

```bash
python ml/train_lightgbm.py --input data/observations.csv
```

El script entrena un modelo para las cuatro anticipaciones (15, 30, 45 y 60 minutos). Reserva los últimos siete días para validación temporal, calcula la exactitud oficial como promedio de las exactitudes por estación y compara con la demanda del día anterior. Guarda el modelo y sus métricas en `artifacts/`, carpeta ignorada por Git. La última demanda usada como variable queda 30 minutos antes del origen del pronóstico; antes de usarlo en producción hay que comprobar el `data_cutoff` real de cada ciclo.

## Torneo automático

El workflow `4 - Torneo y reentrenamiento` se ejecuta cada hora. Compara el modelo
productivo con cuatro candidatos usando los mismos últimos siete días como
validación temporal. Un candidato solo se promueve cuando supera al campeón por
al menos 0,10 puntos porcentuales. El ganador se reentrena con toda la historia,
se guarda en `artifacts/lightgbm_demand.joblib` y su versión queda incluida en
las siguientes entregas. Cada ejecución publica `tournament.latest.json` como
artefacto de GitHub Actions, incluso cuando conserva el campeón.

## Validación de VAR v2

El modelo adaptativo activo es `var-v2`; VAR v1 está retirado. LightGBM solo
puede sustituirlo cuando tanto el último ciclo completo como su validación
alcanzan el 80% (`PULSO_ADAPTATION_TARGET`). Esto es un umbral de selección,
no una garantía de accuracy en ciclos futuros.

```bash
python -m ml.backtest_var
```

El backtest compara doce configuraciones en 72 horas de datos: selecciona con
las primeras 48 y evalúa con las últimas 24. Respeta 30 minutos de retraso de
publicación, usa únicamente historia anterior a cada pronóstico y excluye
ciclos simulados con targets sin observación. Reporta media, mínimo y ciclos
por debajo de 80%. Estas simulaciones no equivalen al leaderboard oficial,
que también penaliza ciclos sin entrega.

El torneo publica `validacion-var-<run_id>` con el reporte. Un candidato solo
queda habilitado para revisión si tiene al menos seis ciclos completos de
evaluación, supera al VAR v2 en 0,5 puntos, alcanza 80% de media y no empeora
el mínimo ni aumenta los ciclos bajo 80%. El reporte no cambia producción.

## Experimentos de mejora y riesgo bajo 70%

`python -m ml.evaluate_var_ensemble` compara 33 estrategias: VAR, modelos por
estación, mezclas, patrones periódicos, calibración de sesgo y selección causal
por estación. Se puede reproducir con `--input archivo.csv`. El torneo guarda
`var-ensemble-evaluation.json` junto con la validación de VAR.

El selector usa solo el bloque anterior; el día posterior queda para evaluación.
Las estrategias adaptativas solo utilizan errores cuyos targets ya estaban
disponibles al pronosticar. Una estrategia necesita al menos doce ciclos
completos de evaluación, 80% de media, mejora de 0,5 puntos frente a VAR y no
empeorar el mínimo ni el número de ciclos bajo 70% para quedar habilitada para
revisión. El reporte no cambia el modelo productivo.

Inspeccionar repetidamente un bloque reservado convierte esos resultados en
exploratorios: se requiere una evaluación posterior independiente antes de
publicar una variante elegida después de ver sus resultados.
