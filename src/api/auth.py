import base64
import hashlib
import hmac
import json
import re
import secrets
import time

PBKDF2_ITERATIONS = 200000
TOKEN_TTL_SECONDS = 12 * 3600
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 300

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,}$")


class AuthError(Exception):
    pass


def normalize_email(value):
    if not isinstance(value, str):
        raise AuthError("Email is required")
    email = value.strip().lower()
    if not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address")
    return email


def validate_password(value):
    if not isinstance(value, str) or not value:
        raise AuthError("Password is required")
    if len(value) < 8 or len(value) > 128:
        raise AuthError("Password must be 8 to 128 characters")
    if not (re.search(r"[a-z]", value) and re.search(r"[A-Z]", value) and re.search(r"\d", value)):
        raise AuthError("Password needs an uppercase letter, a lowercase letter and a number")
    return value


def hash_password(password, salt=None, iterations=PBKDF2_ITERATIONS):
    salt = secrets.token_bytes(16) if salt is None else salt
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        scheme, iterations, salt_hex, digest_hex = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


def waste_time(password):
    hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"\0" * 16, PBKDF2_ITERATIONS)


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(message, secret):
    return hmac.new(secret.encode("utf-8"), message.encode("ascii"), hashlib.sha256).digest()


def sign_token(claims, secret, now=None, ttl=TOKEN_TTL_SECONDS):
    issued = int(time.time() if now is None else now)
    payload = dict(claims, iat=issued, exp=issued + ttl)
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{head}.{body}.{_b64(_sign(head + '.' + body, secret))}"


def verify_token(token, secret, now=None):
    try:
        head, body, signature = token.split(".")
        header = json.loads(_unb64(head))
        given = _unb64(signature)
        payload = json.loads(_unb64(body))
    except (ValueError, AttributeError):
        raise AuthError("Invalid token")
    if header.get("alg") != "HS256":
        raise AuthError("Invalid token")
    if not hmac.compare_digest(_sign(head + "." + body, secret), given):
        raise AuthError("Invalid token")
    current = time.time() if now is None else now
    if not isinstance(payload.get("exp"), (int, float)) or payload["exp"] <= current:
        raise AuthError("Token expired")
    return payload
