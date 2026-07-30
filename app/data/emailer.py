"""
emailer.py — sends a rendered report export to one or more recipients over SMTP.

Reads SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/SMTP_FROM from .env.

Every failure here has to arrive at the user as something they can act on. A mail
send that reports success without delivering anything is the worst outcome
available - the user has no reason to check, and finds out days later - so the
exceptions below are deliberately specific and the endpoint maps each to its own
status code and message.
"""

import os
import re
import smtplib
import socket
import ssl
from email.message import EmailMessage
from typing import List, Sequence, Tuple

from dotenv import load_dotenv


class EmailNotConfigured(RuntimeError):
    """No SMTP credentials in the environment. Not a failure, just not set up."""


class EmailSendFailed(RuntimeError):
    """The mail server was reached but refused the message.

    Carries `kind` so the endpoint can pick a status code without re-inspecting
    the underlying smtplib exception: "auth" | "recipients" | "unreachable" | "other".
    """

    def __init__(self, message: str, kind: str = "other"):
        super().__init__(message)
        self.kind = kind


# Deliberately permissive. This catches the typo that would otherwise cost a
# round trip to the mail server ("me@", "me.example.com"); it is not an attempt
# to validate deliverability, which only the mail server can do.
_ADDRESS_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")

# A wrong SMTP_HOST otherwise hangs the request until the OS gives up on the
# connection, which on Windows is over a minute of a spinning button.
_TIMEOUT_SECONDS = 20


def _smtp_settings() -> dict:
    """SMTP config, read fresh on every call.

    Read lazily rather than at import time for the reason documented in
    report_builder._debug_files_enabled: load_dotenv() only runs as a side effect
    of importing AI_Engine, so a module-level os.getenv here would race the import
    order and see nothing. Re-reading also means you can fill in credentials in
    .env and retry without restarting uvicorn.
    """
    load_dotenv()
    user = os.getenv("SMTP_USER") or ""
    return {
        "host": (os.getenv("SMTP_HOST") or "").strip(),
        "port": int(os.getenv("SMTP_PORT", "587") or 587),
        "user": user.strip(),
        "password": os.getenv("SMTP_PASSWORD") or "",
        "sender": (os.getenv("SMTP_FROM") or user).strip(),
    }


def _credential_state(settings: dict) -> str:
    """Classify how the environment is filled in.

    Credentials are optional - a relay that needs no login is a real
    configuration, and it is the only way to test this module end to end without
    an account (see the local mail catcher recipe in docs/EXPORT_FEATURE.md).
    But they are all-or-nothing: a half-filled .env is a typo, not a mode, and
    silently dropping the login because the password line is blank would send
    mail somewhere nobody intended.

    Returns:
        "off" (no host), "auth", "noauth", or "partial".
    """
    if not settings["host"]:
        return "off"
    user, password = settings["user"], settings["password"]
    if user and password:
        return "auth"
    if not user and not password:
        return "noauth"
    return "partial"


def smtp_config_error() -> str:
    """The reason email can't be sent, or "" when it can.

    One source of truth: the endpoint uses this for its pre-flight 503 and
    send_report_email re-checks it, so the two can't drift into disagreeing
    about what counts as configured.
    """
    settings = _smtp_settings()
    state = _credential_state(settings)

    if state == "off":
        return ("Email isn't configured on this server. Set SMTP_HOST, SMTP_USER and "
                "SMTP_PASSWORD in .env (see .env.example) and restart the API.")
    if state == "partial":
        missing = "SMTP_PASSWORD" if settings["user"] else "SMTP_USER"
        return (f"Email is half-configured: {missing} is empty in .env. Set both "
                f"SMTP_USER and SMTP_PASSWORD, or leave both blank for a mail server "
                f"that needs no login.")
    return ""


def smtp_configured() -> bool:
    """Whether email can be sent at all.

    The UI asks this up front so it can disable the email row with a reason
    showing, rather than accepting an address and failing afterwards.
    """
    return not smtp_config_error()


def validate_recipients(recipients: Sequence[str]) -> List[str]:
    """Trim, split, de-duplicate and syntax-check recipient addresses.

    Accepts a comma- or semicolon-separated string in any element, since that is
    how the UI's single input arrives.

    Raises:
        ValueError: naming the first address that isn't usable.
    """
    flat: List[str] = []
    for entry in recipients or []:
        for part in re.split(r"[,;]", str(entry)):
            addr = part.strip()
            if addr and addr not in flat:
                flat.append(addr)

    if not flat:
        raise ValueError("No recipient address was given.")

    for addr in flat:
        if not _ADDRESS_RE.match(addr):
            raise ValueError(f"Not a valid email address: {addr}")

    return flat


def _login(server, user: str, password: str) -> None:
    """Authenticate, if there is anything to authenticate with.

    A rejected login is not always an SMTPAuthenticationError: some servers
    (Mailtrap among them) simply drop the connection on bad credentials. That
    arrives as SMTPServerDisconnected, which would otherwise be reported as
    "couldn't reach the mail server" and send the user off to check SMTP_HOST -
    the one setting that was right.
    """
    if not (user and password):
        return
    try:
        server.login(user, password)
    except smtplib.SMTPAuthenticationError:
        raise  # already the clearest possible signal; handled by the caller
    except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError) as e:
        raise EmailSendFailed(
            "The mail server closed the connection during login, which normally "
            "means SMTP_USER or SMTP_PASSWORD is wrong. For Gmail and Yahoo this "
            "must be an app password, not your account password.",
            kind="auth",
        ) from e


def send_report_email(
    recipients: Sequence[str],
    subject: str,
    body_text: str,
    attachment: bytes,
    filename: str,
    mimetype: Tuple[str, str] = ("application", "pdf"),
) -> List[str]:
    """Send `attachment` to `recipients` as a file attachment.

    The export goes as an attachment in both formats rather than inline in the
    body: Gmail and Outlook strip <style> blocks and refuse data: image sources,
    so an inline HTML body would arrive unstyled and chartless. The .html
    attachment carries its charts as data URIs and opens correctly offline.

    Args:
        mimetype: ("application", "pdf") or ("text", "html").

    Returns:
        The validated recipient list actually sent to.

    Raises:
        EmailNotConfigured: SMTP_HOST/USER/PASSWORD absent.
        ValueError: an address that can't be used.
        EmailSendFailed: the server refused or couldn't be reached.
    """
    settings = _smtp_settings()
    problem = smtp_config_error()
    if problem:
        raise EmailNotConfigured(problem)

    to = validate_recipients(recipients)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings["sender"]
    msg["To"] = ", ".join(to)
    msg.set_content(body_text)
    msg.add_attachment(
        attachment, maintype=mimetype[0], subtype=mimetype[1], filename=filename
    )

    host, port = settings["host"], settings["port"]
    user, password = settings["user"], settings["password"]
    context = ssl.create_default_context()

    try:
        # 465 is implicit TLS and has no STARTTLS to negotiate; 587 and anything
        # else upgrade an initially plaintext connection.
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT_SECONDS, context=context) as server:
                _login(server, user, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=_TIMEOUT_SECONDS) as server:
                server.ehlo()
                # Asked for rather than assumed: local mail catchers (Mailpit,
                # aiosmtpd) offer no STARTTLS, and calling it unconditionally is
                # what stopped this module from being testable without an account.
                if server.has_extn("starttls"):
                    server.starttls(context=context)
                    server.ehlo()  # the server's extension list is reset by STARTTLS
                elif user and password:
                    # Refuse rather than downgrade. Anything else puts the password
                    # on the wire in the clear.
                    raise EmailSendFailed(
                        f"{host}:{port} does not offer STARTTLS, so SMTP_USER and "
                        f"SMTP_PASSWORD would be sent unencrypted. Use port 465, or "
                        f"clear both if this server needs no login.",
                        kind="other",
                    )
                _login(server, user, password)
                server.send_message(msg)

    except smtplib.SMTPAuthenticationError as e:
        raise EmailSendFailed(
            "The mail server rejected the credentials in SMTP_USER / SMTP_PASSWORD. "
            "For Gmail this must be an App Password, not your account password.",
            kind="auth",
        ) from e

    except smtplib.SMTPRecipientsRefused as e:
        refused = ", ".join((e.recipients or {}).keys()) or ", ".join(to)
        raise EmailSendFailed(
            f"The mail server refused these recipients: {refused}", kind="recipients"
        ) from e

    except (socket.timeout, TimeoutError) as e:
        raise EmailSendFailed(
            f"Couldn't reach the mail server at {host}:{port} within "
            f"{_TIMEOUT_SECONDS}s. Check SMTP_HOST and SMTP_PORT.",
            kind="unreachable",
        ) from e

    except smtplib.SMTPException as e:
        # Must be caught BEFORE OSError, because smtplib.SMTPException subclasses
        # it. Testing isinstance(e, OSError) to tell the two apart - as this used
        # to - is always True, so every protocol error was reported as a
        # connectivity failure telling the user to check a correct SMTP_HOST.
        raise EmailSendFailed(
            f"The mail server at {host}:{port} refused the message — "
            f"{type(e).__name__}: {e}",
            kind="other",
        ) from e

    except OSError as e:
        # DNS failure, refused connection, dropped socket: never reached the server.
        raise EmailSendFailed(
            f"Couldn't send mail via {host}:{port} — {type(e).__name__}: {e}",
            kind="unreachable",
        ) from e

    return to
