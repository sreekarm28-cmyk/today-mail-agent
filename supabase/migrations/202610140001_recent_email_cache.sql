-- Keep only lightweight dashboard fields. Email bodies remain in Gmail and
-- are fetched on demand when a user opens an email.
create table if not exists public.recent_emails (
    user_id uuid not null references auth.users (id) on delete cascade,
    gmail_message_id text not null,
    sender text not null default '',
    subject text not null default '(No subject)',
    received_at timestamptz not null,
    preview text not null default '',
    labels jsonb not null default '[]'::jsonb,
    primary key (user_id, gmail_message_id)
);

create index if not exists recent_emails_received_at_idx
    on public.recent_emails (user_id, received_at desc);

alter table public.recent_emails enable row level security;
revoke all on table public.recent_emails from anon, authenticated;
grant all on table public.recent_emails to service_role;
