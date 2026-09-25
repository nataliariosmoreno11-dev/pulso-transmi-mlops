# Solicitud de revisión de la actividad Pulso TransMi

## Resumen verificable

En el corte mostrado por la plataforma el **25 de septiembre de 2026 a las 11:42 a. m.**, mi resultado figuraba así:

- Accuracy oficial: **39,7 %**.
- Cobertura: **52,8 %**.
- Ciclos evaluados: **19 de 36**.
- Clasificación mostrada: **puesto 15**.

La cobertura incompleta redujo considerablemente el resultado porque las ausencias cuentan como predicción cero.

## Incidente técnico identificado

La automatización intentó enviar predicciones calibradas usando una versión de modelo que contenía el carácter `+`. La API rechazó esos envíos con HTTP 422 porque ese carácter no está permitido en `model.version`.

- Ejecución fallida documentada: [GitHub Actions run 36193054093](https://github.com/nataliariosmoreno11-dev/pulso-transmi-mlops/actions/runs/36193054093)
- Corrección publicada: [commit fdd96c5](https://github.com/nataliariosmoreno11-dev/pulso-transmi-mlops/commit/fdd96c5)
- Cambio aplicado: la versión pasó a usar `-station-cal-v1` y la automatización dejó de ocultar los errores de envío.

La causa ya está corregida. También se agregó una validación previa que bloquea versiones incompatibles antes de llamar a la API.

## Evidencia de funcionamiento después de la corrección

Una vez corregido el formato, la API recibió correctamente el siguiente ciclo:

- Ciclo: `cyc_official-20260921_20260913T110000Z`
- Recibo: `sub_0750ce14932d400b8964f8116aac2a47`
- Predicciones aceptadas: **48 de 48**.
- Modelo: `lightgbm-tournament:20260924T203613Z-station-cal-v1`.
- Automatización corregida: [GitHub Actions run 36193306237](https://github.com/nataliariosmoreno11-dev/pulso-transmi-mlops/actions/runs/36193306237)

Como evidencia adicional, el ciclo de las 8:00 a. m. había sido aceptado con el recibo `sub_1bda9f6e8e0c4e39916408f4dd6ef802`, también con **48 de 48** predicciones.

## Solicitud sugerida al profesor

> Profesor, solicito respetuosamente revisar mi resultado de la actividad. En el corte aparezco con 19/36 ciclos y 52,8 % de cobertura. La automatización sí ejecutó, pero varios envíos fueron rechazados por un carácter no admitido en la versión del modelo. Adjunto la ejecución donde se observa el error, el commit que lo corrigió y el recibo posterior de 48/48 predicciones aceptadas. Entiendo que los ciclos ya cerrados no se pueden reenviar mediante la API; por eso quisiera pedir una oportunidad de recuperación o una revisión basada en la evidencia del pipeline implementado y corregido.

## Límite técnico

Los ciclos cerrados no pueden recuperarse desde los endpoints disponibles. Este documento demuestra la causa, la corrección y los envíos posteriores; cualquier ajuste del corte o de la nota requiere revisión del profesor.
