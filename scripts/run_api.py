"""Uvicorn entrypoint for the RTIA API + UI.

Prints a Jupyter-style URL containing the API token on startup so the
operator can open the UI in one click. Set ``RTIA_API_TOKEN`` in the
environment to pin a stable token across restarts.

Run with:
    uv run python scripts/run_api.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.auth import generate_token  # noqa: E402
from api.main import create_app  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def main() -> None:
    load_dotenv()

    # Reuse the same token between the printed banner and the app so
    # the operator's first click works. Set RTIA_API_TOKEN explicitly in
    # the env to pin a token across restarts.
    token = generate_token()
    os.environ["RTIA_API_TOKEN"] = token

    host = os.environ.get("RTIA_API_HOST", DEFAULT_HOST)
    port = int(os.environ.get("RTIA_API_PORT", DEFAULT_PORT))

    print()
    print("=" * 70)
    print("RTIA API + UI")
    print("=" * 70)
    print(f"Open http://{host}:{port}/?token={token}")
    print()
    print("API smoke (token must accompany every request):")
    print(f"  curl -H 'Authorization: Bearer {token}' http://{host}:{port}/pipeline/<id>")
    print("=" * 70)
    print()

    import uvicorn

    # Build the app eagerly so the cost of pipeline construction is
    # paid before the first request, not on the first POST.
    app = create_app()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
