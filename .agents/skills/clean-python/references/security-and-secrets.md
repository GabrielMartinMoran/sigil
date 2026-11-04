# Security and secrets

Use this reference when a Python project authenticates users, handles API keys,
issues tokens, or stores recoverable sensitive data. Keep security decisions at
the boundary that owns authentication and key management, and do not invent
cryptographic primitives.

## Classify the data first

Choose the protection based on what the application needs to do with the value:

| Data | Protection |
| --- | --- |
| User password | Adaptive one-way password hashing |
| Reset token or API key used only for comparison | Random value sent once, hash stored for verification |
| Secret the application must recover | Authenticated encryption with managed key storage |
| JWT | Digital signature with explicit validation; JWT is not automatically encrypted |

Do not encrypt passwords just so the application can recover them. If a design
requires recovering a user's password, challenge the integration and prefer a
delegated credential or OAuth flow instead.

## Password hashing

Prefer Argon2id for new applications. `argon2-cffi` provides a high-level
`PasswordHasher` API that hashes, verifies, and detects when a stored hash needs
new parameters:

```python
from argon2 import PasswordHasher


password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    return password_hasher.verify(password_hash, password)


def password_hash_needs_upgrade(password_hash: str) -> bool:
    return password_hasher.check_needs_rehash(password_hash)
```

The encoded hash contains the algorithm, salt, and parameters. Do not generate
or store a separate salt when the library manages it correctly. Tune the work
factor against the real deployment and revisit it as hardware changes. A login
that verifies an old hash can rehash it with current parameters after successful
verification.

Use `bcrypt` when compatibility with an existing bcrypt system is required. Do
not silently truncate passwords to fit bcrypt's input limit; validate the byte
length according to the chosen implementation and document the migration path.
Do not pre-hash with a fast digest unless the exact construction has been
reviewed for encoding, length, null-byte, and password-shucking risks.

Never use MD5, SHA-1, SHA-256, or another fast general-purpose digest as a
password storage algorithm. Do not compare password strings or hashes with
ordinary application logic when the password library provides verification.

## Salt and pepper

A salt is public per-password randomness stored inside the encoded password
hash. The hashing library generates it automatically.

A pepper is a shared secret applied in addition to the password hash. Use it
only when the threat model justifies the operational cost:

- Store it in a secret manager, vault, or HSM, never beside password hashes.
- Keep it out of source control, images, logs, exceptions, and `.env.example`.
- Version it so the application can identify the active key.
- Define rotation and recovery behavior before deploying it. A compromised
  pepper cannot be changed transparently without access to the plaintext
  password, so rotation may require forced resets.

Do not add a pepper as a substitute for a strong adaptive hash, unique salts, or
proper secret management.

## Reset tokens and API keys

Generate one-time values with `secrets`, not `random` or a predictable UUID
scheme. Store only a hash of a reset token or API key when the server needs to
verify it but never recover it.

Give every reset token a short expiry, a purpose, an owner, and a consumed state.
Invalidate it after successful use. Show an API key only at creation time when
possible, and store a hash for future verification.

Test expiry, reuse, wrong-user, wrong-purpose, and invalid-token paths. Never
write the raw token to logs or exception messages.

## JWT with PyJWT

Use JWT only when the application needs its interoperability or stateless token
contract. A signed JWT provides integrity, not confidentiality. Never put
passwords, API keys, or sensitive personal data in claims merely because the
payload is base64 encoded.

When decoding with `PyJWT`:

- Hard-code or securely configure the allowed algorithm list. Never derive it
  from the token header.
- Verify the signature and use the correct key for the configured algorithm.
- Require and validate claims appropriate to the contract, commonly `exp`,
  `iat`, `nbf`, `iss`, `aud`, and `sub`.
- Use a short access-token lifetime and define refresh rotation or revocation
  for long-lived sessions.
- Use asymmetric signing keys for independently deployed services when suitable;
  use a shared secret only when its distribution and rotation are controlled.
- Support key rotation with key identifiers and an overlap period where old
  tokens can be verified but no longer issued.
- Treat `decode(..., options={"verify_signature": False})` as forbidden in an
  authentication path.

Keep JWT errors at the authentication boundary. Translate them into a stable
unauthorized response without exposing whether a token was almost valid or
which key, claim, or signature detail failed.

## Recoverable encryption and key management

If the application must recover a secret, use an established authenticated
encryption implementation such as `cryptography` with a key held by a secrets
manager or KMS. Do not invent a cipher mode, reuse a nonce, derive keys from a
password without a reviewed KDF, or store the encryption key beside the
ciphertext.

Document key ownership, rotation, version identifiers, nonce handling, failure
behavior, and backup recovery. Separate application configuration from key
material. If the platform already offers envelope encryption or a secret vault,
prefer that boundary over application-managed master keys.

## Security tests and logging

Add tests for the security contract, not just the happy path:

- Correct and incorrect password verification.
- Rehash detection when password parameters change.
- Password reset expiry, one-time use, and wrong-purpose rejection.
- JWT signature, algorithm, expiry, issuer, audience, and required-claim failures.
- Key rotation and overlap behavior when the project supports it.
- Absence of passwords, tokens, API keys, and sensitive claims in logs and error
  responses.

Use a safe logging allowlist. Log event type, operation, outcome, and a
correlation ID where useful, but never credentials or raw security material.
