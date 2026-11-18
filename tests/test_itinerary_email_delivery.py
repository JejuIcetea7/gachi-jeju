import copy
from concurrent.futures import ThreadPoolExecutor
import json
import unittest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from backend.app.main import app
from backend.app.routers import itinerary_email as router
from backend.app.services import itinerary_email_delivery as delivery
from backend.app.services.itinerary_email import ItineraryEmailError
from backend.app.services.itinerary_pdf import PdfUnavailableError
from test_itinerary_email import SETTINGS, RECIPIENT, PDF
from test_itinerary_pdf import snapshot


class EmailDeliveryRegistryTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.registry = delivery.EmailDeliveryRegistry(
            clock=lambda: self.now,
        )

    def test_pending_duplicate_is_blocked_until_completion(self):
        self.assertFalse(self.registry.reserve("same", "recipient"))
        self.now = 1000
        with self.assertRaises(ItineraryEmailError) as raised:
            self.registry.reserve("same", "recipient")
        self.assertEqual(raised.exception.code, "email_in_progress")
        self.registry.finish("same", "accepted")
        self.assertTrue(self.registry.reserve("same", "recipient"))

    def test_accepted_result_expires_after_retention(self):
        self.registry.reserve("same", "recipient")
        self.registry.finish("same", "accepted")
        self.now = delivery.RETENTION_SECONDS - 1
        self.assertTrue(self.registry.reserve("same", "recipient"))
        self.now = delivery.RETENTION_SECONDS
        self.assertFalse(self.registry.reserve("same", "recipient"))

    def test_unknown_submission_is_not_repeated(self):
        self.registry.reserve("same", "recipient")
        self.registry.finish("same", "unknown")
        with self.assertRaises(ItineraryEmailError) as raised:
            self.registry.reserve("same", "recipient")
        self.assertTrue(raised.exception.uncertain)
        self.assertEqual(raised.exception.code, "email_delivery_unknown")
        self.assertFalse(
            self.registry.reserve("other", "other-recipient"),
        )

    def test_same_recipient_cooldown_and_independent_recipient(self):
        self.registry.reserve("first", "recipient")
        self.registry.finish("first", "accepted")
        with self.assertRaises(ItineraryEmailError) as raised:
            self.registry.reserve("changed", "recipient")
        self.assertEqual(raised.exception.status_code, 429)
        self.assertFalse(
            self.registry.reserve("other", "other-recipient"),
        )
        self.now = delivery.RECIPIENT_COOLDOWN_SECONDS
        self.assertFalse(self.registry.reserve("changed", "recipient"))

    def test_capacity_is_bounded_and_known_failure_releases_slot(self):
        registry = delivery.EmailDeliveryRegistry(
            max_records=2, max_active=1,
        )
        registry.reserve("first", "one")
        with self.assertRaises(ItineraryEmailError):
            registry.reserve("second", "two")
        registry.finish("first", None)
        self.assertFalse(registry.reserve("second", "two"))

    def test_completed_record_capacity_recovers_after_expiration(self):
        registry = delivery.EmailDeliveryRegistry(
            clock=lambda: self.now,
            max_records=2,
            max_active=1,
        )
        for key in ("one", "two"):
            registry.reserve(key, key)
            registry.finish(key, "accepted")
        with self.assertRaises(ItineraryEmailError):
            registry.reserve("third", "three")
        self.now = delivery.RETENTION_SECONDS
        self.assertFalse(registry.reserve("third", "three"))

    def test_parallel_duplicate_has_only_one_reservation(self):
        def reserve():
            try:
                return self.registry.reserve("same", "recipient")
            except ItineraryEmailError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda value: reserve(), range(2),
            ))
        self.assertEqual(results.count(False), 1)
        self.assertEqual(results.count("email_in_progress"), 1)


class ItineraryEmailDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(
            delivery, "_deliveries", delivery.EmailDeliveryRegistry(),
        ))
        self.settings = self.enterContext(patch.object(
            delivery, "load_smtp_settings", return_value=SETTINGS,
        ))
        self.pdf = self.enterContext(patch.object(
            delivery, "build_itinerary_pdf", return_value=PDF,
        ))
        self.submit = self.enterContext(patch.object(
            delivery, "submit_pdf_message",
        ))

    def send(self, value=None):
        return delivery.deliver_itinerary_email(
            snapshot() if value is None else value,
            RECIPIENT,
        )

    def test_whole_snapshot_and_exact_attachment_are_preserved(self):
        value = snapshot()
        before = copy.deepcopy(value)
        self.assertFalse(self.send(value))
        self.pdf.assert_called_once_with(before)
        message, recipient, settings = self.submit.call_args.args
        self.assertEqual(recipient, RECIPIENT)
        self.assertEqual(settings, SETTINGS)
        self.assertEqual(
            list(message.iter_attachments())[0].get_payload(decode=True),
            PDF,
        )
        self.assertEqual(value, before)

    def test_disabled_settings_skip_pdf_and_smtp(self):
        self.settings.side_effect = ItineraryEmailError(
            "email_not_configured", "발송 설정이 없습니다.",
        )
        with self.assertRaises(ItineraryEmailError):
            self.send()
        self.pdf.assert_not_called()
        self.submit.assert_not_called()

    def test_accepted_duplicate_skips_pdf_and_smtp(self):
        self.assertFalse(self.send())
        self.assertTrue(self.send())
        self.pdf.assert_called_once()
        self.submit.assert_called_once()

    def test_unknown_result_blocks_another_submission(self):
        self.submit.side_effect = ItineraryEmailError(
            "email_delivery_unknown",
            "접수 여부 미확인",
            502,
            uncertain=True,
        )
        for expected_status in (502, 409):
            with self.assertRaises(ItineraryEmailError) as raised:
                self.send()
            self.assertEqual(
                raised.exception.status_code, expected_status,
            )
        self.submit.assert_called_once()

    def test_definite_failure_allows_an_explicit_new_attempt(self):
        self.submit.side_effect = ItineraryEmailError(
            "email_rejected", "명시적으로 거부됨", 502,
        )
        with self.assertRaises(ItineraryEmailError):
            self.send()
        self.submit.side_effect = None
        self.assertFalse(self.send())
        self.assertEqual(self.submit.call_count, 2)

    def test_pdf_failure_releases_reservation_without_submission(self):
        self.pdf.side_effect = PdfUnavailableError(
            "PDF 글꼴이 없습니다.",
        )
        with self.assertRaises(ItineraryEmailError) as raised:
            self.send()
        self.assertEqual(raised.exception.code, "pdf_unavailable")
        self.submit.assert_not_called()
        self.pdf.side_effect = None
        self.assertFalse(self.send())

    def test_unexpected_error_after_submission_is_uncertain(self):
        self.submit.side_effect = RuntimeError(
            "private provider details",
        )
        with self.assertRaises(ItineraryEmailError) as raised:
            self.send()
        self.assertTrue(raised.exception.uncertain)
        self.assertNotIn("private", str(raised.exception))
        self.submit.side_effect = None
        with self.assertRaises(ItineraryEmailError) as repeated:
            self.send()
        self.assertEqual(
            repeated.exception.code, "email_delivery_unknown",
        )
        self.submit.assert_called_once()

    def test_preparation_error_does_not_poison_an_explicit_retry(self):
        self.pdf.side_effect = RuntimeError("private details")
        with self.assertRaises(ItineraryEmailError) as raised:
            self.send()
        self.assertEqual(
            raised.exception.code, "email_prepare_failed",
        )
        self.assertFalse(raised.exception.uncertain)
        self.pdf.side_effect = None
        self.assertFalse(self.send())


class ItineraryEmailRequestTests(unittest.TestCase):
    def test_request_normalizes_address_and_keeps_all_days(self):
        request = router.ItineraryEmailRequest.model_validate({
            **snapshot(),
            "recipient": " Visitor@EXAMPLE.COM ",
        })
        self.assertEqual(request.recipient, "Visitor@example.com")
        self.assertEqual(len(request.plan.days), 3)

    def test_invalid_recipient_plan_and_extra_fields_are_rejected(self):
        values = [
            {**snapshot(), "recipient": "bad"},
            {
                **snapshot(),
                "recipient": RECIPIENT,
                "sender": "other@example.com",
            },
            {**snapshot(), "recipient": RECIPIENT, "map_routes": []},
            {**snapshot(), "recipient": RECIPIENT, "plan": None},
        ]
        for value in values:
            with self.subTest(keys=list(value)):
                with self.assertRaises(ValidationError):
                    router.ItineraryEmailRequest.model_validate(value)

    def test_success_and_duplicate_responses_describe_server_acceptance(self):
        request = router.ItineraryEmailRequest.model_validate({
            **snapshot(), "recipient": RECIPIENT,
        })
        for duplicate in (False, True):
            with patch.object(
                router,
                "deliver_itinerary_email",
                return_value=duplicate,
            ) as send:
                response = router.send_itinerary_email(request)
            body = json.loads(response.body)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(body["status"], "accepted")
            self.assertEqual(body["duplicate"], duplicate)
            self.assertIn(
                "no-store", response.headers["cache-control"],
            )
            self.assertNotIn("recipient", send.call_args.args[0])
            self.assertEqual(
                len(send.call_args.args[0]["plan"]["days"]), 3,
            )

    def test_failure_keeps_error_format_and_no_store(self):
        request = router.ItineraryEmailRequest.model_validate({
            **snapshot(), "recipient": RECIPIENT,
        })
        with patch.object(
            router,
            "deliver_itinerary_email",
            side_effect=ItineraryEmailError(
                "email_not_configured", "설정 없음",
            ),
        ):
            response = router.send_itinerary_email(request)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            json.loads(response.body)["error"]["code"],
            "email_not_configured",
        )
        self.assertIn(
            "no-store", response.headers["cache-control"],
        )


class ItineraryEmailHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_acceptance_contract(self):
        with patch.object(
            router, "deliver_itinerary_email", return_value=False,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/itineraries/email",
                    json={**snapshot(), "recipient": RECIPIENT},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "accepted")

    async def test_invalid_http_request_skips_delivery(self):
        with patch.object(
            router, "deliver_itinerary_email",
        ) as send:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/itineraries/email",
                    json={**snapshot(), "recipient": "bad"},
                )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"]["code"], "invalid_request",
        )
        send.assert_not_called()

    async def test_unconfigured_sender_never_builds_or_submits(self):
        with (
            patch.object(
                delivery,
                "load_smtp_settings",
                side_effect=ItineraryEmailError(
                    "email_not_configured", "발송 설정이 없습니다.",
                ),
            ),
            patch.object(
                delivery, "build_itinerary_pdf",
            ) as pdf,
            patch.object(
                delivery, "submit_pdf_message",
            ) as submit,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/itineraries/email",
                    json={**snapshot(), "recipient": RECIPIENT},
                )
        self.assertEqual(response.status_code, 503)
        pdf.assert_not_called()
        submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()