-- Store user-approved new emails that should be delivered later.

begin;

create table if not exists public.scheduled_messages (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    connection_id uuid not null,
    recipient_email text not null,
    subject text not null,
    body text not null,
    send_at timestamptz not null,
    status text not null default 'pending'
        check (status in ('pending', 'sending', 'sent', 'failed', 'cancelled')),
    attempt_count integer not null default 0 check (attempt_count >= 0),
    claimed_at timestamptz,
    sent_at timestamptz,
    last_error text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint scheduled_messages_connection_owner_fk
        foreign key (connection_id, user_id)
        references public.gmail_connections (id, user_id)
        on delete cascade
);

create index if not exists scheduled_messages_due_idx
    on public.scheduled_messages (send_at)
    where status = 'pending';

create index if not exists scheduled_messages_user_created_idx
    on public.scheduled_messages (user_id, created_at desc);

create or replace function public.claim_due_scheduled_messages(p_limit integer default 20)
returns setof public.scheduled_messages
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
    return query
    with due as (
        select sm.id
        from public.scheduled_messages sm
        where sm.status = 'pending'
          and sm.send_at <= now()
        order by sm.send_at
        for update skip locked
        limit greatest(1, least(coalesce(p_limit, 20), 50))
    )
    update public.scheduled_messages sm
    set status = 'sending',
        claimed_at = now(),
        attempt_count = sm.attempt_count + 1
    from due
    where sm.id = due.id
    returning sm.*;
end;
$$;

revoke all on function public.claim_due_scheduled_messages(integer)
    from public, anon, authenticated;
grant execute on function public.claim_due_scheduled_messages(integer)
    to service_role;

alter table public.scheduled_messages enable row level security;

drop policy if exists scheduled_messages_read_own on public.scheduled_messages;
create policy scheduled_messages_read_own on public.scheduled_messages
    for select to authenticated
    using (auth.uid() = user_id);

revoke all on table public.scheduled_messages from anon, authenticated;
grant select on table public.scheduled_messages to authenticated;
grant all on table public.scheduled_messages to service_role;

drop trigger if exists scheduled_messages_set_updated_at on public.scheduled_messages;
create trigger scheduled_messages_set_updated_at
    before update on public.scheduled_messages
    for each row execute function public.set_updated_at();

comment on table public.scheduled_messages is
    'User-approved new Gmail messages waiting for backend delivery.';
comment on column public.scheduled_messages.body is
    'Final user-approved email text; never send a model draft before user approval.';

commit;
