-- Give each connected Gmail mailbox its own identity, email rows, and sync state.
-- Run only after reviewing the migration and backing up the current database.

begin;

alter table public.gmail_connections
    add column if not exists id uuid;

update public.gmail_connections
set id = gen_random_uuid()
where id is null;

alter table public.gmail_connections
    alter column id set default gen_random_uuid(),
    alter column id set not null;

alter table public.gmail_connections
    drop constraint if exists gmail_connections_pkey;

alter table public.gmail_connections
    add constraint gmail_connections_pkey primary key (id);

alter table public.gmail_connections
    add constraint gmail_connections_id_user_unique unique (id, user_id);

create unique index if not exists gmail_connections_user_email_unique_idx
    on public.gmail_connections (user_id, lower(gmail_email));

alter table public.emails
    add column if not exists connection_id uuid;

update public.emails e
set connection_id = gc.id
from public.gmail_connections gc
where gc.user_id = e.user_id
  and e.connection_id is null;

do $$
begin
    if exists (select 1 from public.emails where connection_id is null) then
        raise exception 'Some emails have no Gmail connection; migration stopped.';
    end if;
end;
$$;

alter table public.emails
    alter column connection_id set not null,
    add column if not exists classification_attempted_at timestamptz;

alter table public.emails
    drop constraint if exists emails_user_gmail_message_unique;

alter table public.emails
    add constraint emails_connection_gmail_message_unique
    unique (connection_id, gmail_message_id);

alter table public.emails
    add constraint emails_connection_owner_fk
    foreign key (connection_id, user_id)
    references public.gmail_connections (id, user_id)
    on delete cascade;

create index if not exists emails_connection_internal_date_idx
    on public.emails (connection_id, internal_date desc);

create index if not exists emails_connection_ai_label_idx
    on public.emails (connection_id, ai_label);

alter table public.sync_state
    add column if not exists connection_id uuid;

update public.sync_state s
set connection_id = gc.id
from public.gmail_connections gc
where gc.user_id = s.user_id
  and s.connection_id is null;

do $$
begin
    if exists (select 1 from public.sync_state where connection_id is null) then
        raise exception 'Some sync states have no Gmail connection; migration stopped.';
    end if;
end;
$$;

alter table public.sync_state
    alter column connection_id set not null;

alter table public.sync_state
    drop constraint if exists sync_state_pkey;

alter table public.sync_state
    add constraint sync_state_pkey primary key (connection_id);

alter table public.sync_state
    add constraint sync_state_connection_owner_fk
    foreign key (connection_id, user_id)
    references public.gmail_connections (id, user_id)
    on delete cascade;

-- This backend-only function serializes connection attempts per app user and
-- enforces the two-account limit even if two OAuth callbacks arrive together.
create or replace function public.save_gmail_connection(
    p_user_id uuid,
    p_gmail_email text,
    p_refresh_token_ciphertext text
)
returns table (id uuid, user_id uuid, gmail_email text, connected_at timestamptz)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    saved public.gmail_connections%rowtype;
    account_count integer;
begin
    perform pg_advisory_xact_lock(hashtextextended(p_user_id::text, 0));

    select * into saved
    from public.gmail_connections gc
    where gc.user_id = p_user_id
      and lower(gc.gmail_email) = lower(p_gmail_email)
    for update;

    if found then
        update public.gmail_connections gc
        set refresh_token_ciphertext = p_refresh_token_ciphertext
        where gc.id = saved.id
        returning gc.* into saved;
        return query select saved.id, saved.user_id, saved.gmail_email, saved.connected_at;
        return;
    end if;

    select count(*) into account_count
    from public.gmail_connections gc
    where gc.user_id = p_user_id;

    if account_count >= 2 then
        raise exception 'GMAIL_ACCOUNT_LIMIT';
    end if;

    insert into public.gmail_connections (user_id, gmail_email, refresh_token_ciphertext)
    values (p_user_id, p_gmail_email, p_refresh_token_ciphertext)
    returning * into saved;

    return query select saved.id, saved.user_id, saved.gmail_email, saved.connected_at;
end;
$$;

revoke all on function public.save_gmail_connection(uuid, text, text)
    from public, anon, authenticated;
grant execute on function public.save_gmail_connection(uuid, text, text)
    to service_role;

commit;
