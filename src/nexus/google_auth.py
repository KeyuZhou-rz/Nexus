from __future__ import annotations

from pathlib import Path

from .config import default_config
from .aggregators.google import SCOPES, _atomic_write_secret, _ensure_google_deps


def authorize_google(credentials_path: Path, token_path: Path) -> None:
    """Runs the local OAuth server to let the user log in to Google."""
    _ensure_google_deps()
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not credentials_path.exists():
        raise FileNotFoundError(
            f"Google client secret not found at {credentials_path}."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
    creds = flow.run_local_server(port=0)

    _atomic_write_secret(token_path, creds.to_json())
    print(f"Token saved to {token_path}")


def main() -> None:
    """Main entry point for the auth script."""
    config = default_config()
    if not config.google_credentials_path or not config.google_token_path:
        raise RuntimeError("Google paths not configured.")

    authorize_google(config.google_credentials_path, config.google_token_path)


if __name__ == "__main__":
    main()
