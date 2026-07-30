"""
Integration tests for the export endpoints.

These drive the real routes against a hand-built session, so no LLM quota is spent
and the whole backend of the feature is covered without a browser: the chart PNGs a
browser would post are stubbed with a real one-pixel PNG.

The assertions to care about are the ones about labelling and degradation. An export
that renders beautifully but drops the "computed" / "AI note" distinction has
regressed the thing the dashboard was reworked to fix, and an export that 500s
because one chart image arrived truncated has turned a cosmetic problem into a
total failure.
"""

import base64
import io
import re
import smtplib

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.api as api
from tests.test_generate_report_api import (  # reuse the existing session harness
    make_session, orders_frame, recommendation,
)


@pytest.fixture
def client():
    return TestClient(api.app)


@pytest.fixture(autouse=True)
def clean_sessions():
    api.SESSIONS.clear()
    yield
    api.SESSIONS.clear()


@pytest.fixture(autouse=True)
def smtp_env(monkeypatch):
    """Configured by default, so only the tests that care opt out.

    Tests opt out with setenv("SMTP_HOST", ""), never delenv: emailer calls
    load_dotenv() on every read, which would refill a deleted key from the
    developer's real .env and make the result depend on their local mail setup.
    An empty value is already present, so load_dotenv leaves it alone.
    """
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    monkeypatch.setenv("SMTP_FROM", "sender@example.com")


def png_data_url(width=40, height=20):
    """A real PNG, as export.js would produce."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (37, 99, 235)).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def generate(client, session_id, report_type):
    res = client.post(
        "/api/generate-report",
        json={"session_id": session_id, "report_type": report_type},
    )
    assert res.status_code == 200, res.text
    return res.json()


def export(client, session_id, report_types=("A",), fmt="pdf", **extra):
    return client.post(
        f"/api/export/{session_id}",
        json={"report_types": list(report_types), "format": fmt, **extra},
    )


def squashed(text):
    """Collapse runs of whitespace, so assertions don't depend on template wrapping."""
    return " ".join(text.split())


def appendix_cells(html):
    """The text of every <td> in the export's data tables.

    Scoped deliberately: asserting against the whole document catches substrings in
    the stylesheet - "provenance" contains "nan" - and would pass or fail for
    reasons that have nothing to do with the rendered values.
    """
    body = html.split('<table class="data"', 1)[-1]
    return re.findall(r"<td[^>]*>(.*?)</td>", body, re.S)


# ---------------------------------------------------------------------------
# Session storage — the prerequisite for exporting more than one report
# ---------------------------------------------------------------------------

def test_generating_two_reports_keeps_both_server_side(client, monkeypatch):
    """A single overwritten slot made a combined export impossible to fulfil: the
    browser still showed A and B, but only B survived on the server."""
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    generate(client, sid, "B")

    stored = api.SESSIONS[sid]["reports"]
    assert sorted(stored) == ["A", "B"]
    assert stored["A"]["report_type"] == "A"
    assert stored["B"]["report_type"] == "B"


def test_legacy_report_key_still_points_at_the_latest(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    generate(client, sid, "B")
    assert api.SESSIONS[sid]["report"] is api.SESSIONS[sid]["reports"]["B"]


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def test_pdf_download(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")

    res = export(client, sid, ["A"], "pdf", chart_images={"A": png_data_url()})
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")

    disposition = res.headers["content-disposition"]
    assert "attachment" in disposition
    assert "ai-dashboard-report-a-order-frequency-trend" in disposition
    assert sid in disposition
    assert disposition.endswith('.pdf"')


def test_html_download_is_self_contained(client, monkeypatch):
    """The file gets opened from a Downloads folder with the app shut down, so it
    must not reference anything it doesn't carry."""
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")

    res = export(client, sid, ["A"], "html", chart_images={"A": png_data_url()})
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/html")

    html = res.text
    assert "Order Frequency Trend" in html
    for external in ("http://", "https://", "<script"):
        assert external not in html, f"HTML export references {external!r}"
    assert "data:image/png;base64," in html


def test_export_carries_the_provenance_labelling(client, monkeypatch):
    """Computed statistics and the model's own words must stay distinguishable once
    the document leaves the app - it has no chips legend of its own otherwise."""
    sid, _, rec = make_session(monkeypatch)
    generate(client, sid, "A")

    html = export(client, sid, ["A"], "html").text

    assert "computed" in html
    assert "AI question" in html
    assert "AI note" in html
    # The model's rationale is present, but not under a findings heading.
    assert rec["rationale_bullets"][0] in html
    assert "Key Insights" not in html
    assert "before</em> any data was aggregated" in html


def test_appendix_reports_its_own_truncation(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch, df=orders_frame(n=1200))
    generate(client, sid, "A")

    html = export(client, sid, ["A"], "html").text
    assert "Showing 200 of 1,200 rows." in squashed(html)
    assert len(appendix_cells(html)) == 200 * 2  # two columns


def test_appendix_can_be_switched_off(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")

    html = export(client, sid, ["A"], "html", include_appendix=False).text
    assert "Report data" not in html
    # The report itself is still all there.
    assert "Order Frequency Trend" in html


# ---------------------------------------------------------------------------
# Combined export
# ---------------------------------------------------------------------------

def test_combined_export_compares_the_selected_reports(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    generate(client, sid, "B")

    res = export(client, sid, ["A", "B"], "html")
    assert res.status_code == 200, res.text
    html = res.text

    assert "Comparative Report" in html
    assert "Comparison" in html
    assert "Executive summary" in html
    # The hazard warning has to be present, and above the numbers it warns about.
    assert "comparable across columns" in html
    assert html.index("comparable across columns") < html.index("Chart type")

    assert "ai-dashboard-reports-ab-combined" in res.headers["content-disposition"]


def test_comparison_matrix_says_not_measured_rather_than_blank(client, monkeypatch):
    """A ranking report has no trend and a trend report has no concentration. A blank
    cell would read as "we measured this and found nothing", which is a lie."""
    ranked = pd.DataFrame({"region": ["N", "S", "E", "W"], "revenue_sum": [500, 300, 150, 50]})
    rec_b = recommendation(pattern_used="RANKING", report_name="Revenue by Region",
                           plotly_config={"chart_type": "bar", "x_axis": "region",
                                          "y_axis": "revenue_sum", "title": "t"})

    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")  # TREND

    # Swap in the ranking recommendation and frame for B.
    api.SESSIONS[sid]["recommendations"]["recommendations"][1] = rec_b
    monkeypatch.setattr(api, "generate_report", lambda *a, **k: ranked)
    generate(client, sid, "B")

    html = export(client, sid, ["A", "B"], "html").text

    assert "not measured" in html
    assert "no ordered axis" in html      # B has no trend
    assert "not a ranking" in html        # A has no concentration


# ---------------------------------------------------------------------------
# Degraded paths — one broken part must not cost the whole document
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "data:image/png;base64,zzzz",                  # undecodable
    "data:image/png;base64," + base64.b64encode(b"not a png").decode(),
    "data:image/svg+xml;base64,PHN2Zy8+",          # wrong media type
    "https://example.com/chart.png",               # not a data URL at all
    "",
])
def test_an_unusable_chart_image_does_not_fail_the_export(client, monkeypatch, bad):
    """Reaching reportlab, a truncated PNG surfaces as an uncaught OSError from
    inside the PDF writer. Dropping the image costs a chart; raising costs the file."""
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")

    res = export(client, sid, ["A"], "pdf", chart_images={"A": bad})
    assert res.status_code == 200, res.text
    assert res.content.startswith(b"%PDF")


def test_a_report_with_no_chart_still_exports(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    api.SESSIONS[sid]["reports"]["A"]["chart"] = None

    html = export(client, sid, ["A"], "html").text
    assert "No chart could be drawn" in html
    # Every number is still there.
    assert "Distribution" in html
    assert "Report data" in html


def test_report_with_unavailable_stats_still_exports(client, monkeypatch):
    """Two categorical axes: nothing numeric to compute, and the export has to say so
    rather than printing zeroes."""
    df = pd.DataFrame({"region": ["N", "S", "E"], "segment": ["a", "b", "c"]})
    rec = recommendation(plotly_config={"chart_type": "bar", "x_axis": "region",
                                        "y_axis": "segment", "title": "t"})
    sid, _, _ = make_session(monkeypatch, df=df, rec=rec)
    generate(client, sid, "A")

    res = export(client, sid, ["A"], "pdf")
    assert res.status_code == 200, res.text
    assert "No statistics available" in export(client, sid, ["A"], "html").text


def test_nan_and_timestamps_render_readably_in_the_appendix(client, monkeypatch):
    """The rows held server-side are raw pandas, so without formatting the appendix
    prints 'nan' and '2023-01-01 00:00:00' in every cell."""
    df = orders_frame(n=5)
    df.loc[2, "order_id_count"] = np.nan
    sid, _, _ = make_session(monkeypatch, df=df)
    generate(client, sid, "A")

    cells = [c.strip() for c in appendix_cells(export(client, sid, ["A"], "html").text)]

    assert "2023-01-01" in cells          # a date, not a midnight timestamp
    assert not any("00:00:00" in c for c in cells)
    assert not any(c.lower() == "nan" for c in cells)
    assert "—" in cells                    # the missing count reads as an em dash


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------

def test_ungenerated_report_is_rejected_by_name(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")

    res = export(client, sid, ["A", "B"])
    assert res.status_code == 400
    assert "Report B" in res.json()["detail"]


def test_unknown_session_is_404(client):
    assert export(client, "nope").status_code == 404


def test_unsupported_format_is_422(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    assert export(client, sid, ["A"], "docx").status_code == 422


def test_empty_selection_is_422(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    assert export(client, sid, [], "pdf").status_code == 422


def test_selection_is_normalised_and_deduplicated(client, monkeypatch):
    """Lowercase and repeats must not change the document or its filename."""
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")

    res = export(client, sid, ["a", "A", "a"], "pdf")
    assert res.status_code == 200, res.text
    assert "-report-a-" in res.headers["content-disposition"]


def test_stray_chart_image_keys_are_ignored(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")

    res = export(client, sid, ["A"], "pdf",
                 chart_images={"A": png_data_url(), "Z": "data:image/png;base64,zz"})
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def test_status_lists_generated_reports_and_email_readiness(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    generate(client, sid, "C")

    body = client.get(f"/api/export/{sid}/status").json()
    assert body["generated"] == ["A", "C"]
    assert body["export_available"] is True
    assert body["email_configured"] is True


def test_status_reports_email_off_when_unconfigured(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    monkeypatch.setenv("SMTP_HOST", "")

    assert client.get(f"/api/export/{sid}/status").json()["email_configured"] is False


def test_status_for_unknown_session_is_404(client):
    assert client.get("/api/export/nope/status").status_code == 404


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

class FakeSMTP:
    """Records what a send would have transmitted.

    Stands in for both smtplib.SMTP and smtplib.SMTP_SSL, so `context=` has to be
    accepted: SMTP_SSL is constructed with it, and a fake that couldn't take it is
    the reason the port-465 branch went untested.
    """

    sent = []
    raises = None            # raised from login()
    raises_on_send = None    # raised from send_message()
    offers_starttls = True   # a local mail catcher offers none; a real relay does
    log = []                 # ("SSL"|"plain", method name) in call order

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port
        FakeSMTP.log.append((self.flavour, "connect"))

    flavour = "plain"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        FakeSMTP.log.append((self.flavour, "ehlo"))

    def has_extn(self, name):
        return name.lower() == "starttls" and FakeSMTP.offers_starttls

    def starttls(self, context=None):
        FakeSMTP.log.append((self.flavour, "starttls"))

    def login(self, user, password):
        FakeSMTP.log.append((self.flavour, "login"))
        if FakeSMTP.raises:
            raise FakeSMTP.raises

    def send_message(self, msg):
        FakeSMTP.log.append((self.flavour, "send"))
        if FakeSMTP.raises_on_send:
            raise FakeSMTP.raises_on_send
        FakeSMTP.sent.append(msg)


class FakeSMTPSSL(FakeSMTP):
    """The implicit-TLS flavour, so tests can tell the two branches apart."""

    flavour = "SSL"


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.sent = []
    FakeSMTP.raises = None
    FakeSMTP.raises_on_send = None
    FakeSMTP.offers_starttls = True
    FakeSMTP.log = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)
    return FakeSMTP


def email(client, session_id, **overrides):
    body = {
        "report_types": ["A"],
        "format": "pdf",
        "recipients": ["someone@example.com"],
    }
    body.update(overrides)
    return client.post(f"/api/export/{session_id}/email", json=body)


def test_email_sends_the_export_as_an_attachment(client, monkeypatch, fake_smtp):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")

    res = email(client, sid)
    assert res.status_code == 200, res.text
    assert res.json()["recipients"] == ["someone@example.com"]

    assert len(fake_smtp.sent) == 1
    attachments = list(fake_smtp.sent[0].iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_filename().endswith(".pdf")
    assert attachments[0].get_payload(decode=True).startswith(b"%PDF")


def test_email_html_format_attaches_html(client, monkeypatch, fake_smtp):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")

    assert email(client, sid, format="html").status_code == 200
    attachment = next(iter(fake_smtp.sent[0].iter_attachments()))
    assert attachment.get_content_type() == "text/html"
    assert attachment.get_filename().endswith(".html")


def test_email_body_explains_the_labels(client, monkeypatch, fake_smtp):
    """The recipient never saw the app, so the chips need explaining in the mail."""
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    email(client, sid)

    body = fake_smtp.sent[0].get_body(preferencelist=("plain",)).get_content()
    assert "computed" in body
    assert "AI note" in body
    assert sid in body


def test_email_accepts_a_comma_separated_list(client, monkeypatch, fake_smtp):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")

    res = email(client, sid, recipients=["a@example.com, b@example.com"])
    assert res.status_code == 200, res.text
    assert res.json()["recipients"] == ["a@example.com", "b@example.com"]


def test_email_without_smtp_config_is_503_and_says_what_to_set(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    monkeypatch.setenv("SMTP_HOST", "")

    res = email(client, sid)
    assert res.status_code == 503
    detail = res.json()["detail"]
    assert "SMTP_HOST" in detail and ".env" in detail


def test_bad_address_is_422_naming_the_address(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")

    res = email(client, sid, recipients=["nope@"])
    assert res.status_code == 422
    assert "nope@" in res.json()["detail"]


def test_rejected_credentials_are_502_and_mention_app_passwords(client, monkeypatch, fake_smtp):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    fake_smtp.raises = smtplib.SMTPAuthenticationError(535, b"nope")

    res = email(client, sid)
    assert res.status_code == 502
    assert "App Password" in res.json()["detail"]
    assert fake_smtp.sent == []


def test_unreachable_mail_server_is_504(client, monkeypatch, fake_smtp):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    fake_smtp.raises = TimeoutError("timed out")

    res = email(client, sid)
    assert res.status_code == 504
    assert "smtp.example.com" in res.json()["detail"]


def test_email_for_an_ungenerated_report_is_rejected_before_sending(client, monkeypatch, fake_smtp):
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")

    res = email(client, sid, report_types=["B"])
    assert res.status_code == 400
    assert fake_smtp.sent == []


def test_port_465_uses_implicit_tls_and_never_calls_starttls(client, monkeypatch, fake_smtp):
    """465 has no plaintext phase to upgrade - STARTTLS there is a protocol error."""
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    monkeypatch.setenv("SMTP_PORT", "465")

    assert email(client, sid).status_code == 200
    assert len(fake_smtp.sent) == 1

    flavours = {flavour for flavour, _ in fake_smtp.log}
    assert flavours == {"SSL"}, "port 465 must use SMTP_SSL, not SMTP"
    assert "starttls" not in [call for _, call in fake_smtp.log]
    assert ("SSL", "login") in fake_smtp.log


def test_unreachable_host_is_504_naming_host_and_port(client, monkeypatch, fake_smtp):
    """The typo'd-SMTP_HOST case: DNS failure and refused connections are OSError."""
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    fake_smtp.raises = OSError("[Errno 11001] getaddrinfo failed")

    res = email(client, sid)
    assert res.status_code == 504
    detail = res.json()["detail"]
    assert "smtp.example.com" in detail and "587" in detail
    assert fake_smtp.sent == []


def test_relay_without_credentials_is_configured_and_skips_login(client, monkeypatch, fake_smtp):
    """A local mail catcher has no account - that's a valid setup, not a broken one."""
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", "1025")
    monkeypatch.setenv("SMTP_USER", "")
    monkeypatch.setenv("SMTP_PASSWORD", "")
    monkeypatch.setenv("SMTP_FROM", "ai-dashboard@localhost")
    fake_smtp.offers_starttls = False  # catchers don't offer it

    assert client.get(f"/api/export/{sid}/status").json()["email_configured"] is True

    assert email(client, sid).status_code == 200, "no-auth relay should send"
    assert len(fake_smtp.sent) == 1
    assert "login" not in [call for _, call in fake_smtp.log]
    assert "starttls" not in [call for _, call in fake_smtp.log]


def test_credentials_are_never_sent_over_an_unencrypted_connection(client, monkeypatch, fake_smtp):
    """Downgrading instead of refusing would put SMTP_PASSWORD on the wire in clear."""
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    fake_smtp.offers_starttls = False   # but SMTP_USER/PASSWORD are still set

    res = email(client, sid)
    assert res.status_code == 502
    assert "STARTTLS" in res.json()["detail"]
    assert fake_smtp.sent == []
    assert "login" not in [call for _, call in fake_smtp.log]


def test_a_disconnect_during_login_is_reported_as_a_credentials_problem(
    client, monkeypatch, fake_smtp
):
    """Mailtrap drops the connection on bad credentials instead of returning 535.

    Observed against the real server. Left unhandled this surfaces as 504 "couldn't
    reach the mail server, check SMTP_HOST" - which is the one setting that was right.
    """
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    fake_smtp.raises = smtplib.SMTPServerDisconnected("Connection unexpectedly closed")

    res = email(client, sid)
    assert res.status_code == 502, "a rejected login is not a connectivity failure"
    detail = res.json()["detail"]
    assert "SMTP_PASSWORD" in detail and "app password" in detail
    assert fake_smtp.sent == []


def test_protocol_errors_are_502_not_504(client, monkeypatch, fake_smtp):
    """smtplib.SMTPException subclasses OSError, so order of `except` decides this.

    Catching OSError first silently reclassified every protocol error as
    "unreachable" - the bug this guards.
    """
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    fake_smtp.raises_on_send = smtplib.SMTPDataError(554, b"message rejected")

    res = email(client, sid)
    assert res.status_code == 502
    assert "refused the message" in res.json()["detail"]


def test_half_filled_credentials_are_503_naming_the_empty_one(client, monkeypatch):
    """A blank password next to a filled-in user is a typo, not a no-auth relay."""
    sid, _, _ = make_session(monkeypatch)
    generate(client, sid, "A")
    monkeypatch.setenv("SMTP_PASSWORD", "")

    assert client.get(f"/api/export/{sid}/status").json()["email_configured"] is False

    res = email(client, sid)
    assert res.status_code == 503
    assert "SMTP_PASSWORD" in res.json()["detail"]
