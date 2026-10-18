# Today Mail Agent

A small Gmail dashboard that shows messages from the last 24 hours, classifies them with an LLM, applies Gmail labels, and summarizes a selected email.

## How it works

- **Frontend:** `frontend/` contains the dashboard.
- **Backend:** `backend/` handles sign-in, Gmail, classification, summaries, and orchestration.
- **Supabase:** app accounts, encrypted Gmail refresh tokens, saved email details, and readable email bodies. The dashboard and Ask AI read saved data from Supabase.
- **Ask AI:** searches the signed-in user's saved emails from the last 24 hours, sends up to five relevant matches to the configured model, and returns links to source emails.
- **OpenRouter:** sends classification, summary, and Ask AI requests to the configured model.
- **GitHub Actions:** calls Render about every 30 minutes to classify new messages and refresh the cache, even when nobody has the dashboard open.
- **Langfuse:** optional model-call tracing.
- **Backend timing logs:** `APP_TIMING` entries show request-correlated durations for Gmail, Supabase, and model steps in the service logs.

## Run locally

1. Install Python 3.10 or newer and create `.env` from `.env.example`.
2. Install dependencies:

   ```powershell
   py -m pip install -r requirements-dev.txt
   ```

3. Store `OPENROUTER_API_KEY` in Windows Credential Manager. If using Langfuse, store `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` there too. Do not put secrets in `.env` or commit them.
4. For local Gmail access, enable the Gmail API in Google Cloud, create a **Desktop app** OAuth client, and save its JSON file as `%LOCALAPPDATA%\TodayMailAgent\credentials.json`.
5. Start the dashboard:

   ```powershell
   py -m uvicorn backend.web:app --reload
   ```

6. Open <http://127.0.0.1:8000>, sign in if prompted, and connect Gmail.

## Tests and evaluation

- Run tests: `py -m pytest backend/tests -v`
- Run the baseline and current classification evaluations: `py -m evals.run_eval --compare` (34 model calls; may use OpenRouter credits).
- Summary rate limits are set by `MODEL_REQUESTS_PER_MINUTE` and `MODEL_REQUESTS_PER_DAY` in `backend/web.py`.

## Deploy

The app is hosted on Render. Set its private environment variables there; never commit secrets.

Required for hosted Gmail and user accounts:

- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SECRET_KEY`
- `REQUIRE_APP_AUTH=true`
- `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`
- `GOOGLE_OAUTH_REDIRECT_URI=https://YOUR-RENDER-SERVICE.onrender.com/api/connect-gmail/callback`
- `GMAIL_TOKEN_ENCRYPTION_KEY` (a Fernet key)
- `OPENROUTER_API_KEY`

Optional: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_BASE_URL` (for Langfuse Cloud, `https://cloud.langfuse.com`).

To enable Ask AI, run `supabase/migrations/202610180001_recent_email_search.sql` once in the Supabase SQL Editor. Earlier migrations should not be rerun if they have already been applied. Add `SCHEDULED_JOB_TOKEN` to Render and as a GitHub Actions repository secret; add `RENDER_APP_URL` as another repository secret. GitHub Actions calls `/api/internal/process-recent-emails` on its schedule. Scheduled runs can be delayed.

## Privacy and limitations

- Gmail access is granted through Google OAuth; the app never asks for a Gmail password.
- Supabase stores saved email details and bodies. The dashboard and Ask AI show/search only the rolling last 24 hours; hidden or deleted emails are excluded.
- Timing logs include request IDs, step names, durations, and counts; they do not include email text, email addresses, or tokens.
- Langfuse is optional. Traces include model timings and token counts, not email bodies or summary text.
- Google may require OAuth verification and a security assessment before a public release that uses Gmail's restricted `gmail.modify` scope.
- This app does not send or reply to emails.
