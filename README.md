# Today's Mail Agent

A local email dashboard that uses Google OAuth and the Gmail API to show recent inbox messages, filter them by Gmail labels, and summarize one selected email through OpenRouter.

## Project layout

```text
frontend/   Browser dashboard, styling, and interactions
backend/    Web API, runtime, model client, and Gmail operations
.env        Local, non-secret settings
```

## Requirements

- Python 3.10 or newer
- An OpenRouter API key (free models do not need purchased credits; paid models do)
- A Google Cloud project with the Gmail API enabled
- A Google Desktop app OAuth client for local development
- Langfuse is optional; install it to record traces

## Configure

Copy `.env.example` to `.env` for ordinary settings such as timezone and model name. Store the OpenRouter and optional Langfuse API keys in Windows Credential Manager; the app reads them automatically at startup. Gmail is connected separately through Google's OAuth sign-in. Do not put API keys or OAuth tokens in `.env`.

Install the project requirements after setting up Python:

```powershell
py -m pip install -r requirements.txt
```

Store the OpenRouter API key in Windows Credential Manager. Type it only at the hidden prompt:

```powershell
& "C:\Users\sreek\AppData\Local\Programs\Python\Python314\python.exe" -m keyring set today-mail-agent OPENROUTER_API_KEY
```

If you use Langfuse, store its keys the same way with the names `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`.

### Connect Gmail with OAuth

For this local version, Google opens its own sign-in page. The app never asks for your Gmail password or app password.

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project and enable the **Gmail API**.
2. Configure Google Auth Platform. Set the audience to **External** and publishing status to **Testing**, then add your Gmail account as a test user.
3. Create an OAuth client with application type **Desktop app** and download its JSON file.
4. Create `%LOCALAPPDATA%\TodayMailAgent` on your computer. Put the downloaded file there and rename it `credentials.json`.
5. Start the app and click **Connect Gmail**. Sign in to Google and approve the requested Gmail access.

The app saves the OAuth refresh token in Windows Credential Manager, not in the project folder. The downloaded Desktop app JSON stays under `%LOCALAPPDATA%\TodayMailAgent`, outside OneDrive. The app requests `gmail.modify` so it can read emails and apply existing labels. Google's scope description also includes composing and sending email, but this app has no send or reply feature. Google's testing-mode authorizations expire after seven days, so you may need to connect again while the app remains in Testing.

The `gmail.modify` scope is restricted. A public production app may need Google's OAuth verification; transmitting or storing restricted Gmail data on a server can also require a security assessment. See Google's [Gmail scope guide](https://developers.google.com/workspace/gmail/api/auth/scopes) and [OAuth testing guidance](https://support.google.com/cloud/answer/15549945?hl=en).

### Supabase sign-in prototype

Set `SUPABASE_URL` and `SUPABASE_ANON_KEY` in the hosting service's private environment settings to enable email/password app sign-in. Set `REQUIRE_APP_AUTH=true` on the hosted service so the backend refuses requests if the sign-in settings are missing. The browser receives only the URL and publishable key; the backend checks each signed-in session with Supabase Auth. In local development, leave these settings blank to keep using the existing local dashboard.

The Supabase sign-in identifies the app user. Each user must separately click **Connect Gmail** and approve access at Google. The server encrypts that user's Google refresh token and stores it in the `gmail_connections` table, keyed by the Supabase Auth user ID. The SQL migration is `supabase/migrations/202610120001_gmail_connections.sql`; run it in the Supabase SQL Editor before connecting Gmail. Row access is revoked from browser roles; only the backend's Supabase secret key can access the table.

For a hosted Gmail connection, set these additional environment variables on Render: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI` (exactly `https://your-render-name.onrender.com/api/connect-gmail/callback`), `SUPABASE_SECRET_KEY`, and `GMAIL_TOKEN_ENCRYPTION_KEY`. Generate a Fernet key in PowerShell with `py -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`; copy the output directly into Render and keep it private. In Google Cloud, use a **Web application** OAuth client and add that exact callback URL as an authorized redirect URI. The existing Desktop OAuth client remains for local development.

Google currently classifies the requested `gmail.modify` scope as restricted. Public release may require Google verification and, because the server stores/transmits restricted Gmail data, a security assessment. Keep this as a controlled test while developing.

### Scheduled Gmail classification

The GitHub Actions workflow in `.github/workflows/process-recent-emails.yml` runs twice an hour (at minutes 17 and 47 UTC). It calls the protected Render endpoint `/api/internal/process-recent-emails`. The backend finds Gmail connections in Supabase and reuses the existing recent-email fetch and classification functions for each connected app user. Each run checks the rolling last 24 hours; Gmail category labels prevent already-classified messages from being sent to the model again. The dashboard still loads its email list when the user refreshes it.

Set `SCHEDULED_JOB_TOKEN` as a private environment variable in Render, then add the same value as a GitHub Actions repository secret named `SCHEDULED_JOB_TOKEN`. Add the Render service URL as a repository secret named `RENDER_APP_URL` (for example, `https://your-app.onrender.com`). In GitHub, enable Actions for the repository. The workflow can also be started manually with **Run workflow**. A scheduled run can be delayed by GitHub, so it is not an exact clock.

The non-secret settings used by the app are:

| Variable | Example | Required |
| --- | --- | --- |
| `EMAIL_TIMEZONE` | `Asia/Kolkata` | No; uses the computer timezone |
| `EMAIL_MAX_RESULTS` | `50` | No |
| `GMAIL_PUBSUB_TOPIC` | `projects/your-project-id/topics/gmail-inbox` | No; enables Gmail push watch |
| `PUBSUB_PUSH_AUDIENCE` | `https://your-domain/api/gmail/notifications` | Required for authenticated push delivery |
| `PUBSUB_PUSH_SERVICE_ACCOUNT` | `push@your-project-id.iam.gserviceaccount.com` | Required for authenticated push delivery |
| `OPENROUTER_MODEL` | `nvidia/nemotron-3.5-lightning:free` | No |
| `OPENROUTER_URL` | `https://openrouter.ai/api/v1/chat/completions` | No |
| `LANGFUSE_BASE_URL` | `https://cloud.langfuse.com` | Optional; set the URL for your Langfuse region |
| `SUPABASE_URL` | `https://your-project.supabase.co` | Optional; enables app sign-in |
| `SUPABASE_ANON_KEY` | Supabase publishable/anon key | Optional; used by the browser for app sign-in |
| `REQUIRE_APP_AUTH` | `true` | Set on the hosted service to require app sign-in |
| `SCHEDULED_JOB_TOKEN` | Random private value | Required in Render to authenticate the GitHub scheduled workflow |
| `GOOGLE_OAUTH_CLIENT_ID` | Web OAuth client ID | Required for hosted Gmail connection |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Web OAuth client secret | Required for hosted Gmail connection; keep private |
| `GOOGLE_OAUTH_REDIRECT_URI` | `https://your-app.onrender.com/api/connect-gmail/callback` | Required for hosted Gmail connection |
| `SUPABASE_SECRET_KEY` | Supabase secret key (`sb_secret_...`) | Required by the backend to store encrypted user tokens; never use in the browser |
| `GMAIL_TOKEN_ENCRYPTION_KEY` | Fernet key | Required to encrypt Gmail refresh tokens before they are stored |

### Change the model

Choose the model in `.env` by editing only the `OPENROUTER_MODEL` line. For example:

```text
OPENROUTER_MODEL=nvidia/nemotron-3.5-lightning:free
```

Use the exact model slug shown on OpenRouter. Save `.env`, stop the running app with `Ctrl+C`, and start it again so the new model setting loads. The OpenRouter API key stays in Windows Credential Manager; it does not change when you switch models. Paid model requests use your OpenRouter balance.

## Optional Langfuse tracing

Create a Langfuse project and save its public and secret keys in Windows Credential Manager if you want tracing. Install the dependencies with:

```powershell
py -m pip install -r requirements.txt
```

Langfuse is disabled unless both keys are set. Traces record the model name, timings, tool-call counts, email count, and error type. They deliberately do not include the user's request, email content, prompts, or model summaries. Langfuse keys should be kept private and must not be committed.

## Run

```powershell
py -m backend.main
```

## Run the web interface

Install the project requirements, then start the local web app:

```powershell
py -m pip install -r requirements.txt
py -m uvicorn backend.web:app --reload
```

Open `http://127.0.0.1:8000` in your browser. Connect Gmail, then use **Refresh** to load Inbox messages received in the latest rolling 24 hours. Refresh also creates any missing category labels, classifies only emails without a category label, and applies the chosen labels in Gmail. Already categorized messages are skipped, so repeated refreshes do not send them to the model again. Select **Summarize** on a message to ask the model to summarize only that message. Classification runs when you refresh; this version does not keep its own 24-hour database or check Gmail in the background. It is for local use; cloud deployment needs a different sign-in and secret-storage setup than Windows Credential Manager.

The CLI agent still uses the Inbox and treats “today” according to `EMAIL_TIMEZONE`. The dashboard shows a rolling 24-hour window. The app can apply labels to emails but does not send or reply to messages.

### Gmail push notification prototype

The backend has a Pub/Sub push receiver at `/api/gmail/notifications`. It verifies Pub/Sub's OIDC token, reads Gmail's notification history ID, fetches newly added Inbox messages through Gmail history, and sends those messages through the existing classifier. The last handled history ID and watch expiration are stored in Windows Credential Manager for this local prototype.

To enable it, deploy the backend at a public HTTPS URL first. Set `GMAIL_PUBSUB_TOPIC` to the full topic path. Configure the Pub/Sub push subscription to POST to `https://your-domain/api/gmail/notifications` and enable OIDC authentication; set the exact token audience in `PUBSUB_PUSH_AUDIENCE` and the selected push identity email in `PUBSUB_PUSH_SERVICE_ACCOUNT`. Gmail's publisher identity (`gmail-api-push@system.gserviceaccount.com`) must have Pub/Sub Publisher permission on the topic; this is different from the identity used to authenticate Pub/Sub's push request.

The dashboard starts or renews Gmail's mailbox watch when it is refreshed, provided `GMAIL_PUBSUB_TOPIC` is configured. Gmail watches expire unless renewed at least every seven days, so a deployed always-on app still needs a scheduled renewal job. This prototype stores OAuth credentials and the history cursor in Windows Credential Manager; replace that local storage with secure per-user cloud storage before using the webhook in a cloud deployment. Until it has a public HTTPS endpoint, a local `localhost` server cannot receive Pub/Sub push requests.
