-- Store user-approved Gmail replies that should be sent later.
-- Apply this migration in Supabase before implementing scheduled sending.

begin;

create table if not exists public.scheduled_replies (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    connection_id uuid not null,
    email_id uuid not null,
    gmail_message_id text not null,
    thread_id text not null,
    in_reply_to_message_id text not null,
    references_header text not null default '',
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
    constraint scheduled_replies_connection_owner_fk
        foreign key (connection_id, user_id)
        references public.gmail_connections (id, user_id)
        on delete cascade,
    constraint scheduled_replies_email_owner_fk
        foreign key (email_id, user_id)
        references public.emails (id, user_id)
        on delete cascade
);

create index if not exists scheduled_replies_due_idx
    on public.scheduled_replies (send_at)
    where status = 'pending';

create index if not exists scheduled_replies_user_created_idx
    on public.scheduled_replies (user_id, created_at desc);

-- Claim due rows in one transaction so overlapping timer runs cannot both
-- send the same scheduled reply.
create or replace function public.claim_due_scheduled_replies(p_limit integer default 20)
returns setof public.scheduled_replies
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
    return query
    with due as (
        select sr.id
        from public.scheduled_replies sr
        where sr.status = 'pending'
          and sr.send_at <= now()
        order by sr.send_at
        for update skip locked
        limit greatest(1, least(coalesce(p_limit, 20), 50))
    )
    update public.scheduled_replies sr
    set status = 'sending',
        claimed_at = now(),
        attempt_count = sr.attempt_count + 1
    from due
    where sr.id = due.id
    returning sr.*;
end;
$$;

revoke all on function public.claim_due_scheduled_replies(integer)
    from public, anon, authenticated;
grant execute on function public.claim_due_scheduled_replies(integer)
    to service_role;

alter table public.scheduled_replies enable row level security;

drop policy if exists scheduled_replies_read_own on public.scheduled_replies;
create policy scheduled_replies_read_own on public.scheduled_replies
    for select to authenticated
    using (auth.uid() = user_id);

revoke all on table public.scheduled_replies from anon, authenticated;
grant select on table public.scheduled_replies to authenticated;
grant all on table public.scheduled_replies to service_role;

drop trigger if exists scheduled_replies_set_updated_at on public.scheduled_replies;
create trigger scheduled_replies_set_updated_at
    before update on public.scheduled_replies
    for each row execute function public.set_updated_at();

comment on table public.scheduled_replies is
    'Approved Gmail replies waiting to be sent by the backend scheduler.';
comment on column public.scheduled_replies.body is
    'Final user-approved reply text; never send a model draft before user approval.';
comment on column public.scheduled_replies.last_error is
    'Short sanitized error for support; do not store provider response bodies or tokens.';

commit;
