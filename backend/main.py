"""Load local development settings and saved local credentials."""

import os

from dotenv import load_dotenv
import keyring

def load_local_settings() -> None:
    """Load .env settings and local keyring secrets for the web app."""
    # Hosted services already provide these values as environment variables.
    load_dotenv()

    credential_service = "today-mail-agent"
    secret_names = (
        "OPENROUTER_API_KEY",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "GOOGLE_OAUTH_TOKEN",
    )
    for name in secret_names:
        if not os.getenv(name):
            try:
                saved_secret = keyring.get_password(credential_service, name)
            except Exception:
                saved_secret = None
            if saved_secret:
                os.environ[name] = saved_secret

    # Ignore credentials from the retired IMAP/App Password configuration.
    for name in ("EMAIL_USERNAME", "EMAIL_PASSWORD"):
        os.environ.pop(name, None)
