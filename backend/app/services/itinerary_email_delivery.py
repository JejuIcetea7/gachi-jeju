"""PDF 생성과 메일 접수를 연결하고 중복 제출을 제한합니다."""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from threading import Lock
from time import monotonic

from .itinerary_email import (
    ItineraryEmailError,
    create_pdf_message,
    load_smtp_settings,
    normalize_email_address,
    submit_pdf_message,
)
from .itinerary_pdf import PdfUnavailableError, build_itinerary_pdf

RETENTION_SECONDS = 600
RECIPIENT_COOLDOWN_SECONDS = 60


@dataclass
class DeliveryRecord:
    recipient_key: str
    state: str = "pending"
    finished_at: float | None = None


class EmailDeliveryRegistry:
    def __init__(self, *, clock=monotonic, max_records=256, max_active=2):
        if type(max_active) is not int or max_active < 1:
            raise ValueError("동시 발송 수는 양의 정수여야 합니다.")
        if type(max_records) is not int or max_records < max_active:
            raise ValueError("발송 기록 수는 동시 발송 수 이상이어야 합니다.")
        self._clock = clock
        self._max_records = max_records
        self._max_active = max_active
        self._records = {}
        self._lock = Lock()

    def reserve(self, key, recipient_key):
        with self._lock:
            now = self._clock()
            for old_key, record in list(self._records.items()):
                if (
                    record.finished_at is not None
                    and now - record.finished_at >= RETENTION_SECONDS
                ):
                    del self._records[old_key]

            existing = self._records.get(key)
            if existing is not None:
                if existing.state == "accepted":
                    return True
                if existing.state == "unknown":
                    raise ItineraryEmailError(
                        "email_delivery_unknown",
                        "이전 메일의 접수 여부를 확인하지 못했습니다. "
                        "수신함과 스팸함을 먼저 확인해 주세요.",
                        409,
                        uncertain=True,
                    )
                raise ItineraryEmailError(
                    "email_in_progress",
                    "같은 일정을 이메일로 보내고 있습니다. 잠시 후 확인해 주세요.",
                    409,
                )

            for record in self._records.values():
                if record.recipient_key != recipient_key:
                    continue
                if record.state == "pending":
                    raise ItineraryEmailError(
                        "email_in_progress",
                        "같은 이메일로 일정을 보내고 있습니다. 잠시 후 확인해 주세요.",
                        409,
                    )
                if (
                    record.finished_at is not None
                    and now - record.finished_at < RECIPIENT_COOLDOWN_SECONDS
                ):
                    raise ItineraryEmailError(
                        "email_rate_limited",
                        "같은 이메일로 최근에 발송을 요청했습니다. "
                        "잠시 후 다시 시도해 주세요.",
                        429,
                    )

            active = sum(
                record.state == "pending"
                for record in self._records.values()
            )
            if (
                active >= self._max_active
                or len(self._records) >= self._max_records
            ):
                raise ItineraryEmailError(
                    "email_busy",
                    "이메일 발송 요청이 많습니다. 잠시 후 다시 시도해 주세요.",
                    503,
                )

            self._records[key] = DeliveryRecord(recipient_key)
            return False

    def finish(self, key, state):
        with self._lock:
            record = self._records.get(key)
            if record is None:
                return
            if state is None:
                del self._records[key]
            else:
                record.state = state
                record.finished_at = self._clock()


_deliveries = EmailDeliveryRegistry()


def deliver_itinerary_email(snapshot, recipient):
    """최근 접수 결과를 재사용하며 실제 메일을 자동 재시도하지 않습니다."""
    recipient = normalize_email_address(recipient)
    settings = load_smtp_settings()
    working = deepcopy(snapshot)
    encoded = json.dumps(
        working,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    recipient_key = hashlib.sha256(recipient.encode("ascii")).hexdigest()
    key = hashlib.sha256(
        (recipient + "\0" + encoded).encode("utf-8")
    ).hexdigest()

    if _deliveries.reserve(key, recipient_key):
        return True

    submission_started = False
    try:
        pdf = build_itinerary_pdf(working)
        message = create_pdf_message(
            pdf,
            recipient,
            settings,
            start_date=working["options"].get("start_date"),
        )
        submission_started = True
        submit_pdf_message(message, recipient, settings)
    except PdfUnavailableError as error:
        _deliveries.finish(key, None)
        raise ItineraryEmailError(
            "pdf_unavailable", str(error), 503,
        ) from None
    except ItineraryEmailError as error:
        _deliveries.finish(key, "unknown" if error.uncertain else None)
        raise
    except Exception:
        _deliveries.finish(
            key, "unknown" if submission_started else None,
        )
        if submission_started:
            raise ItineraryEmailError(
                "email_delivery_unknown",
                "메일 서버의 접수 여부를 확인하지 못했습니다. "
                "수신함과 스팸함을 먼저 확인해 주세요.",
                502,
                uncertain=True,
            ) from None
        raise ItineraryEmailError(
            "email_prepare_failed",
            "메일에 첨부할 PDF를 준비하지 못했습니다. 다시 시도해 주세요.",
            500,
        ) from None
    except BaseException:
        _deliveries.finish(
            key, "unknown" if submission_started else None,
        )
        raise

    _deliveries.finish(key, "accepted")
    return False