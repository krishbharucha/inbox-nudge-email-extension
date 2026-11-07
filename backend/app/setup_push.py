"""Generate a stable VAPID pair into the local .env without printing secrets."""

import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from dotenv import dotenv_values


def main():
    path = Path(".env")
    values = dotenv_values(path)
    private, public = values.get("VAPID_PRIVATE_KEY"), values.get("VAPID_PUBLIC_KEY")
    if private and public:
        print("Existing notification keys preserved.")
        return
    if private or public:
        raise ValueError(
            "Both notification keys must be configured together; existing values were not changed."
        )
    key = ec.generate_private_key(ec.SECP256R1())
    private_key = base64.urlsafe_b64encode(
        key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    ).decode()
    public_key = (
        base64.urlsafe_b64encode(
            key.public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )
        )
        .rstrip(b"=")
        .decode()
    )
    text = path.read_text() if path.exists() else ""
    import re

    for name, value in {"VAPID_PRIVATE_KEY": private_key, "VAPID_PUBLIC_KEY": public_key}.items():
        if name in values:
            text = re.sub(rf"^{name}=.*$", name + "=" + value, text, flags=re.M)
        else:
            text += f"\n{name}={value}"
    if "VAPID_SUBJECT" not in values:
        text += "\nVAPID_SUBJECT=mailto:admin@example.com"
    path.write_text(text.rstrip() + "\n")
    path.chmod(0o600)
    print("Generated private notification keys in .env. Keep this pair stable and private.")


if __name__ == "__main__":
    main()
