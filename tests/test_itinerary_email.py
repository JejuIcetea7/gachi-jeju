from dataclasses import replace
from email.parser import BytesParser
from email.policy import default
import smtplib
import ssl
import unittest
from unittest.mock import Mock, patch

from backend.app.services import itinerary_email as mail


SETTINGS = mail.SmtpSettings(
    host="smtp.example.com", port=587, security="starttls",
    username="test-user", password="test-password", sender="sender@example.com",
)
RECIPIENT = "visitor@example.com"
PDF = b"%PDF-1.7\nfixture"


class ItineraryEmailAddressTests(unittest.TestCase):
    def test_address_normalization_keeps_plus_and_local_part_case(self):
        self.assertEqual(
            mail.normalize_email_address(" User+trip@EXAMPLE.COM "),
            "User+trip@example.com",
        )

    def test_international_domain_is_converted_to_ascii(self):
        self.assertEqual(
            mail.normalize_email_address("visitor@예시.한국"),
            "visitor@" + "예시.한국".encode("idna").decode("ascii"),
        )

    def test_invalid_multiple_and_injected_addresses_are_rejected(self):
        for value in (
            None, "", "not-an-email", "visitor@localhost",
            "a@example.com,b@example.com", "이름 <a@example.com>",
            "a@example.com\r\nBcc: other@example.com", "한글@example.com",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    mail.normalize_email_address(value)


class ItineraryEmailSettingsTests(unittest.TestCase):
    def values(self, **changes):
        return {
            "ITINERARY_EMAIL_ENABLED": "true",
            "ITINERARY_SMTP_HOST": SETTINGS.host,
            "ITINERARY_SMTP_PORT": "587",
            "ITINERARY_SMTP_SECURITY": "starttls",
            "ITINERARY_SMTP_USERNAME": SETTINGS.username,
            "ITINERARY_SMTP_PASSWORD": SETTINGS.password,
            "ITINERARY_EMAIL_FROM": SETTINGS.sender,
            **changes,
        }

    def load(self, values):
        with patch.object(
            mail, "read_env_value",
            side_effect=lambda name: values.get(name, ""),
        ):
            return mail.load_smtp_settings()

    def test_disabled_email_does_not_read_credentials(self):
        with patch.object(mail, "read_env_value", return_value="") as reader:
            with self.assertRaises(mail.ItineraryEmailError) as raised:
                mail.load_smtp_settings()
        self.assertEqual(raised.exception.code, "email_not_configured")
        reader.assert_called_once_with("ITINERARY_EMAIL_ENABLED")

    def test_complete_settings_and_ssl_default_port(self):
        self.assertEqual(self.load(self.values()), SETTINGS)
        value = self.load(self.values(
            ITINERARY_SMTP_SECURITY="ssl", ITINERARY_SMTP_PORT="",
        ))
        self.assertEqual(value.port, 465)
        self.assertEqual(value.security, "ssl")

    def test_invalid_configuration_is_rejected_without_exposing_values(self):
        for changes in (
            {"ITINERARY_SMTP_HOST": ""},
            {"ITINERARY_SMTP_HOST": "https://smtp.example.com"},
            {"ITINERARY_SMTP_PORT": "0"},
            {"ITINERARY_SMTP_PORT": "65536"},
            {"ITINERARY_SMTP_PORT": "bad"},
            {"ITINERARY_SMTP_SECURITY": "plain"},
            {"ITINERARY_SMTP_PASSWORD": ""},
            {"ITINERARY_EMAIL_FROM": "bad"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(mail.ItineraryEmailError) as raised:
                    self.load(self.values(**changes))
                self.assertEqual(raised.exception.code, "email_not_configured")
                self.assertNotIn(SETTINGS.password, str(raised.exception))

    def test_credentials_are_not_in_settings_repr(self):
        self.assertNotIn(SETTINGS.username, repr(SETTINGS))
        self.assertNotIn(SETTINGS.password, repr(SETTINGS))


class ItineraryEmailMessageTests(unittest.TestCase):
    def test_korean_filename_and_exact_pdf_attachment(self):
        message = mail.create_pdf_message(
            PDF, RECIPIENT, SETTINGS, start_date="2026-11-20",
        )
        parsed = BytesParser(policy=default).parsebytes(message.as_bytes())
        attachments = list(parsed.iter_attachments())
        self.assertEqual(parsed["To"].addresses[0].addr_spec, RECIPIENT)
        self.assertEqual(parsed["From"].addresses[0].addr_spec, SETTINGS.sender)
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_content_type(), "application/pdf")
        self.assertEqual(attachments[0].get_payload(decode=True), PDF)
        self.assertEqual(
            attachments[0].get_filename(),
            "같이가는제주_2026-11-20_여행일정.pdf",
        )
        self.assertIsNone(parsed["Bcc"])
        self.assertIsNone(parsed["Cc"])

    def test_invalid_or_oversized_pdf_is_rejected(self):
        for content in (b"not-pdf", "%PDF-1.7"):
            with self.subTest(content=content):
                with self.assertRaises(mail.ItineraryEmailError):
                    mail.create_pdf_message(content, RECIPIENT, SETTINGS)
        with patch.object(mail, "MAX_ATTACHMENT_BYTES", 10):
            with self.assertRaises(mail.ItineraryEmailError) as raised:
                mail.create_pdf_message(
                    b"%PDF-" + b"x" * 20, RECIPIENT, SETTINGS,
                )
        self.assertEqual(
            raised.exception.code, "email_attachment_too_large",
        )

    def test_header_injection_is_rejected_before_message_creation(self):
        with self.assertRaises(ValueError):
            mail.create_pdf_message(
                PDF, RECIPIENT,
                replace(
                    SETTINGS,
                    sender="a@example.com\nBcc: b@example.com",
                ),
            )


class ItineraryEmailTransportTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.send_message.return_value = {}
        self.smtp = self.enterContext(patch.object(
            mail.smtplib, "SMTP", return_value=self.client,
        ))
        self.smtp_ssl = self.enterContext(patch.object(
            mail.smtplib, "SMTP_SSL", return_value=self.client,
        ))
        self.message = mail.create_pdf_message(PDF, RECIPIENT, SETTINGS)

    def submit(self, settings=SETTINGS):
        return mail.submit_pdf_message(self.message, RECIPIENT, settings)

    def test_starttls_precedes_login_and_sends_only_one_recipient(self):
        self.submit()
        calls = [call[0] for call in self.client.mock_calls]
        self.assertLess(calls.index("starttls"), calls.index("login"))
        context = self.client.starttls.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(self.smtp.call_args.kwargs["timeout"], 10)
        self.client.send_message.assert_called_once_with(
            self.message, from_addr=SETTINGS.sender, to_addrs=[RECIPIENT],
        )
        self.client.quit.assert_called_once()
        self.smtp_ssl.assert_not_called()

    def test_ssl_uses_verified_context_without_plain_connection(self):
        self.submit(replace(SETTINGS, security="ssl", port=465))
        context = self.smtp_ssl.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.smtp.assert_not_called()
        self.client.starttls.assert_not_called()
        self.client.send_message.assert_called_once()

    def test_missing_starttls_never_sends_credentials(self):
        self.client.starttls.side_effect = smtplib.SMTPNotSupportedError(
            "no TLS",
        )
        with self.assertRaises(mail.ItineraryEmailError) as raised:
            self.submit()
        self.assertEqual(raised.exception.code, "email_not_configured")
        self.client.login.assert_not_called()
        self.client.send_message.assert_not_called()

    def test_authentication_failure_is_not_reported_as_acceptance(self):
        self.client.login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"private provider details",
        )
        with self.assertRaises(mail.ItineraryEmailError) as raised:
            self.submit()
        self.assertEqual(raised.exception.code, "email_auth_failed")
        self.assertNotIn("private", str(raised.exception))
        self.client.send_message.assert_not_called()

    def test_connection_failure_is_known_not_submitted(self):
        self.smtp.side_effect = OSError("private connection details")
        with self.assertRaises(mail.ItineraryEmailError) as raised:
            self.submit()
        self.assertEqual(raised.exception.code, "email_send_failed")
        self.assertFalse(raised.exception.uncertain)
        self.client.send_message.assert_not_called()

    def test_recipient_and_data_refusal_are_definite_failures(self):
        errors = (
            smtplib.SMTPRecipientsRefused({
                RECIPIENT: (550, b"rejected"),
            }),
            smtplib.SMTPDataError(554, b"rejected"),
            smtplib.SMTPSenderRefused(
                550, b"rejected", SETTINGS.sender,
            ),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.client.send_message.side_effect = error
                with self.assertRaises(mail.ItineraryEmailError) as raised:
                    self.submit()
                self.assertEqual(raised.exception.code, "email_rejected")
                self.assertFalse(raised.exception.uncertain)

    def test_timeout_during_submission_is_uncertain_and_never_retried(self):
        self.client.send_message.side_effect = TimeoutError(
            "private timeout details",
        )
        with self.assertRaises(mail.ItineraryEmailError) as raised:
            self.submit()
        self.assertEqual(raised.exception.code, "email_delivery_unknown")
        self.assertTrue(raised.exception.uncertain)
        self.assertNotIn("private", str(raised.exception))
        self.client.send_message.assert_called_once()

    def test_unexpected_submission_result_cannot_claim_acceptance(self):
        self.client.send_message.return_value = None
        with self.assertRaises(mail.ItineraryEmailError) as raised:
            self.submit()
        self.assertEqual(raised.exception.code, "email_delivery_unknown")
        self.assertTrue(raised.exception.uncertain)

    def test_quit_failure_does_not_change_an_accepted_result(self):
        self.client.quit.side_effect = smtplib.SMTPServerDisconnected(
            "quit failed",
        )
        self.submit()
        self.client.send_message.assert_called_once()
        self.client.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()