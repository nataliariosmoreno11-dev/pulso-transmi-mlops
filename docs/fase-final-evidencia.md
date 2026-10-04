# Evidencia de adaptación de la fase final

Informe verificado el 4 de octubre de 2026. Las horas reales de este informe
usan America/Bogota; los cortes de entrenamiento indicados con `Z` usan UTC
y pertenecen al reloj virtual. No deben confundirse ambos relojes.

Requisito: [fase final oficial](https://github.com/uexternadojz/pulso-transmi/blob/main/docs/fase-final.md).

## Cambio detectado e impacto

El stream conserva su endpoint y paginación, pero las observaciones posteriores
a `2026-09-20T12:00:00Z` virtual usan schema v2: `measurement.value` sustituye
a `demand`, llega como texto decimal y puede ser `null`. Una página puede
mezclar ambas versiones. Una medición faltante no representa demanda cero.

La adaptación necesitó tres reparaciones: leer ambos formatos, convertir
decimales válidos y omitir mediciones no disponibles. Además, los huecos
provocaban errores al construir rezagos para la inferencia.

Fallo comprobado: el 3 de octubre a las 20:51:56, el respaldo de entrega abortó
con `Faltan rezagos para 09000 2026-09-20 15:15:00+00:00: ['lag_available']`.
[Ejecución fallida](https://github.com/nataliariosmoreno11-dev/pulso-transmi-mlops/actions/runs/37169266241).
Esto demuestra un bloqueo de esa ejecución; no demuestra por sí solo que se
perdiera definitivamente el ciclo.

## Reparaciones y recuperación

| Cambio | Evidencia versionada |
|---|---|
| Compatibilidad del stream v2 | `2542d93` |
| Normalización de decimales | `e088d08` |
| Omisión de mediciones sin valor | `ee51f45` |
| Rezagos ausentes: buscar un dato anterior, como máximo cuatro intervalos | `2ca1faf` |
| Ajuste de VAR al régimen v2 | `db4fe52` |
| Retirada de VAR v1 | `a0ae4d6` |
| Umbral de sustitución de VAR elevado al 80% y backtest temporal | `1bda6ad` |
| Vigilante continúa tras errores y evita cancelaciones por nuevas ejecuciones | `51fd876` |

Primera recuperación verificada **después del fallo de las 20:51**:

- Ciclo: `cyc_official-20260921_20260920T150000Z`.
- Registro local de aceptación: 3 de octubre, 20:53:22.
- Submission: `sub_480948615b3d451795fc927943789377`.
- Estado persistido: `accepted`; 48 predicciones recibidas de 48 esperadas.
- Versión: `lightgbm-tournament:20261003T070327Z-meta-v2`.

Fuente: fila de `public.entregas_api` consultada el 4 de octubre. Se identifica
esta recuperación concreta, sin afirmar que fuera la primera entrega de toda
la fase v2. Los recibos incluyen hash, corte, versión e intento.

## Evolución de cobertura y accuracy

Snapshot del monitoreo local del 4 de octubre a las 16:39:39:

| Métrica | Resultado |
|---|---:|
| Ciclos registrados | 208 |
| Predicciones registradas / evaluadas | 9.984 / 9.940 |
| Disponibilidad de observaciones para evaluación | 99,56% |
| Media de accuracy por estación acumulada local | 73,25% |
| Media de seis ciclos completos recientes | 61,98% |
| Media de los seis ciclos completos anteriores | 92,29% |
| Ciclos localmente completos | 195 |

Fuente: último registro de `public.monitoreo_modelo` disponible al consultar.
La bajada de 92,29% a 61,98% evidencia deterioro reciente; no implica que la
reparación de ingesta garantice recuperación de precisión.

El campo local de cobertura marca 100%, pero calcula recibidas/esperadas
**solo entre submissions aceptadas**. No cuenta ciclos oficiales sin entrega.
No debe presentarse como cobertura oficial. La accuracy local también omite
targets sin observación disponible y ciclos sin predicción registrada; no es
intercambiable con el leaderboard, que penaliza ausencias. Los seis ciclos
completos locales tampoco necesariamente coinciden con los seis ciclos
resueltos que utiliza el observatorio docente.

## Datos de entrenamiento y evaluación

Artefacto LightGBM versionado en el commit `d109c25`, versión
`lightgbm-tournament:20261004T223003Z`:

- 65.958 observaciones fuente de 12 estaciones.
- 227.088 targets de entrenamiento; 4.608 de validación.
- Último target de entrenamiento: `2026-09-20T11:30:00Z` virtual.
- Último target de validación: `2026-09-21T11:30:00Z` virtual.
- Ganador del torneo: `huber_compact`; parámetros persistidos en
  `artifacts/lightgbm_demand.metrics.json` junto con métricas por estación.
- Accuracy de validación: 49,798%; WAPE global: 0,509109.
- El modelo para evaluar se ajusta solo con la partición de entrenamiento.
  El artefacto final se reajusta con toda la historia disponible, incluida la
  ventana ya evaluada; esa diferencia se explica en `model_tournament.py`.

Ese 49,798% corresponde al candidato LightGBM, no a la accuracy de VAR v2
ni a la calificación oficial. Su validación inferior al umbral impide que
sustituya a VAR. El modelo adaptativo vigente sigue siendo `var-v2`.

VAR v2 ajusta una autorregresión multivariada durante cada inferencia usando
solo historia anterior al instante disponible: orden 3, ventana 32, ridge 1,
sin transformación logarítmica y con retraso conservador de 30 minutos.
No es únicamente un cambio de etiqueta del artefacto LightGBM.

La comparación realizada con datos hasta `2026-09-21T02:00:00Z` cubrió
72 horas: 48 para selección y 24 reservadas para evaluación. En los 16 ciclos
simulados completos del bloque reservado, VAR v2 obtuvo 80,45% de media,
32,67% de mínimo y cuatro ciclos bajo 80%. Ningún candidato pasó el criterio
de mejora. Esto no garantiza un mínimo futuro ni constituye scoring oficial.
El torneo publica los siguientes reportes como artefactos de Actions:
`torneo-modelos-<run_id>` y `validacion-var-<run_id>`.

## Trazabilidad y límites

Los pesos y métricas de LightGBM se versionan en Git; el torneo y MLflow
registran evaluación y parámetros. `entregas_api` vincula cada envío con su
versión, corte, hash y recibo; `predicciones_api` conserva los valores enviados.
Las pruebas comprueban lectura v1/v2, faltantes, causalidad de VAR y recuperación
del vigilante. La reparación del vigilante pasó 29 pruebas y CI.

Este informe no acredita una nota ni promete 70% u 80% en cada ciclo.
Para certificar cobertura y accuracy oficiales debe conservarse también el
corte del portal o del observatorio con su denominador de ciclos oficiales.
