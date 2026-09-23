begin;
create table public.monitoreo_modelo (
 id_monitoreo uuid primary key default gen_random_uuid(),
 calculado_en timestamptz not null default now(),
 version_modelo text not null,
 ciclos_totales integer not null,
 predicciones_totales integer not null,
 predicciones_evaluadas integer not null,
 cobertura numeric not null check (cobertura between 0 and 1),
 wape_global numeric,
 accuracy_promedio_estaciones numeric,
 drift_demanda numeric,
 decision text not null check (decision in ('esperar','conservar','reentrenar')),
 detalle jsonb not null default '{}'::jsonb
);
create index monitoreo_modelo_fecha_idx on public.monitoreo_modelo(calculado_en desc);
alter table public.monitoreo_modelo enable row level security;
revoke all on table public.monitoreo_modelo from anon, authenticated;
grant select,insert,update,delete on table public.monitoreo_modelo to service_role;
commit;
