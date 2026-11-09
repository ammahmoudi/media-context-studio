"""Create local deployment credentials without printing or overwriting them."""
import argparse
import secrets
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=18765)
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
target = root / ".env"
if target.exists():
    raise SystemExit(".env already exists; keeping its credentials unchanged.")
text = (root / ".env.example").read_text()
for placeholder in ("replace-with-a-long-random-password", "replace-access-key", "replace-with-a-long-random-secret", "replace-with-a-long-random-api-key"):
    text = text.replace(placeholder, secrets.token_hex(24))
text = text.replace("VIDEO_CONTEXT_HTTP_PORT=18765", f"VIDEO_CONTEXT_HTTP_PORT={args.port}")
target.write_text(text, encoding="utf-8")
print("Created .env with unique credentials. Keep it private and back it up securely.")
