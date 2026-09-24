"""Store the OpenAI API key in Windows Credential Manager.

Preferable to a .env file: the value is encrypted per-user by Windows and cannot
be committed to git by accident.

    python scripts/set_key.py
"""

from __future__ import annotations

import getpass

import _bootstrap  # noqa: F401  (sys.path side effect)

import keyring

from orbit.config import KEYRING_SERVICE, KEYRING_USERNAME


def main() -> int:
    existing = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    if existing:
        print(f"A key is already stored (ending {existing[-4:]}).")
        if input("Replace it? [y/N] ").strip().lower() != "y":
            print("Unchanged.")
            return 0

    # getpass keeps the key out of the terminal scrollback and shell history.
    key = getpass.getpass("OpenAI API key (input hidden): ").strip()
    if not key:
        print("Nothing entered. Aborted.")
        return 1
    if not key.startswith("sk-"):
        print("Warning: that does not look like an OpenAI key (expected 'sk-' prefix).")
        if input("Store anyway? [y/N] ").strip().lower() != "y":
            return 1

    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, key)
    print(f"Stored in Windows Credential Manager under {KEYRING_SERVICE!r}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
