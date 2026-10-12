"""Command-line entry point for the today's mail agent."""

import os

from dotenv import load_dotenv
import keyring

# Load ordinary settings (timezone, model name, and so on) from .env. Secrets
# are deliberately removed from the environment and loaded only from the
# Windows credential store below.
load_dotenv()

_CREDENTIAL_SERVICE = "today-mail-agent"
_SECRET_NAMES = (
    "OPENROUTER_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "GOOGLE_OAUTH_TOKEN",
)

for _name in _SECRET_NAMES:
    os.environ.pop(_name, None)
    _saved_secret = keyring.get_password(_CREDENTIAL_SERVICE, _name)
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
