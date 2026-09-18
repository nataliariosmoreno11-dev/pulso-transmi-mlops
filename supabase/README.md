# Migración a Supabase

La migración [20260916150000_crear_esquema_pulso_transmi.sql](migrations/20260916150000_crear_esquema_pulso_transmi.sql) convierte el [modelo ER](../docs/proposed-data-model.md) en **10 tablas PostgreSQL** con claves, relaciones, validaciones e índices. No carga las 51.840 observaciones; crea únicamente el esquema. Para los datos se usa [load_api_data.py](load_api_data.py).

## Qué se comprobó

La migración se aplicó de principio a fin en PostgreSQL 18 local temporal. Se verificaron las 10 tablas, RLS activo en todas y una inserción de estación + observación relacionada. La instancia temporal se eliminó tras la prueba.

## Aplicación al proyecto Supabase

Se necesita un proyecto Supabase conectado, o una conexión PostgreSQL autorizada. Con la CLI oficial, desde la raíz del repositorio:

```bash
supabase init                 # solo si el proyecto local aún no tiene supabase/config.toml
supabase login
supabase link --project-ref TU_REFERENCIA_DE_PROYECTO
supabase migration list      # revisar el historial remoto antes de aplicar
supabase db push --dry-run    # previsualizar qué migraciones se aplicarían
supabase db push             # aplica migraciones pendientes en orden
supabase migration list      # confirmar que esta versión aparece aplicada
```

Si el proyecto remoto ya tiene tablas o migraciones propias, primero hay que revisar ese estado para evitar conflictos. Conviene aplicar esta migración por el flujo de migraciones, no copiarla en el editor SQL remoto, porque Supabase registra así qué versiones fueron aplicadas.

## Acceso a los datos

La migración habilita **Row Level Security (RLS)** en las 10 tablas. Revoca acceso a `anon` y `authenticated` y concede operaciones al rol `service_role`, que debe usarse solo desde el servidor o GitHub Actions. El dashboard actual sigue leyendo el snapshot local; no necesita credenciales de Supabase en el navegador. Cuando se decida mostrar datos directamente desde Supabase, hará falta otra migración con políticas y permisos de solo lectura adecuados.

La tabla `estado_ingesta` permite continuar descargas; `observaciones` usa la clave (`id_estacion`, `instante`) para admitir ingesta idempotente. El esquema no presupone los cuatro horizontes exactos ni el contrato definitivo de envíos, que aún no están publicados.

## Cargar los datos de la API

Después de aplicar la migración, crea `.env` en la raíz del repositorio (Git lo ignora) con la URI completa copiada de **Connect → Direct → Session pooler → URI**, conservando literalmente `[YOUR-PASSWORD]`, y la contraseña de la base de datos en otra línea:

```text
PULSO_DATABASE_URL=postgresql://usuario:[YOUR-PASSWORD]@servidor:5432/postgres
PULSO_DB_PASSWORD=tu_contraseña_de_base_de_datos
```

El ejemplo muestra marcadores: pega la URI real que te da Supabase. El programa sustituye `[YOUR-PASSWORD]` y codifica los caracteres especiales de la contraseña. No compartas `.env` ni lo agregues a Git. Luego ejecuta:

```bash
python3 supabase/load_api_data.py
```

El script requiere `psql` en `PATH`. Descarga los CSV actuales de estaciones, contexto y observaciones, comprueba sus SHA-256 frente a `/v1/meta`, consulta también `/v1/stream/observations` y carga todo dentro de una transacción. Usa `upsert` para que una segunda ejecución no duplique datos. No imprime la cadena de conexión ni guarda credenciales en archivos del repositorio.

En la comprobación local del 18 de septiembre de 2026, la API ofreció 12 estaciones, 4.320 intervalos de contexto, 51.840 observaciones iniciales y **0 filas nuevas en el stream**. La carga se ejecutó dos veces en PostgreSQL temporal y mantuvo exactamente esos conteos. Cuando el stream libere datos, el mismo script incluirá esas filas en `observaciones`.
