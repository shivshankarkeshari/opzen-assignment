"""Fill missing local-only Langfuse secrets without printing their values."""

import os
import re
import secrets
from pathlib import Path
from uuid import uuid4


def configure():
    path = Path(".env")
    content = path.read_text() if path.exists() else Path(".env.example").read_text()
    defaults = {
        "LANGFUSE_PUBLIC_KEY": f"pk-lf-{uuid4()}",
        "LANGFUSE_SECRET_KEY": f"sk-lf-{uuid4()}",
        "LANGFUSE_NEXTAUTH_SECRET": secrets.token_urlsafe(32),
        "LANGFUSE_SALT": secrets.token_hex(32),
        "LANGFUSE_ENCRYPTION_KEY": secrets.token_hex(32),
        "LANGFUSE_ADMIN_EMAIL": "admin@opzen.local",
        "LANGFUSE_ADMIN_PASSWORD": secrets.token_urlsafe(24),
    }
    for name, value in defaults.items():
        match = re.search(rf"^{name}=(.*)$", content, re.MULTILINE)
        if match and match.group(1).strip():
            continue
        if match:
            content = re.sub(
                rf"^{name}=.*$", lambda _: f"{name}={value}", content, flags=re.MULTILINE
            )
        else:
            content += f"\n{name}={value}\n"
    path.write_text(content)
    os.chmod(path, 0o600)
    print("Local Langfuse credentials configured in .env; existing values preserved.")


if __name__ == "__main__":
    configure()
