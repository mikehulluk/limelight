from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

if TYPE_CHECKING:
    from .reader import LimelightPackage

ALGORITHM = "ed25519"


class SigningError(RuntimeError):
    """Raised when a document cannot be signed or verified."""


@dataclass(frozen=True)
class DocumentSignature:
    signer: str
    public_key_b64: str
    signature_b64: str
    content_sha256: str
    algorithm: str = ALGORITHM
    signed_at: str | None = None


@dataclass(frozen=True)
class SignatureCheck:
    document_path: str
    signer: str
    ok: bool
    reason: str | None = None


def generate_ed25519_keypair() -> tuple[bytes, bytes]:
    """Return (private_key_bytes, public_key_bytes), both raw 32-byte encodings."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    private_bytes = private_key.private_bytes(
        encoding=Encoding.Raw, format=PrivateFormat.Raw, encryption_algorithm=NoEncryption()
    )
    public_bytes = public_key.public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
    return private_bytes, public_bytes


def private_key_to_pem(private_key_bytes: bytes) -> str:
    private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    return private_key.private_bytes(
        encoding=Encoding.PEM, format=PrivateFormat.PKCS8, encryption_algorithm=NoEncryption()
    ).decode("ascii")


def private_key_from_pem(pem_text: str) -> bytes:
    private_key = load_pem_private_key(pem_text.encode("ascii"), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise SigningError("PEM does not contain an Ed25519 private key")
    return private_key.private_bytes(
        encoding=Encoding.Raw, format=PrivateFormat.Raw, encryption_algorithm=NoEncryption()
    )


def sign_document(
    content: bytes,
    *,
    signer: str,
    private_key_bytes: bytes,
    signed_at: str | None = None,
) -> DocumentSignature:
    digest = hashlib.sha256(content).digest()
    private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    signature_bytes = private_key.sign(digest)
    public_bytes = private_key.public_key().public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
    return DocumentSignature(
        signer=signer,
        public_key_b64=base64.b64encode(public_bytes).decode("ascii"),
        signature_b64=base64.b64encode(signature_bytes).decode("ascii"),
        content_sha256=digest.hex(),
        signed_at=signed_at,
    )


def verify_document_signature(content: bytes, signature: DocumentSignature) -> tuple[bool, str | None]:
    if signature.algorithm != ALGORITHM:
        return False, f"unsupported signature algorithm {signature.algorithm!r}"

    digest = hashlib.sha256(content).digest()
    if digest.hex() != signature.content_sha256:
        return False, "content changed since signing"

    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(signature.public_key_b64))
        public_key.verify(base64.b64decode(signature.signature_b64), digest)
    except (InvalidSignature, ValueError):
        return False, "signature does not verify"

    return True, None


def _document_signature_from_payload(payload: dict[str, Any]) -> DocumentSignature:
    return DocumentSignature(
        signer=payload["signer"],
        algorithm=payload["algorithm"],
        public_key_b64=payload["publicKey"],
        signature_b64=payload["signature"],
        content_sha256=payload["contentSha256"],
        signed_at=payload.get("signedAt"),
    )


def verify_manifest_signatures(package: "LimelightPackage", manifest: dict[str, Any]) -> list[SignatureCheck]:
    checks: list[SignatureCheck] = []

    for source in manifest["sources"]:
        checks.extend(_verify_document_signatures(package, source["path"], source.get("signatures") or []))

    story = manifest["story"]
    checks.extend(_verify_document_signatures(package, story["documentPath"], story.get("signatures") or []))

    return checks


def _verify_document_signatures(
    package: "LimelightPackage", document_path: str, signature_payloads: list[dict[str, Any]]
) -> list[SignatureCheck]:
    if not signature_payloads:
        return []

    content = package.read_bytes(document_path)
    checks: list[SignatureCheck] = []
    for payload in signature_payloads:
        signature = _document_signature_from_payload(payload)
        ok, reason = verify_document_signature(content, signature)
        checks.append(SignatureCheck(document_path=document_path, signer=signature.signer, ok=ok, reason=reason))
    return checks
