# Experimento de mejora de VAR v2

Datos utilizados: 66.123 observaciones de doce estaciones, hasta
`2026-09-21T15:00:00Z` virtual. Se evalúan 72 horas de orígenes horarios;
las primeras 48 seleccionan y las últimas 24 comprueban. De estas últimas,
solo siete ciclos tienen todas sus observaciones de evaluación disponibles.
Los ciclos incompletos se excluyen de esta comparación, no del scoring oficial.

Se compararon 33 estrategias, incluyendo VAR, mezclas, autorregresión por
estación, ajuste periódico, calibración y selección con errores pasados.

| Estrategia | Media anterior (47 ciclos) | Media posterior (7 ciclos) | Mínimo posterior | Ciclos posteriores bajo 70% |
|---|---:|---:|---:|---:|
| VAR v2 productivo | 89,42% | 80,02% | 50,62% | 2 |
| Mezcla fija VAR y armónicos de 4, 8 y 24 horas, ventana de 32 observaciones | 87,07% | 80,75% | 58,40% | 1 |
| Mezcla periódica condicionada a una mejora pasada de 15% por estación | 89,42% | 80,08% | 54,22% | 2 |
| Ganador seleccionado solo con el bloque anterior: mezcla VAR y VAR largo | 89,64% | 79,79% | 49,86% | 1 |

La mezcla fija es prometedora en el bloque posterior, pero empeora el anterior
y no fue el ganador seleccionado antes de conocer el bloque posterior. La
inspección repetida de ese bloque es exploratoria: no certifica generalización.
La mezcla condicionada mejora el mínimo, pero su ganancia media de 0,06 puntos
no supera el umbral de promoción. Ninguna variante se publica en inferencia;
VAR v2 permanece activo y VAR v1 retirado.

El experimento es reproducible con `ml.evaluate_var_ensemble`; los reportes
automáticos registran cortes, resultados por ciclo y métricas de riesgo. Las
pruebas comprueban que no se aprende de targets aún no disponibles, que la
calibración es acotada y que las mezclas condicionadas requieren mejora pasada.
Se requieren nuevos datos independientes y más ciclos completos antes de
reemplazar el modelo. Esta comparación no garantiza mínimos de accuracy ni
modifica la nota o el leaderboard oficial.
