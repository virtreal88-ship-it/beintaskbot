"""Run interactively to prepare isolated review settings; never prints password."""

import getpass
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone


def main():
    password = getpass.getpass("Choose review-only password (at least 16 characters): ")
    confirmation = getpass.getpass("Repeat review-only password: ")
    if password != confirmation or len(password) < 16 or len(password) > 256:
        raise SystemExit("Passwords must match and contain 16–256 characters.")
    salt = secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600000).hex()
    print("Set these ONLY on the CRM web service; do not commit them:")
    print("MODERATION_DEMO_ENABLED=true")
    print("MODERATION_DEMO_USERNAME=marketplace-review")
    print("MODERATION_DEMO_TENANT_ID=" + str(uuid.uuid4()))
    print("MODERATION_DEMO_PASSWORD_HASH=pbkdf2_sha256$600000$" + salt.hex() + "$" + digest)
    print("MODERATION_DEMO_EXPIRES_AT=" + (datetime.now(timezone.utc) + timedelta(days=7)).isoformat())
    print("The login page is /moderation/login. First valid login creates a NEW isolated company.")
    print("Connect a test CRM account there, never the customer production account.")
    print("Revoke all demo sessions by setting MODERATION_DEMO_ENABLED=false.")


if __name__ == "__main__":
    main()
