# Operación durante la fase de drift

La evidencia concreta de schema v2, fallos, reparaciones, recuperación y
validación está en [el informe de fase final](fase-final-evidencia.md).

Este proyecto sigue la [guía oficial de adaptación](https://github.com/uexternadojz/pulso-transmi/blob/main/docs/fase-drift.md) mediante decisiones reproducibles y sin usar datos posteriores al corte del ciclo.

## Continuidad operativa

- `1 - Collector ETL y respaldo de entrega` sincroniza observaciones y conserva el cursor de ingesta.
- `2 - Vigilar ciclos y entregar` consulta el ciclo vigente, valida exactamente sus targets y usa una clave idempotente.
- `entregas_api` guarda recibo, versión, corte, hash y cantidades recibidas/esperadas.
- La integridad de submissions se calcula como `SUM(recibidas) / SUM(esperadas)` para entregas aceptadas. No mide cobertura oficial porque no incluye ciclos sin entrega. Una evaluación pendiente se registra aparte.

## Monitoreo y decisión

`3 - Monitorear accuracy y drift` se ejecuta cada 15 minutos y registra en `monitoreo_modelo`:

- accuracy acumulada por estación con WAPE;
- accuracy de los últimos seis ciclos completos y de los seis anteriores;
- cobertura de submissions y cobertura de evaluación;
- accuracy por estación y horizonte;
- cambio de nivel de demanda entre el último día y los siete días anteriores;
- versión evaluada, decisión y política aplicada.

No se reentrena por un único ciclo ni por drift aislado. Se espera si la cobertura es menor a 90% o todavía no existen seis ciclos completos. Se evalúa reentrenamiento cuando el promedio de los seis ciclos cae bajo la meta, o cuando existe drift mayor a 20% y el promedio cae al menos tres puntos frente a los seis anteriores. Esta histéresis evita oscilaciones.

## Evaluación y promoción

`4 - Torneo y reentrenamiento` mantiene el orden temporal: entrena con el pasado y valida en una ventana posterior. Compara candidatos con el campeón reproducido bajo el mismo corte. Solo promueve un candidato que supera el umbral; si avanzaron los datos conserva parámetros y refresca el artefacto con trazabilidad.

El artefacto registra `validation_through`, parámetros, métricas por estación y versión. MLflow conserva las métricas del torneo en PostgreSQL. Cambiar el nombre no constituye promoción: el reporte, el artefacto y el commit muestran qué se evaluó.

## Adaptación en inferencia

El envío compara el modelo con referencias causales calculadas solo con observaciones disponibles al corte. La mezcla periódica de 4, 8, 12 y 16 horas fue promovida después de superar al rezago de cuatro horas en doce ciclos cerrados. La selección usa resultados recientes por estación y conserva la estrategia ganadora mediante histéresis.

## Evidencia visible

El dashboard de Vercel muestra acumulado, últimos seis ciclos, seis anteriores, cobertura, drift, decisión y ciclos recientes. Los workflows, reportes del torneo, registros de MLflow, recibos de Supabase y commits permiten reconstruir qué ocurrió antes y después de cada cambio.
