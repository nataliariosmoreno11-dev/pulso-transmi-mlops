begin;
create table public.entregas_api (
 id_ciclo text primary key, version_modelo text not null, id_submission text not null unique,
 id_ejecucion_cliente text not null, clave_idempotencia text not null, estado text not null,
 intento integer not null check (intento > 0), corte_datos timestamptz not null, cierre_ciclo timestamptz,
 predicciones_recibidas integer not null check (predicciones_recibidas > 0),
 predicciones_esperadas integer not null check (predicciones_esperadas > 0),
 hash_payload text, recibo jsonb not null check (jsonb_typeof(recibo) = 'object'),
 aceptada_en timestamptz not null default now()
);
create table public.predicciones_api (
 id_ciclo text not null references public.entregas_api(id_ciclo) on delete cascade,
 id_estacion text not null references public.estaciones(id_estacion),
 instante_objetivo timestamptz not null, horizonte_minutos integer not null check (horizonte_minutos > 0),
 demanda_predicha numeric not null check (demanda_predicha >= 0),
 primary key (id_ciclo, id_estacion, instante_objetivo)
);
create index predicciones_api_objetivo_idx on public.predicciones_api (instante_objetivo, id_estacion);
alter table public.entregas_api enable row level security;
alter table public.predicciones_api enable row level security;
revoke all on table public.entregas_api, public.predicciones_api from anon, authenticated;
grant select, insert, update, delete on table public.entregas_api, public.predicciones_api to service_role;
commit;
