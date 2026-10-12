-- Gmail refresh tokens are encrypted by the Render backend before storage.
-- Browser users receive no table permissions; only the trusted backend uses
-- the Supabase secret/service-role key after checking the user's Auth session.
create table if not exists public.gmail_connections (
    user_id uuid primary key references auth.users (id) on delete cascade,
    gmail_email text not null,
    refresh_token_ciphertext text not null,
    connected_at timestamptz not null default now()
);

alter table public.gmail_connections enable row level security;
revoke all on table public.gmail_connections from anon, authenticated;
grant all on table public.gmail_connections to service_role;
