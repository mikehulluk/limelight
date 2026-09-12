from __future__ import annotations

from pathlib import Path

from limelight.reader import open_limelight
from limelight.signing import (
    generate_ed25519_keypair,
    sign_document,
    verify_document_signature,
    verify_manifest_signatures,
)
from limelight.writer import Index, LimelightProject


def test_sign_and_verify_round_trip() -> None:
    private_key, _ = generate_ed25519_keypair()
    content = b"hello world"

    signature = sign_document(content, signer="alice", private_key_bytes=private_key)
    ok, reason = verify_document_signature(content, signature)

    assert ok
    assert reason is None


def test_verify_detects_tampered_content() -> None:
    private_key, _ = generate_ed25519_keypair()
    signature = sign_document(b"hello world", signer="alice", private_key_bytes=private_key)

    ok, reason = verify_document_signature(b"hello worlD", signature)

    assert not ok
    assert reason == "content changed since signing"


def test_verify_detects_wrong_key() -> None:
    private_key, _ = generate_ed25519_keypair()
    other_private_key, other_public_key = generate_ed25519_keypair()
    content = b"hello world"

    signature = sign_document(content, signer="alice", private_key_bytes=private_key)

    import base64
    from dataclasses import replace

    swapped = replace(signature, public_key_b64=base64.b64encode(other_public_key).decode("ascii"))

    ok, reason = verify_document_signature(content, swapped)

    assert not ok
    assert reason == "signature does not verify"


def test_end_to_end_signed_dataset(tmp_path: Path) -> None:
    private_key, _ = generate_ed25519_keypair()

    project = LimelightProject(title="Signing Test", authors=["Test"])
    project.add_csv_dataset(
        id="prices",
        arrays={"value": [1, 2, 3]},
        index=Index.no_index(),
        signers=[("alice", private_key)],
    )

    out_dir = tmp_path / "pkg"
    project.write_folder(out_dir)

    with open_limelight(out_dir) as package:
        manifest = package.manifest_json()
        checks = verify_manifest_signatures(package, manifest)

    assert len(checks) == 1
    assert checks[0].ok
    assert checks[0].signer == "alice"
