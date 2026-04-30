"""Commit-reveal primitives for role and substrate assignment.

Per PROTOCOL_MAPPING.md §Role and substrate assignment: at game-init the
host publishes commitments `sha256(value || salt)`. At game-end the host
reveals (value, salt) pairs; anyone can verify each commitment matches.
This makes mid-game role-switch impossible — the commitment is in the
append-only log forever.

Salts are 16 random bytes (per the doc's reference snippet using
`secrets.token_bytes(16)`). Values are encoded UTF-8 before hashing.
"""

from __future__ import annotations

import hashlib
import secrets


_SALT_BYTES = 16


def generate_salts(n: int) -> list[bytes]:
    """Return `n` cryptographically-random 16-byte salts."""
    if n < 0:
        raise ValueError("n must be non-negative")
    return [secrets.token_bytes(_SALT_BYTES) for _ in range(n)]


def commit(value: str, salt: bytes) -> str:
    """Return `sha256(value_utf8 || salt).hexdigest()`.

    `value` is the role string ("mafia") or substrate string ("human") or
    any opaque committable string. Hex output matches what host posts in
    `game-init.role_commitments` / `substrate_commitments`.
    """
    if not isinstance(value, str):
        raise TypeError(f"value must be str, got {type(value).__name__}")
    if not isinstance(salt, (bytes, bytearray)):
        raise TypeError(f"salt must be bytes, got {type(salt).__name__}")
    h = hashlib.sha256()
    h.update(value.encode("utf-8"))
    h.update(bytes(salt))
    return h.hexdigest()


def verify(value: str, salt_hex: str, commitment: str) -> bool:
    """Verify `commitment == sha256(value_utf8 || bytes.fromhex(salt_hex))`.

    Accepts the salt as a hex string because that's how reveals are
    serialized over the wire (see `RevealPayload.salts`). Returns False on
    any decoding error rather than raising — verification is a check, not
    an assertion.
    """
    if not isinstance(value, str) or not isinstance(salt_hex, str) or not isinstance(commitment, str):
        return False
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    return commit(value, salt) == commitment
