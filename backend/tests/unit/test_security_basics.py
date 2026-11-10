"""SSRF guard, PII log redaction, crypto helpers, SMTP verifier never sends mail."""
import json
import smtplib
from unittest import mock

import pytest

from app.core.logging import redact_pii, redact_text
from app.core.security import decrypt, encrypt, hash_password, lookup_hash, verify_password
from app.modules.providers.website import is_safe_url


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",      # cloud metadata
    "http://127.0.0.1:8000/admin", "http://localhost/", "http://10.0.0.5/", "http://192.168.1.1/",
    "http://[::1]/", "file:///etc/passwd", "ftp://example.com/", "http://metadata.google.internal/",
    "http://printer.local/",
])
def test_ssrf_blocked(url):
    assert not is_safe_url(url, resolve=False)


@pytest.mark.parametrize("url", ["https://example.com/", "http://kosh.example/about", "https://8.8.8.8/"])
def test_public_urls_allowed(url):
    assert is_safe_url(url, resolve=False)


def test_redaction_masks_email_and_phone():
    s = redact_text("Contact ananya@koshskincare.example or +91 98765 43210 today")
    assert "ananya@" not in s and "a***@koshskincare.example" in s
    assert "98765" not in s and s.count("*") >= 8 and "3210" in s


def test_redaction_processor_covers_nested_fields():
    event = redact_pii(None, "info", {"event": "x", "user": {"email": "rohan.joshi@brand.in"},
                                      "phones": ["+919876543210"]})
    dumped = json.dumps(event)
    assert "rohan.joshi@brand.in" not in dumped
    assert "+919876543210" not in dumped


def test_password_hashing():
    h = hash_password("s3cret-pass")
    assert verify_password("s3cret-pass", h) and not verify_password("wrong", h)
    assert "s3cret" not in h


def test_encryption_round_trip_and_nonce():
    a, b = encrypt("ananya@kosh.example"), encrypt("ananya@kosh.example")
    assert a != b                                      # random nonce each time
    assert decrypt(a) == decrypt(b) == "ananya@kosh.example"


def test_lookup_hash_normalises_and_namespaces():
    assert lookup_hash("email", " A@B.com ") == lookup_hash("email", "a@b.com")
    assert lookup_hash("email", "a@b.com") != lookup_hash("phone", "a@b.com")


def test_smtp_verifier_never_sends_data():
    """The verifier may only EHLO / MAIL / RCPT / RSET / QUIT. DATA would deliver an email."""
    from app.modules.providers.smtp_verifier import SmtpVerifier

    fake = mock.MagicMock()
    fake.__enter__.return_value = fake
    fake.rcpt.return_value = (250, b"ok")
    with mock.patch.object(SmtpVerifier, "_best_mx", return_value="mx.example"), \
         mock.patch.object(smtplib, "SMTP", return_value=fake):
        status = SmtpVerifier().verify("someone@brand.example")
    called = {c[0] for c in fake.method_calls}
    assert not called & {"data", "sendmail", "send_message"}
    assert status in ("valid", "catch_all")
