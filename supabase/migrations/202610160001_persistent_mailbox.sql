-- Persistent mailbox schema for Today Mail Agent.
-- The backend writes through the Supabase service key after verifying the
-- signed-in user's identity. Browser clients receive read-only access to
-- mailbox data through RLS and cannot read the encrypted OAuth tokens.

create table if not exists public.users (
    user_id uuid primary key references auth.users (id) on delete cascade,
    email text not null,
    access_token text,
    refresh_token text,
    token_expiry timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

comment on column public.users.access_token is
    'Encrypted OAuth access token ciphertext; backend only.';
comment on column public.users.refresh_token is
    'Encrypted OAuth refresh token ciphertext; backend only.';

create table if not exists public.emails (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    gmail_message_id text not null,
    thread_id text not null default '',
    sender text not null default '',
    subject text not null default '(No subject)',
    snippet text not null default '',
    internal_date bigint not null,
    gmail_labels jsonb not null default '[]'::jsonb,
    ai_label text,
    ai_label_status text not null default 'unlabeled'
        check (ai_label_status in ('labeled', 'unlabeled', 'failed')),
    deleted boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint emails_user_gmail_message_unique unique (user_id, gmail_message_id),
    constraint emails_id_user_unique unique (id, user_id)
);

create index if not exists emails_user_internal_date_idx
    on public.emails (user_id, internal_date desc);

create index if not exists emails_user_ai_label_idx
    on public.emails (user_id, ai_label);

create table if not exists public.email_bodies (
    email_id uuid primary key,
    user_id uuid not null references auth.users (id) on delete cascade,
    body text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint email_bodies_email_owner_fk
        foreign key (email_id, user_id)
        references public.emails (id, user_id)
        on delete cascade
);

create table if not exists public.sync_state (
    user_id uuid primary key references auth.users (id) on delete cascade,
    last_history_id text,
    last_internal_date bigint,
    initial_sync_complete boolean not null default false,
    is_syncing boolean not null default false,
    sync_started_at timestamptz,
    last_synced_at timestamptz,
    updated_at timestamptz not null default now()
);

-- Users can read only their own profile and mailbox rows. Only safe profile
-- columns are granted to browser clients; OAuth tokens stay backend-only.
alter table public.users enable row level security;
alter table public.emails enable row level security;
alter table public.email_bodies enable row level security;
alter table public.sync_state enable row level security;

drop policy if exists users_read_own on public.users;
create policy users_read_own on public.users
    for select to authenticated
    using (auth.uid() = user_id);

drop policy if exists emails_read_own on public.emails;
create policy emails_read_own on public.emails
    for select to authenticated
    using (auth.uid() = user_id);

drop policy if exists email_bodies_read_own on public.email_bodies;
create policy email_bodies_read_own on public.email_bodies
    for select to authenticated
    using (auth.uid() = user_id);

drop policy if exists sync_state_read_own on public.sync_state;
create policy sync_state_read_own on public.sync_state
    for select to authenticated
    using (auth.uid() = user_id);

revoke all on table public.users from anon, authenticated;
revoke all on table public.emails from anon, authenticated;
revoke all on table public.email_bodies from anon, authenticated;
revoke all on table public.sync_state from anon, authenticated;

grant select (user_id, email, token_expiry) on public.users to authenticated;
grant select on public.emails to authenticated;
grant select on public.email_bodies to authenticated;
grant select on public.sync_state to authenticated;

grant all on table public.users to service_role;
grant all on table public.emails to service_role;
grant all on table public.email_bodies to service_role;
grant all on table public.sync_state to service_role;

-- Keep updated_at accurate when backend upserts existing records.
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists users_set_updated_at on public.users;
create trigger users_set_updated_at
    before update on public.users
    for each row execute function public.set_updated_at();

drop trigger if exists emails_set_updated_at on public.emails;
create trigger emails_set_updated_at
    before update on public.emails
    for each row execute function public.set_updated_at();

drop trigger if exists email_bodies_set_updated_at on public.email_bodies;
create trigger email_bodies_set_updated_at
    before update on public.email_bodies
    for each row execute function public.set_updated_at();

drop trigger if exists sync_state_set_updated_at on public.sync_state;
create trigger sync_state_set_updated_at
    before update on public.sync_state
    for each row execute function public.set_updated_at();
