"""일정 PDF의 메일 작성과 SMTP 접수 처리를 담당합니다."""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime, make_msgid
import smtplib
import ssl

from email_validator import EmailNotValidError, validate_email
import truststore

from ..config import read_env_value

SMTP_TIMEOUT_SECONDS = 10
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


class ItineraryEmailError(RuntimeError):
    def __init__(self, code, message, status_code=503, *, uncertain=False):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.uncertain = uncertain


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    security: str
    username: str = field(repr=False)
    password: str = field(repr=False)
    sender: str


def normalize_email_address(value):
    if not isinstance(value, str) or any(char in value for char in "\r\n\0"):
        raise ValueError("이메일 주소 형식을 확인해 주세요.")
    value = value.strip()
    if not value or len(value) > 254:
        raise ValueError("이메일 주소 형식을 확인해 주세요.")
    try:
        result = validate_email(
            value, check_deliverability=False, allow_smtputf8=False,
        )
    except EmailNotValidError:
        raise ValueError("이메일 주소 형식을 확인해 주세요.") from None
    return result.ascii_email


def load_smtp_settings():
    if read_env_value("ITINERARY_EMAIL_ENABLED").lower() != "true":
        raise ItineraryEmailError(
            "email_not_configured",
            "이메일 발송이 아직 설정되지 않았습니다. PDF 다운로드를 이용해 주세요.",
        )
    try:
        host = read_env_value("ITINERARY_SMTP_HOST")
        security = read_env_value("ITINERARY_SMTP_SECURITY") or "starttls"
        port_text = read_env_value("ITINERARY_SMTP_PORT") or (
            "465" if security == "ssl" else "587"
        )
        port = int(port_text)
        username = read_env_value("ITINERARY_SMTP_USERNAME")
        password = read_env_value("ITINERARY_SMTP_PASSWORD")
        sender = normalize_email_address(read_env_value("ITINERARY_EMAIL_FROM"))
        if (
            not host or len(host) > 253
            or any(char.isspace() or char in "/\\\0" for char in host)
            or "://" in host
            or security not in {"starttls", "ssl"}
            or not 1 <= port <= 65535
            or not username or not password
        ):
            raise ValueError("invalid SMTP settings")
    except ValueError:
        raise ItineraryEmailError(
            "email_not_configured", "이메일 발송 설정을 확인해야 합니다.",
        ) from None
    return SmtpSettings(host, port, security, username, password, sender)


def create_pdf_message(pdf, recipient, settings, *, start_date=None):
    recipient = normalize_email_address(recipient)
    sender = normalize_email_address(settings.sender)
    if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-"):
        raise ItineraryEmailError(
            "email_invalid_pdf", "첨부할 PDF를 확인하지 못했습니다.", 500,
        )
    if len(pdf) > MAX_ATTACHMENT_BYTES:
        raise ItineraryEmailError(
            "email_attachment_too_large",
            "PDF가 이메일 첨부 크기를 초과했습니다. PDF 다운로드를 이용해 주세요.",
            413,
        )
    day = date.fromisoformat(start_date).isoformat() if start_date else "날짜미정"
    message = EmailMessage(policy=SMTP)
    message["From"] = Address(display_name="같이 가는 제주", addr_spec=sender)
    message["To"] = recipient
    message["Subject"] = "[같이 가는 제주] 여행 일정 PDF"
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[1])
    message.set_content(
        "요청하신 여행 일정 PDF를 첨부합니다.\n\n"
        "저장 당시의 일정과 안내입니다. 운영시간·교통 운행·접근 조건은 "
        "방문 전에 다시 확인해 주세요.\n"
    )
    message.add_attachment(
        pdf, maintype="application", subtype="pdf",
        filename=f"같이가는제주_{day}_여행일정.pdf",
    )
    return message


def submit_pdf_message(message, recipient, settings):
    """메일 서버 접수만 확인합니다. 수신함 도착을 보장하거나 자동 재시도하지 않습니다."""
    recipient = normalize_email_address(recipient)
    sender = normalize_email_address(settings.sender)
    if settings.security not in {"starttls", "ssl"}:
        raise ItineraryEmailError(
            "email_not_configured", "이메일 발송 설정을 확인해야 합니다.",
        )
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client = None
    submission_started = False
    try:
        if settings.security == "ssl":
            client = smtplib.SMTP_SSL(
                settings.host, settings.port,
                timeout=SMTP_TIMEOUT_SECONDS, context=context,
            )
        else:
            client = smtplib.SMTP(
                settings.host, settings.port, timeout=SMTP_TIMEOUT_SECONDS,
            )
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        client.login(settings.username, settings.password)
        submission_started = True
        refused = client.send_message(
            message, from_addr=sender, to_addrs=[recipient],
        )
        if not isinstance(refused, dict):
            raise ItineraryEmailError(
                "email_delivery_unknown",
                "메일 서버의 접수 여부를 확인하지 못했습니다.",
                502, uncertain=True,
            )
        if refused:
            raise ItineraryEmailError(
                "email_rejected",
                "메일 서버가 받는 주소를 거부했습니다. 주소를 확인해 주세요.",
                502,
            )
    except smtplib.SMTPAuthenticationError:
        raise ItineraryEmailError(
            "email_auth_failed", "메일 서버 인증 설정을 확인해야 합니다.",
        ) from None
    except (
        smtplib.SMTPRecipientsRefused,
        smtplib.SMTPSenderRefused,
        smtplib.SMTPDataError,
    ):
        raise ItineraryEmailError(
            "email_rejected",
            "메일 서버가 발송을 거부했습니다. 잠시 후 다시 확인해 주세요.",
            502,
        ) from None
    except smtplib.SMTPNotSupportedError:
        raise ItineraryEmailError(
            "email_not_configured", "메일 서버의 보안 연결 설정을 확인해야 합니다.",
        ) from None
    except (OSError, smtplib.SMTPException):
        if submission_started:
            raise ItineraryEmailError(
                "email_delivery_unknown",
                "메일 서버의 접수 여부를 확인하지 못했습니다. "
                "수신함과 스팸함을 먼저 확인해 주세요.",
                502, uncertain=True,
            ) from None
        raise ItineraryEmailError(
            "email_send_failed",
            "메일 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.",
            502,
        ) from None
    finally:
        if client is not None:
            try:
                client.quit()
            except (OSError, smtplib.SMTPException):
                try:
                    client.close()
                except OSError:
                    pass