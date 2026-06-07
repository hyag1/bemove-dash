create extension if not exists pgcrypto;

create table if not exists app_users (
    id uuid primary key default gen_random_uuid(),
    username text not null unique,
    display_name text not null,
    password_hash text not null,
    role text not null default 'client',
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table app_users
add column if not exists role text not null default 'client';

update app_users
set role = 'admin'
where username = 'admin'
  and role <> 'admin';

create table if not exists app_user_clients (
    username text not null references app_users(username) on delete cascade,
    client_id text not null,
    created_at timestamptz not null default now(),
    primary key (username, client_id)
);

create table if not exists app_user_dashboards (
    username text not null references app_users(username) on delete cascade,
    client_id text not null,
    dashboard_id text not null,
    created_at timestamptz not null default now(),
    primary key (username, client_id, dashboard_id)
);

create table if not exists app_sessions (
    token_hash text primary key,
    username text not null references app_users(username) on delete cascade,
    created_at timestamptz not null default now(),
    expires_at timestamptz not null
);

create index if not exists idx_app_sessions_username on app_sessions (username);
create index if not exists idx_app_sessions_expires_at on app_sessions (expires_at);

create table if not exists evo_sync_runs (
    id bigserial primary key,
    client_id text not null,
    status text not null check (status in ('running', 'success', 'error')),
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    records_count integer not null default 0,
    error_message text
);

create table if not exists evo_members (
    client_id text not null,
    id_member text not null,
    payload jsonb not null,
    last_seen_sync_id bigint references evo_sync_runs(id) on delete set null,
    fetched_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (client_id, id_member)
);

create index if not exists idx_evo_members_client_id on evo_members (client_id);
create index if not exists idx_evo_members_payload_gin on evo_members using gin (payload);
create index if not exists idx_evo_sync_runs_client_started on evo_sync_runs (client_id, started_at desc);
