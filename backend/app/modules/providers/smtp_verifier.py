"""
SMTP mailbox verifier (live mode) - checks an address exists WITHOUT sending mail.

Protocol (LLD 4.5):
  1. MX lookup for the domain (no MX -> invalid).
  2. Connect to the best MX on port 25, EHLO, MAIL FROM.
  3. Catch-all probe: RCPT TO a random address. 250 -> domain accepts everything.
  4. RCPT TO the real address: 250/251 -> valid, 550/551/553 -> invalid,
     4xx (greylisting) / timeout -> unknown.
  5. RSET + QUIT. We NEVER send DATA, so no email is ever delivered.

Production note: run this from a dedicated server with outbound port 25 open,
a proper reverse-DNS name and good IP reputation (HLD 4). Many cloud providers
block port 25 - in that case keep SN_VERIFIER_MODE=fake or use a paid verifier.
"""
from __future__ import annotations

import secrets
import smtplib

import dns.resolver

from app.modules.providers.base import EMAIL_CATCH_ALL, EMAIL_INVALID, EMAIL_UNKNOWN, EMAIL_VALID

# The commands we are allowed to send. A unit test asserts DATA is never among them.
ALLOWED_COMMANDS = ("EHLO", "MAIL FROM", "RCPT TO", "RSET", "QUIT")


class SmtpVerifier:
    def __init__(self, helo_host: str = "verify.salesnavigator.example",
                 mail_from: str = "probe@verify.salesnavigator.example", timeout: float = 10.0):
        self.helo_host = helo_host
        self.mail_from = mail_from
        self.timeout = timeout

    def _best_mx(self, domain: str) -> str | None:
        try:
            answers = dns.resolver.resolve(domain, "MX", lifetime=5.0)
        except Exception:  # NXDOMAIN, no answer, timeout - all mean "cannot verify"
            return None
        best = min(answers, key=lambda r: r.preference)
        return str(best.exchange).rstrip(".")

    def verify(self, email: str) -> str:
        domain = email.rsplit("@", 1)[-1]
        mx = self._best_mx(domain)
        if mx is None:
            return EMAIL_INVALID
        try:
            with smtplib.SMTP(mx, 25, timeout=self.timeout) as smtp:
                smtp.ehlo(self.helo_host)
                smtp.mail(self.mail_from)
                probe = f"{secrets.token_hex(10)}@{domain}"
                catch_all = smtp.rcpt(probe)[0] in (250, 251)
                code = smtp.rcpt(email)[0]
                smtp.rset()
        except (OSError, smtplib.SMTPException):
            return EMAIL_UNKNOWN
        if code in (250, 251):
            return EMAIL_CATCH_ALL if catch_all else EMAIL_VALID
        if code in (550, 551, 553):
            return EMAIL_INVALID
        return EMAIL_UNKNOWN
