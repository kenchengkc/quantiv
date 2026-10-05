import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ml.candidate_evidence import sign_candidate_evidence, verify_candidate_evidence
from ml.model_bundle import ModelBundleError


@pytest.mark.parametrize("mutation", ["member", "extra", "identity", "signature"])
def test_candidate_provenance_is_signed_and_members_are_immutable(tmp_path, mutation):
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public_pem = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    member = tmp_path / "training.parquet"
    member.write_bytes(b"exact training rows")
    bundle_id = "a" * 64
    sign_candidate_evidence(tmp_path, bundle_id, private_key=private_pem)
    assert verify_candidate_evidence(tmp_path, bundle_id, public_key=public_pem)["bundle_id"] == bundle_id
    if mutation == "member":
        member.write_bytes(b"changed")
    elif mutation == "extra":
        (tmp_path / "extra.parquet").write_bytes(b"unbound")
    elif mutation == "identity":
        bundle_id = "b" * 64
    else:
        public_pem = Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    with pytest.raises(ModelBundleError, match="digest|member|identity|signature"):
        verify_candidate_evidence(tmp_path, bundle_id, public_key=public_pem)
