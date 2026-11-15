"""[AI] Create and verify signed JWT access tokens (PyJWT), using SECRET_KEY and ALGORITHM from config.

[AI] Note: no route uses these helpers yet, and `src/auth/dependencies.py` is still empty.
"""

from datetime import UTC, datetime, timedelta

from jwt import decode, encode

from src.config import ACCESS_TOKEN_EXPIRE_MINUTES, ALGORITHM, SECRET_KEY


def create_token(data: dict) -> str:
    """create a signed jwt token with an expiration claim\n
    param data dict\n
    return token str"""
    payload = data.copy()  # [AI] copy so the caller's dict is not modified
    # [AI] `exp` is a standard JWT claim; `decode_access_token` rejects the token once it has passed.
    payload["exp"] = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return encode(payload=payload, key=SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """decode and verify a jwt token to get its payload\n
    param token str\n
    return payload dict"""
    # [AI] Pinning `algorithms` to the configured one stops a token from choosing its own algorithm.
    return decode(jwt=token, key=SECRET_KEY, algorithms=[ALGORITHM])
