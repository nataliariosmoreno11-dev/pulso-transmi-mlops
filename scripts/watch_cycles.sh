#!/usr/bin/env bash
set -uo pipefail

attempts=${PULSO_WATCH_ATTEMPTS:-45}
delay=${PULSO_WATCH_DELAY_SECONDS:-300}
failures=0
last_failed=0
for ((attempt=1; attempt<=attempts; attempt++)); do
  echo "Consulta ${attempt}/${attempts} - $(date -u +%FT%TZ)"
  if git pull --ff-only origin main && python -m pulso_transmi.submit_current_cycle; then
    last_failed=0
  else
    failures=$((failures + 1))
    last_failed=1
    echo "::warning::Consulta ${attempt} falló; se volverá a consultar sin detener el vigilante."
  fi
  if ((attempt < attempts)); then sleep "$delay"; fi
done
echo "Vigilancia terminada: ${attempts} consultas; ${failures} fallos."
if ((last_failed)); then
  echo "::error::La última consulta falló; revisar logs y ejecutar el respaldo de entrega."
  exit 1
fi
