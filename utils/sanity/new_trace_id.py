"""Print a random 128-bit trace ID locally; no cluster access."""

import secrets

print(secrets.token_hex(16))
