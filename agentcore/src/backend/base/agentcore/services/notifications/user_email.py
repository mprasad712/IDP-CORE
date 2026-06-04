from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from email.message import EmailMessage
from html import escape
import logging
from pathlib import Path
import smtplib
import ssl


TEMPLATES_DIR = Path(__file__).with_suffix("").parent / "templates"
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SmtpConfig:
    host: str
    port: int
    from_email: str
    from_name: str | None
    use_tls: bool
    use_ssl: bool
    timeout_seconds: int
    username: str | None = None
    password: str | None = None


def _coalesce_setting(
    settings,
    *names: str,
) -> str | None:
    for name in names:
        value = getattr(settings, name, None)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
        elif value is not None:
            return str(value)
    return None


def _coalesce_bool_setting(settings, *names: str, default: bool) -> bool:
    for name in names:
        value = getattr(settings, name, None)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip():
            return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _coalesce_int_setting(settings, *names: str, default: int) -> int:
    for name in names:
        value = getattr(settings, name, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return int(value.strip())
            except ValueError:
                continue
    return default


def _load_smtp_config(settings) -> SmtpConfig:
    host = _coalesce_setting(settings, "smtp_host", "smtp_server")
    port_raw = _coalesce_int_setting(settings, "smtp_port", default=0)
    from_email = _coalesce_setting(settings, "smtp_from_email", "smtp_from")

    if not host or not port_raw or not from_email:
        raise ValueError("SMTP is not fully configured.")

    from_name = _coalesce_setting(settings, "smtp_from_name")
    use_ssl = _coalesce_bool_setting(settings, "smtp_use_ssl", default=False)
    use_tls = _coalesce_bool_setting(settings, "smtp_use_tls", default=not use_ssl)
    # Cap the worst-case SMTP wait at 7s. Healthy SMTP completes well under a
    # second; the previous 20s default meant a misconfigured/slow mail server
    # blocked every user-edit request for up to 20 seconds. Override via the
    # smtp_timeout_seconds setting if a longer wait is genuinely required.
    timeout_seconds = _coalesce_int_setting(settings, "smtp_timeout_seconds", default=7)
    username = _coalesce_setting(settings, "smtp_username", "smtp_user")
    password = _coalesce_setting(settings, "smtp_password")

    return SmtpConfig(
        host=host,
        port=port_raw,
        from_email=from_email,
        from_name=from_name,
        use_tls=use_tls,
        use_ssl=use_ssl,
        timeout_seconds=timeout_seconds,
        username=username,
        password=password,
    )


def _render_template(template_name: str, context: dict[str, str]) -> str:
    template_path = TEMPLATES_DIR / template_name
    template = template_path.read_text(encoding="utf-8")
    return template.format(**context)


def _render_changed_fields_html(changed_fields: list[str]) -> str:
    if not changed_fields:
        return "<li>No field details were provided.</li>"
    return "".join(f"<li>{escape(field)}</li>" for field in changed_fields)


def _render_changed_fields_text(changed_fields: list[str]) -> str:
    if not changed_fields:
        return "  - No field details were provided."
    return "\n".join(f"  - {field}" for field in changed_fields)


def _build_message(
    *,
    smtp_config: SmtpConfig,
    recipient_email: str,
    subject: str,
    context: dict[str, str],
) -> EmailMessage:
    message = EmailMessage()
    message["To"] = recipient_email
    message["From"] = (
        f"{smtp_config.from_name} <{smtp_config.from_email}>"
        if smtp_config.from_name
        else smtp_config.from_email
    )
    message["Subject"] = subject
    message.set_content(_render_template("user_notification.txt", context))
    message.add_alternative(
        _render_template("user_notification.html", context),
        subtype="html",
    )
    return message


def _build_raw_message(
    *,
    smtp_config: SmtpConfig,
    recipient_email: str,
    subject: str,
    text_body: str,
    html_body: str,
) -> EmailMessage:
    message = EmailMessage()
    message["To"] = recipient_email
    message["From"] = (
        f"{smtp_config.from_name} <{smtp_config.from_email}>"
        if smtp_config.from_name
        else smtp_config.from_email
    )
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    return message


def _send_message_sync(
    *,
    smtp_config: SmtpConfig,
    message: EmailMessage,
) -> None:
    logger.info(
        "Sending email via SMTP host=%s port=%s tls=%s ssl=%s",
        smtp_config.host,
        smtp_config.port,
        smtp_config.use_tls,
        smtp_config.use_ssl,
    )
    if smtp_config.use_ssl:
        with smtplib.SMTP_SSL(
            smtp_config.host,
            smtp_config.port,
            timeout=smtp_config.timeout_seconds,
            context=ssl.create_default_context(),
        ) as server:
            if smtp_config.username and smtp_config.password:
                server.login(smtp_config.username, smtp_config.password)
            server.send_message(message)
        logger.info("Email sent successfully via SMTP_SSL.")
        return

    with smtplib.SMTP(
        smtp_config.host,
        smtp_config.port,
        timeout=smtp_config.timeout_seconds,
    ) as server:
        server.ehlo()
        if smtp_config.use_tls:
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if smtp_config.username and smtp_config.password:
            server.login(smtp_config.username, smtp_config.password)
        server.send_message(message)
    logger.info("Email sent successfully via SMTP.")


async def send_user_notification_email(
    *,
    settings,
    recipient_email: str | None,
    recipient_name: str,
    subject: str,
    headline: str,
    intro_text: str,
    summary_text: str,
    actor_name: str,
    changed_fields: list[str],
    organization_name: str | None = None,
    department_name: str | None = None,
) -> tuple[bool, str | None]:
    if not recipient_email:
        logger.warning("Skipping notification email because recipient email is missing.")
        return False, "Recipient email is missing for this user."

    try:
        smtp_config = _load_smtp_config(settings)
    except ValueError as exc:
        logger.warning("Skipping notification email because SMTP config is incomplete: %s", exc)
        return False, str(exc)

    context = {
        "recipient_name": recipient_name,
        "headline": headline,
        "intro_text": intro_text,
        "summary_text": summary_text,
        "actor_name": actor_name,
        "organization_name": organization_name or "-",
        "department_name": department_name or "-",
        "changed_fields_text": _render_changed_fields_text(changed_fields),
        "changed_fields_html": _render_changed_fields_html(changed_fields),
    }

    try:
        message = _build_message(
            smtp_config=smtp_config,
            recipient_email=recipient_email,
            subject=subject,
            context=context,
        )
        await asyncio.to_thread(
            _send_message_sync,
            smtp_config=smtp_config,
            message=message,
        )
        return True, None
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send notification email via SMTP: %s", exc)
        return False, str(exc)


async def send_admin_user_created_email(
    *,
    settings,
    recipient_email: str,
    recipient_name: str,
    actor_name: str,
    role: str,
    organization_name: str | None,
    department_name: str | None,
    set_password_link: str,
) -> tuple[bool, str | None]:
    try:
        smtp_config = _load_smtp_config(settings)
    except ValueError as exc:
        logger.warning("Skipping admin-created-user email because SMTP config is incomplete: %s", exc)
        return False, str(exc)

    subject = "Your Digital Workmate IDP account has been created"

    dept_row = f"<b>Department:</b> {escape(department_name or '-')}<br>" if department_name else ""
    org_row = f"<b>Organization:</b> {escape(organization_name or '-')}<br>" if organization_name else ""

    html_body = f"""<!DOCTYPE html>
<html>
<body style="font-family: Calibri, Arial, sans-serif; font-size: 11pt; color: #000; max-width: 600px; margin: 0 auto;">
<div style="border-top: 4px solid #D04A02; padding: 32px 24px;">
  <p>Hello <b>{escape(recipient_name)}</b>,</p>
  <p>An administrator has created an account for you on <b>Digital Workmate IDP</b>.</p>
  <p>
    <b>Username:</b> {escape(recipient_email)}<br>
    <b>Role:</b> {escape(role)}<br>
    {org_row}{dept_row}
    <b>Created by:</b> {escape(actor_name)}
  </p>
  <p>To get started, set your password by clicking the button below:</p>
  <p style="text-align: center; margin: 32px 0;">
    <a href="{set_password_link}"
       style="background:#D04A02; color:#fff; text-decoration:none; padding:12px 28px;
              border-radius:4px; font-weight:bold; display:inline-block;">
      Set Your Password
    </a>
  </p>
  <p>Or copy and paste this link into your browser:</p>
  <p style="word-break:break-all; color:#555; font-size:10pt;">{escape(set_password_link)}</p>
  <p style="color:#888; font-size:9pt; margin-top:24px;">
    This link expires in 72 hours. If you did not expect this email, please contact your administrator.
  </p>
  <p style="color:#666; font-size: 9pt;">
    This is an automated notification from Digital Workmate IDP. Please do not reply to this email.
  </p>
</div>
</body>
</html>"""

    text_body = (
        f"Hello {recipient_name},\n\n"
        "An administrator has created an account for you on Digital Workmate IDP.\n\n"
        f"Username: {recipient_email}\n"
        f"Role: {role}\n"
        f"Organization: {organization_name or '-'}\n"
        f"Department: {department_name or '-'}\n"
        f"Created by: {actor_name}\n\n"
        "To set your password, visit the link below:\n"
        f"{set_password_link}\n\n"
        "This link expires in 72 hours.\n\n"
        "— Digital Workmate IDP Team"
    )

    try:
        message = _build_raw_message(
            smtp_config=smtp_config,
            recipient_email=recipient_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )
        await asyncio.to_thread(
            _send_message_sync,
            smtp_config=smtp_config,
            message=message,
        )
        return True, None
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send admin-created-user email via SMTP: %s", exc)
        return False, str(exc)


async def send_verification_email(
    *,
    settings,
    recipient_email: str,
    recipient_name: str,
    verification_link: str,
) -> tuple[bool, str | None]:
    try:
        smtp_config = _load_smtp_config(settings)
    except ValueError as exc:
        logger.warning("Skipping verification email because SMTP config is incomplete: %s", exc)
        return False, str(exc)

    subject = "Verify your email address - Digital Workmate IDP"

    text_body = (
        f"Hello {recipient_name},\n\n"
        "Thank you for registering with Digital Workmate IDP.\n\n"
        "Please verify your email address by visiting the link below:\n"
        f"{verification_link}\n\n"
        "This link expires in 24 hours.\n\n"
        "If you did not create an account, you can ignore this email.\n\n"
        "— Digital Workmate IDP Team"
    )

    html_body = f"""<!DOCTYPE html>
<html>
<body style="font-family: Calibri, Arial, sans-serif; font-size: 11pt; color: #000; max-width: 600px; margin: 0 auto;">
<div style="border-top: 4px solid #D04A02; padding: 32px 24px;">
  <p>Hello <b>{escape(recipient_name)}</b>,</p>
  <p>Thank you for registering with <b>Digital Workmate IDP</b>.</p>
  <p>Please verify your email address by clicking the button below:</p>
  <p style="text-align: center; margin: 32px 0;">
    <a href="{verification_link}"
       style="background:#D04A02; color:#fff; text-decoration:none; padding:12px 28px;
              border-radius:4px; font-weight:bold; display:inline-block;">
      Verify Email Address
    </a>
  </p>
  <p>Or copy and paste this link into your browser:</p>
  <p style="word-break:break-all; color:#555; font-size:10pt;">{escape(verification_link)}</p>
  <p style="color:#888; font-size:9pt; margin-top:24px;">
    This link expires in 24 hours. If you did not create an account, you can ignore this email.
  </p>
  <p style="color:#666; font-size: 9pt;">
    This is an automated notification from Digital Workmate IDP. Please do not reply to this email.
  </p>
</div>
</body>
</html>"""

    try:
        message = _build_raw_message(
            smtp_config=smtp_config,
            recipient_email=recipient_email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )
        await asyncio.to_thread(
            _send_message_sync,
            smtp_config=smtp_config,
            message=message,
        )
        return True, None
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send verification email via SMTP: %s", exc)
        return False, str(exc)
