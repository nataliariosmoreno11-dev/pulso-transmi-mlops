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
