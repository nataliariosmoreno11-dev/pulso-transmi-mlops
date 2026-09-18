# Experimentos de demanda con MLflow

Esta rama registra **una ejecución nueva y una versión nueva del modelo** cada vez que ejecutas el entrenamiento. Se guardan parámetros, hash SHA-256 del CSV, commit de Git, cutoff, métricas globales, métricas por estación y horizonte, el reporte JSON y el modelo.

## Entrenar

Desde la raíz del repositorio:

```bash
python -m pip install -e '.[ml]'
python ml/train_lightgbm.py --run-name prueba-01
```

Puedes usar otro CSV de observaciones descargado de la API:

```bash
python ml/train_lightgbm.py --input data/observations.csv --run-name datos-nuevos-01
```

El registro local usa `mlflow.db` y los artefactos quedan en `mlruns/`; ambos se ignoran en Git porque son datos de ejecución. Para verlos:

```bash
mlflow ui --backend-store-uri sqlite:///$(pwd)/mlflow.db
```

Abre `http://127.0.0.1:5000` en el navegador. Busca el experimento `pulso-transmi-demand` y el modelo registrado `pulso-transmi-demand`. Cada entrenamiento aparece como una ejecución y crea la siguiente versión del modelo. El JSON de `artifacts/` incluye el ID de la ejecución y el número de versión.

## Servidor MLflow remoto

Para compartir automáticamente las métricas entre equipos o máquinas, necesitas un servidor MLflow accesible. Configura `MLFLOW_TRACKING_URI` con su dirección antes de entrenar, o usa `--tracking-uri`. Sin esa dirección, el historial queda **solo en este equipo**. No pongas credenciales en Git.

La validación usa los últimos siete días y los cuatro horizontes (15, 30, 45 y 60 minutos). Las variables de demanda usan como máximo observaciones disponibles 30 minutos antes del origen supuesto; verifica el `data_cutoff` real al integrar el envío de pronósticos.
