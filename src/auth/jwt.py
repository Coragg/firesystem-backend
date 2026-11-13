from datetime import UTC, datetime, timedelta

from jwt import decode, encode

from src.config import ACCESS_TOKEN_EXPIRE_MINUTES, ALGORITHM, SECRET_KEY


def create_token(data: dict) -> str:
    """create a signed jwt token with an expiration claim\n
    param data dict\n
    return token str"""
    payload = data.copy()
    payload["exp"] = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return encode(payload=payload, key=SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """decode and verify a jwt token to get its payload\n
    param token str\n
    return payload dict"""
    return decode(jwt=token, key=SECRET_KEY, algorithms=[ALGORITHM])
