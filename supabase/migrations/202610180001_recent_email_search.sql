-- Search a signed-in user's saved email bodies for Ask AI.
-- The server calls this with its Supabase service key; browser roles cannot run it.

create or replace function public.search_recent_emails(
    p_user_id uuid,
    p_query text,
    p_connection_id uuid default null,
    p_limit integer default 5
)
returns table (
    id uuid,
    connection_id uuid,
    sender text,
    subject text,
    snippet text,
    internal_date bigint,
    ai_label text,
    body text,
    relevance real
)
language sql
stable
security invoker
set search_path = public
as $$
    with search_input as (
        select websearch_to_tsquery(
            'english',
            regexp_replace(coalesce(trim(p_query), ''), '\s+', ' OR ', 'g')
        ) as terms
    ),
    recent_mail as (
        select
            e.id,
            e.connection_id,
            e.sender,
            e.subject,
            e.snippet,
            e.internal_date,
            e.ai_label,
            coalesce(b.body, '') as body,
            setweight(to_tsvector('english', coalesce(e.subject, '')), 'A')
                || setweight(to_tsvector('english', coalesce(e.sender, '')), 'B')
                || setweight(to_tsvector('english', coalesce(e.snippet, '')), 'C')
                || setweight(to_tsvector('english', coalesce(b.body, '')), 'D') as document
        from public.emails e
        left join public.email_bodies b
            on b.email_id = e.id
           and b.user_id = e.user_id
        where e.user_id = p_user_id
          and (p_connection_id is null or e.connection_id = p_connection_id)
          and e.internal_date >= (extract(epoch from (now() - interval '24 hours')) * 1000)::bigint
          and e.deleted = false
          and e.hidden_in_app = false
    )
    select
        recent_mail.id,
        recent_mail.connection_id,
        recent_mail.sender,
        recent_mail.subject,
        recent_mail.snippet,
        recent_mail.internal_date,
        recent_mail.ai_label,
        recent_mail.body,
        ts_rank(recent_mail.document, search_input.terms)::real as relevance
    from recent_mail
    cross join search_input
    where numnode(search_input.terms) > 0
      and recent_mail.document @@ search_input.terms
    order by relevance desc, recent_mail.internal_date desc
    limit greatest(1, least(coalesce(p_limit, 5), 5));
$$;

revoke all on function public.search_recent_emails(uuid, text, uuid, integer)
    from public, anon, authenticated;
grant execute on function public.search_recent_emails(uuid, text, uuid, integer)
    to service_role;
