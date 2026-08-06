"""
Tests for email_sender.py's confirmation gate - added after finding it
would send email with no human-in-the-loop check at all if ever wired
into an autonomous action path.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from email_sender import EmailSender


def test_send_without_confirm_is_blocked():
    sender = EmailSender(sender_email="a@example.com", sender_password="x", receiver_email="b@example.com")
    try:
        sender.send_email("subject", "body")
        assert False, "send_email() should have raised without confirm=True"
    except PermissionError:
        pass


def test_send_with_confirm_attempts_to_send():
    sender = EmailSender(sender_email="a@example.com", sender_password="x", receiver_email="b@example.com")
    with patch("email_sender.smtplib.SMTP") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server
        sender.send_email("subject", "body", confirm=True)
        mock_server.login.assert_called_once()
        mock_server.sendmail.assert_called_once()


def test_missing_credentials_logs_warning_but_does_not_crash():
    import os
    saved = {k: os.environ.pop(k, None) for k in
             ("PARTNERSHIP_AI_SENDER_EMAIL", "PARTNERSHIP_AI_SENDER_PASSWORD")}
    try:
        sender = EmailSender(receiver_email="b@example.com")
        assert sender.sender_email is None
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS: {t.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL: {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
