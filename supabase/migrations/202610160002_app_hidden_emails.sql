-- Let a user hide an email in Today Mail without deleting it from Gmail.
alter table public.emails
    add column if not exists hidden_in_app boolean not null default false;

comment on column public.emails.hidden_in_app is
    'When true, hide this email from the owning user’s app inbox; Gmail remains unchanged.';
