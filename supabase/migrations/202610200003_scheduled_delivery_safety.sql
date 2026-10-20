-- Keep uncertain Gmail deliveries out of the retry queue.

begin;

alter table public.scheduled_replies
    add column if not exists sent_gmail_message_id text;
alter table public.scheduled_messages
    add column if not exists sent_gmail_message_id text;

alter table public.scheduled_replies
    drop constraint if exists scheduled_replies_status_check;
alter table public.scheduled_replies
    add constraint scheduled_replies_status_check
    check (status in ('pending', 'sending', 'sent', 'failed', 'cancelled', 'delivery_unknown'));

alter table public.scheduled_messages
    drop constraint if exists scheduled_messages_status_check;
alter table public.scheduled_messages
    add constraint scheduled_messages_status_check
    check (status in ('pending', 'sending', 'sent', 'failed', 'cancelled', 'delivery_unknown'));

-- The old worker used this same error for both genuine send failures and
-- failures to save the status after a successful Gmail send. Treat those old
-- rows as uncertain so they cannot be retried and sent twice.
update public.scheduled_replies
set status = 'delivery_unknown',
    last_error = 'A previous attempt may have sent this reply. Check Gmail Sent before retrying.'
where status = 'failed'
  and last_error = 'Delivery failed. Reopen the app to review and retry.';

update public.scheduled_messages
set status = 'delivery_unknown',
    last_error = 'A previous attempt may have sent this email. Check Gmail Sent before retrying.'
where status = 'failed'
  and last_error = 'Delivery failed. Reopen the app to review and retry.';

-- A worker interrupted while sending cannot safely be retried automatically.
update public.scheduled_replies
set status = 'delivery_unknown',
    last_error = 'A previous attempt may have sent this reply. Check Gmail Sent before retrying.'
where status = 'sending'
  and claimed_at < now() - interval '10 minutes';

update public.scheduled_messages
set status = 'delivery_unknown',
    last_error = 'A previous attempt may have sent this email. Check Gmail Sent before retrying.'
where status = 'sending'
  and claimed_at < now() - interval '10 minutes';

comment on column public.scheduled_replies.sent_gmail_message_id is
    'Gmail message ID returned after sending; used to reconcile delivery status.';
comment on column public.scheduled_messages.sent_gmail_message_id is
    'Gmail message ID returned after sending; used to reconcile delivery status.';

commit;
