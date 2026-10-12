"""Command-line entry point for the today's mail agent."""

import os

from dotenv import load_dotenv
import keyring

# Load ordinary settings (timezone, model name, and so on) from .env. A hosted
# service can provide secrets as environment variables; locally, fall back to
# Windows Credential Manager when one is not already set.
load_dotenv()

_CREDENTIAL_SERVICE = "today-mail-agent"
_SECRET_NAMES = (
    "OPENROUTER_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "GOOGLE_OAUTH_TOKEN",
)

for _name in _SECRET_NAMES:
    if not os.getenv(_name):
        try:
            _saved_secret = keyring.get_password(_CREDENTIAL_SERVICE, _name)
        except Exception:
            _saved_secret = None
        if _saved_secret:
            os.environ[_name] = _saved_secret

# Ignore credentials from the retired IMAP/App Password configuration.
for _name in ("EMAIL_USERNAME", "EMAIL_PASSWORD"):
    os.environ.pop(_name, None)

from .runtime import run_agent


def main() -> None:
    request = input("What would you like to know about today's emails?\n> ").strip()
    if not request:
        print("Please enter a request.")
        return

    try:
        print("\n" + run_agent(request))
    except Exception as exc:
        print(f"\nI couldn't complete that request: {exc}")


if __name__ == "__main__":
    main()
