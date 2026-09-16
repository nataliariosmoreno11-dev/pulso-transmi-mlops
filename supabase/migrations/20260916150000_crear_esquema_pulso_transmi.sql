-- Esquema inicial del proyecto estudiantil Pulso TransMi.
-- Nombres en español, tiempos absolutos (timestamptz), IDs de estación como texto.
-- La API de lectura es la fuente; esta migración no importa datos.

begin;

create table public.estaciones (
    id_estacion text primary key,
    nombre text not null,
    corredor text not null,
    latitud numeric(11, 8) not null check (latitud between -90 and 90),
    longitud numeric(11, 8) not null check (longitud between -180 and 180),
    creado_en timestamptz not null default now()
);

create table public.contexto (
    instante timestamptz primary key,
    lluvia_mm numeric not null check (lluvia_mm >= 0),
    pronostico_lluvia numeric not null check (pronostico_lluvia >= 0),
    temperatura_c numeric not null,
    pronostico_temperatura numeric not null,
    intensidad_evento numeric not null check (intensidad_evento between 0 and 1),
    recibido_en timestamptz not null default now()
);

create table public.observaciones (
    id_estacion text not null references public.estaciones(id_estacion),
    instante timestamptz not null,
    demanda integer not null check (demanda >= 0),
    recibido_en timestamptz not null default now(),
    primary key (id_estacion, instante)
);
create index observaciones_instante_idx on public.observaciones (instante);

create table public.estado_ingesta (
    recurso text primary key,
    ultimo_instante timestamptz,
    cursor text,
    actualizado_en timestamptz not null default now(),
    check (length(trim(recurso)) > 0)
);

create table public.ejecuciones_pipeline (
    id_ejecucion uuid primary key default gen_random_uuid(),
    inicio timestamptz not null default now(),
    fin timestamptz,
    estado text not null default 'en_curso'
        check (estado in ('en_curso', 'exitosa', 'fallida')),
    origen text not null default 'manual',
    commit_codigo text,
    corte_datos timestamptz,
    decision_reentrenar text not null default 'pendiente'
        check (decision_reentrenar in ('pendiente', 'conservar', 'reentrenar')),
    motivo_decision text,
    error text,
    check (fin is null or fin >= inicio)
);
create index ejecuciones_pipeline_inicio_idx on public.ejecuciones_pipeline (inicio desc);

create table public.versiones_modelo (
    id_modelo uuid primary key default gen_random_uuid(),
    id_ejecucion_entrenamiento uuid not null
        references public.ejecuciones_pipeline(id_ejecucion),
    tipo_modelo text not null,
    uri_artefacto text,
    hash_artefacto text,
    variables jsonb not null default '[]'::jsonb,
    corte_entrenamiento timestamptz not null,
    creada_en timestamptz not null default now(),
    check (jsonb_typeof(variables) = 'array')
);
create index versiones_modelo_entrenamiento_idx
    on public.versiones_modelo (id_ejecucion_entrenamiento);

create table public.metricas_validacion (
    id_metrica uuid primary key default gen_random_uuid(),
    id_modelo uuid not null references public.versiones_modelo(id_modelo),
    id_estacion text not null references public.estaciones(id_estacion),
    ventana_inicio timestamptz not null,
    ventana_fin timestamptz not null,
    wape numeric not null check (wape >= 0),
    accuracy numeric not null check (accuracy between 0 and 100),
    cantidad_registros integer not null check (cantidad_registros > 0),
    calculada_en timestamptz not null default now(),
    unique (id_modelo, id_estacion, ventana_inicio, ventana_fin),
    check (ventana_fin >= ventana_inicio)
);
create index metricas_validacion_estacion_ventana_idx
    on public.metricas_validacion (id_estacion, ventana_fin desc);

create table public.predicciones (
    id_prediccion uuid primary key default gen_random_uuid(),
    id_ejecucion uuid not null references public.ejecuciones_pipeline(id_ejecucion),
    id_modelo uuid not null references public.versiones_modelo(id_modelo),
    id_estacion text not null references public.estaciones(id_estacion),
    creada_en timestamptz not null,
    instante_objetivo timestamptz not null,
    horizonte_minutos integer not null check (horizonte_minutos > 0),
    demanda_predicha numeric not null check (demanda_predicha >= 0),
    estado_envio text not null default 'pendiente',
    unique (id_ejecucion, id_estacion, instante_objetivo, horizonte_minutos),
    check (instante_objetivo > creada_en)
);
create index predicciones_estacion_objetivo_idx
    on public.predicciones (id_estacion, instante_objetivo);
create index predicciones_modelo_idx on public.predicciones (id_modelo);

create table public.evaluaciones_prediccion (
    id_prediccion uuid primary key references public.predicciones(id_prediccion),
    demanda_real integer not null check (demanda_real >= 0),
    error_absoluto numeric not null check (error_absoluto >= 0),
    evaluada_en timestamptz not null default now()
);

create table public.senales_cambio (
    id_senal uuid primary key default gen_random_uuid(),
    id_ejecucion uuid not null references public.ejecuciones_pipeline(id_ejecucion),
    id_estacion text references public.estaciones(id_estacion),
    tipo_senal text not null,
    nombre_metrica text not null,
    valor numeric not null,
    umbral numeric not null,
    hay_alerta boolean not null,
    evaluada_en timestamptz not null default now()
);
create index senales_cambio_ejecucion_estacion_idx
    on public.senales_cambio (id_ejecucion, id_estacion);

-- Supabase expone el esquema public por la Data API. Mantenerlo cerrado hasta
-- definir explícitamente qué datos puede leer el dashboard y bajo qué rol.
alter table public.estaciones enable row level security;
alter table public.contexto enable row level security;
alter table public.observaciones enable row level security;
alter table public.estado_ingesta enable row level security;
alter table public.ejecuciones_pipeline enable row level security;
alter table public.versiones_modelo enable row level security;
alter table public.metricas_validacion enable row level security;
alter table public.predicciones enable row level security;
alter table public.evaluaciones_prediccion enable row level security;
alter table public.senales_cambio enable row level security;

revoke all on table
    public.estaciones, public.contexto, public.observaciones,
    public.estado_ingesta, public.ejecuciones_pipeline, public.versiones_modelo,
    public.metricas_validacion, public.predicciones,
    public.evaluaciones_prediccion, public.senales_cambio
from anon, authenticated;

grant select, insert, update, delete on table
    public.estaciones, public.contexto, public.observaciones,
    public.estado_ingesta, public.ejecuciones_pipeline, public.versiones_modelo,
    public.metricas_validacion, public.predicciones,
    public.evaluaciones_prediccion, public.senales_cambio
 to service_role;

commit;
