"""Print a token for a subject, for Postman and manual testing.

    python scripts/make_token.py user_001
    python scripts/make_token.py user_001 player-surface

In production the API gateway does this after verifying the listener's
Spotify session. This script exists so you can try the API by hand.
"""

import sys
from pathlib import Path

# Run from anywhere: put the project root on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.auth import TOKEN_LIFETIME, mint_token  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)

    subject_id = sys.argv[1]
    service_id = sys.argv[2] if len(sys.argv) > 2 else "chat-surface"

    token = mint_token(subject_id, service_id)

    print(f"subject : {subject_id}")
    print(f"service : {service_id}")
    print(f"expires : in {int(TOKEN_LIFETIME.total_seconds() // 60)} minutes")
    print()
    print("Paste this header into Postman:")
    print()
    print(f"Authorization: Bearer {token}")


if __name__ == "__main__":
    main()
