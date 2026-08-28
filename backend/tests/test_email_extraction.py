from __future__ import annotations

from base64 import urlsafe_b64encode
import unittest

from app.services.gmail_importer import _mark_full_payload_body_fetch_status
from app.services.email_extraction import (
    build_thread_message_reader,
    gmail_payload_has_unresolved_text_body,
    has_persisted_renderable_body,
    html_body_for_reader,
    html_render_document_for_reader,
    parse_gmail_message,
    sanitize_email_html,
    sanitize_email_render_document,
)


def encoded(value: str) -> str:
    return urlsafe_b64encode(value.encode("utf-8")).decode("utf-8").rstrip("=")


class EmailExtractionTests(unittest.TestCase):
    def test_text_file_attachment_is_not_resolved_or_treated_as_message_body(self) -> None:
        resolver_calls: list[tuple[str, str]] = []

        parsed = parse_gmail_message(
            {
                "id": "msg-text-file",
                "threadId": "thread-text-file",
                "snippet": "Attached notes",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "filename": "notes.txt",
                            "body": {"attachmentId": "text-file-1", "size": 42},
                        }
                    ],
                },
            },
            user_id="user-1",
            inline_attachment_resolver=lambda message_id, attachment_id: (
                resolver_calls.append((message_id, attachment_id)) or encoded("Attachment contents")
            ),
        )

        self.assertEqual(resolver_calls, [])
        self.assertIsNone(parsed["text_body"])
        self.assertIsNone(parsed["html_render_document"])
        self.assertFalse(gmail_payload_has_unresolved_text_body(parsed["raw_payload"]))

    def test_small_inline_data_text_attachment_is_not_treated_as_message_body(self) -> None:
        parsed = parse_gmail_message(
            {
                "id": "msg-small-text-file",
                "threadId": "thread-small-text-file",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "filename": "notes.txt",
                            "body": {"data": encoded("Small attachment contents"), "size": 25},
                        }
                    ],
                },
            },
            user_id="user-1",
        )

        self.assertIsNone(parsed["text_body"])
        self.assertFalse(gmail_payload_has_unresolved_text_body(parsed["raw_payload"]))
        self.assertFalse(
            has_persisted_renderable_body(
                text_body=parsed["text_body"],
                html_body=parsed["html_body_sanitized"],
                html_render_document=parsed["html_render_document"],
                raw_payload=parsed["raw_payload"],
            )
        )

    def test_attached_rfc822_message_subtree_is_skipped(self) -> None:
        resolver_calls: list[tuple[str, str]] = []
        parsed = parse_gmail_message(
            {
                "id": "msg-attached-email",
                "threadId": "thread-attached-email",
                "payload": {
                    "mimeType": "multipart/mixed",
                    "parts": [
                        {
                            "mimeType": "message/rfc822",
                            "body": {"size": 2048},
                            "parts": [
                                {
                                    "mimeType": "text/plain",
                                    "body": {"attachmentId": "attached-email-text", "size": 200},
                                },
                                {
                                    "mimeType": "text/html",
                                    "body": {"data": encoded("<html><body>Attached email body</body></html>")},
                                },
                            ],
                        }
                    ],
                },
            },
            user_id="user-1",
            inline_attachment_resolver=lambda message_id, attachment_id: (
                resolver_calls.append((message_id, attachment_id)) or encoded("Attached email plain body")
            ),
        )

        self.assertEqual(resolver_calls, [])
        self.assertIsNone(parsed["text_body"])
        self.assertIsNone(parsed["html_render_document"])
        self.assertFalse(gmail_payload_has_unresolved_text_body(parsed["raw_payload"]))

    def test_resolved_html_makes_unresolved_plain_alternative_terminal(self) -> None:
        parsed = parse_gmail_message(
            {
                "id": "msg-alternative",
                "threadId": "thread-alternative",
                "payload": {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"attachmentId": "large-plain", "size": 4096},
                        },
                        {
                            "mimeType": "text/html",
                            "body": {"data": encoded("<html><body>Complete HTML alternative</body></html>")},
                        },
                    ],
                },
            },
            user_id="user-1",
            inline_attachment_resolver=lambda _message_id, _attachment_id: None,
        )

        self.assertTrue(gmail_payload_has_unresolved_text_body(parsed["raw_payload"]))
        self.assertIn("Complete HTML alternative", parsed["html_render_document"] or "")
        marked = _mark_full_payload_body_fetch_status(parsed)
        self.assertEqual(marked["body_fetch_status"], "fetched")
        parts = marked["raw_payload"]["payload"]["parts"]
        self.assertNotIn("data", parts[1].get("body", {}))
        self.assertEqual(parts[0]["body"]["attachmentId"], "large-plain")

    def test_large_text_body_is_resolved_from_gmail_attachment(self) -> None:
        payload = {
            "id": "msg-large-text",
            "threadId": "thread-large-text",
            "snippet": "Metadata preview",
            "payload": {
                "mimeType": "text/plain",
                "body": {"attachmentId": "large-text-1", "size": 42},
            },
        }
        resolver_calls: list[tuple[str, str]] = []

        def resolve(message_id: str, attachment_id: str) -> str:
            resolver_calls.append((message_id, attachment_id))
            return encoded("Complete large plain-text body")

        parsed = parse_gmail_message(
            payload,
            user_id="user-1",
            inline_attachment_resolver=resolve,
        )

        self.assertEqual(resolver_calls, [("msg-large-text", "large-text-1")])
        self.assertEqual(parsed["text_body"], "Complete large plain-text body")
        self.assertFalse(gmail_payload_has_unresolved_text_body(parsed["raw_payload"]))

    def test_plain_text_body_preserves_reader_paragraphs_and_header_lines(self) -> None:
        body = (
            "Mailing list introduction\n\n"
            "From: Speaker <speaker@example.com>\n"
            "Subject: Conference proposal\n"
            "To: list@example.com\n"
            "Content-Type: text/plain; charset=utf-8\n\n"
            "Hi all,\n\n"
            "The proposal deadline is next week.\n"
        )
        parsed = parse_gmail_message(
            {
                "id": "msg-structured-plain",
                "threadId": "thread-structured-plain",
                "payload": {
                    "mimeType": "text/plain",
                    "body": {"data": encoded(body)},
                },
            },
            user_id="user-1",
        )

        self.assertEqual(parsed["text_body"], body.strip())
        reader = build_thread_message_reader(
            html_render_document=None,
            html_body=None,
            text_body=parsed["text_body"],
            snippet=None,
            headers={},
        )
        self.assertIn("Mailing list introduction", reader["primary_text"])
        self.assertIn("From: Speaker", reader["quoted_text"] or "")
        self.assertIn("Hi all,", reader["quoted_text"] or "")

    def test_mailing_list_digest_keeps_embedded_message_visible(self) -> None:
        body = (
            "Send Example List submissions to list@example.org\n\n"
            "Today's Topics:\n\n"
            "1. Conference proposal (Speaker)\n\n"
            "Message: 1\n"
            "Date: Friday\n"
            "From: Speaker <speaker@example.com>\n"
            "Subject: Conference proposal\n"
            "To: list@example.org\n\n"
            "Hi all,\n\n"
            "The proposal deadline is next week.\n"
        )

        reader = build_thread_message_reader(
            html_render_document=None,
            html_body=None,
            text_body=body,
            snippet=None,
            headers={"list-id": "<example-list.example.org>"},
        )

        self.assertFalse(reader["quote_detected"])
        self.assertIsNone(reader["quoted_text"])
        self.assertIn("From: Speaker", reader["primary_text"])
        self.assertIn("The proposal deadline is next week.", reader["primary_text"])

    def test_failed_large_text_body_resolution_remains_incomplete(self) -> None:
        parsed = parse_gmail_message(
            {
                "id": "msg-large-text",
                "threadId": "thread-large-text",
                "snippet": "Metadata preview",
                "payload": {
                    "mimeType": "text/html",
                    "body": {"attachmentId": "large-html-1", "size": 42},
                },
            },
            user_id="user-1",
            inline_attachment_resolver=lambda _message_id, _attachment_id: None,
        )

        self.assertIsNone(parsed["text_body"])
        self.assertIsNone(parsed["html_render_document"])
        self.assertTrue(gmail_payload_has_unresolved_text_body(parsed["raw_payload"]))

    def test_metadata_snippet_is_not_treated_as_full_text_body(self) -> None:
        parsed = parse_gmail_message(
            {
                "id": "msg-1",
                "threadId": "thread-1",
                "historyId": "10",
                "labelIds": ["INBOX"],
                "snippet": "Open https://example.com/app to continue.",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Continue application"},
                        {"name": "From", "value": "Cal State <apply@example.com>"},
                    ]
                },
            },
            user_id="user-1",
        )

        self.assertEqual(parsed["snippet"], "Open https://example.com/app to continue.")
        self.assertIsNone(parsed["text_body"])
        self.assertIsNone(parsed["html_body_sanitized"])
        self.assertIn("example.com", parsed["extracted_signals"]["domains"])

    def test_bank_correspondence_reference_extracts_ticket_id(self) -> None:
        parsed = parse_gmail_message(
            {
                "id": "msg-northstar-ref",
                "threadId": "thread-northstar-ref",
                "labelIds": ["INBOX"],
                "snippet": "The unique reference number for this correspondence is 106756996.",
                "payload": {
                    "headers": [
                        {
                            "name": "Subject",
                            "value": "Re: Unauthorized Credit Card Consent Request",
                        },
                        {
                            "name": "From",
                            "value": "Support Department <grievance.redressalcc@northstar.example>",
                        },
                    ],
                    "mimeType": "text/plain",
                    "body": {
                        "data": encoded(
                            "Dear Customer,\n"
                            "The unique reference number for this correspondence is 106756996.\n"
                        )
                    },
                },
            },
            user_id="user-1",
        )

        self.assertEqual(parsed["extracted_signals"]["ticket_id"], "106756996")

    def test_html_is_preserved_for_reader_when_gmail_supplies_html(self) -> None:
        rich_html = """
        <html><head><style>.headline { font-family: Arial; font-size: 32px; }</style></head><body>
          <table style="width:100%"><tr><td style="font-size:42px">
            <img src="cid:logo-image"> <span class="headline">CAL STATE APPLY</span>
          </td></tr></table>
          <script>alert("x")</script>
        </body></html>
        """
        parsed = parse_gmail_message(
            {
                "id": "msg-2",
                "threadId": "thread-2",
                "historyId": "11",
                "labelIds": ["INBOX"],
                "snippet": "CAL STATE APPLY",
                "payload": {
                    "headers": [{"name": "Subject", "value": "Application started"}],
                    "mimeType": "multipart/related",
                    "parts": [
                        {
                            "mimeType": "text/html",
                            "body": {"data": encoded(rich_html)},
                        },
                        {
                            "mimeType": "image/png",
                            "headers": [{"name": "Content-ID", "value": "<logo-image>"}],
                            "body": {"data": encoded("image-bytes")},
                        },
                    ],
                },
            },
            user_id="user-1",
        )

        html_body = parsed["html_body_sanitized"]
        render_document = parsed["html_render_document"]
        self.assertIsNotNone(html_body)
        self.assertNotIn("<script", html_body or "")
        self.assertNotIn("<style", html_body or "")
        self.assertIsNotNone(render_document)
        self.assertIn("<style>", render_document or "")
        self.assertNotIn("<script", render_document or "")
        self.assertIn("data:image/png;base64,", render_document or "")
        self.assertIn("CAL STATE APPLY", parsed["text_body"] or "")
        self.assertIsNotNone(html_body_for_reader(html_body))

        simple_html = sanitize_email_html('<div>Hello<br><a href="https://example.com">Open link</a></div>')
        self.assertEqual(html_body_for_reader(simple_html), simple_html)

    def test_otp_text_link_html_preserves_original_html_for_reader(self) -> None:
        otp_html = """
        <!doctype html>
        <html>
        <head>
          <meta name="viewport" content="width=device-width">
          <!--[if mso]><style>body { width: 600px; }</style><![endif]-->
        </head>
        <body>
          <div>Hello,</div>
          <div><a href="https://links.mcdonaldsapps.com/otp">205759</a> is your one-time password (OTP) for the McDonald's app.</div>
          <div>You can tap on the code to have it automatically applied.</div>
          <div>Enjoy the app!</div>
          <img src="https://u15089226.ct.sendgrid.net/wf/open?upn=tracking-pixel" width="1" height="1">
        </body></html>
        """
        parsed = parse_gmail_message(
            {
                "id": "msg-otp",
                "threadId": "thread-otp",
                "payload": {
                    "headers": [{"name": "Subject", "value": "Your One-Time Password"}],
                    "mimeType": "text/html",
                    "body": {"data": encoded(otp_html)},
                },
            },
            user_id="user-1",
        )

        self.assertIsNotNone(parsed["html_render_document"])
        self.assertIsNone(parsed["html_body_sanitized"])
        self.assertEqual(html_render_document_for_reader(parsed["html_render_document"]), parsed["html_render_document"])
        self.assertIsNone(html_body_for_reader(parsed["html_body_sanitized"]))
        self.assertIn("links.mcdonaldsapps.com/otp", parsed["html_render_document"] or "")
        self.assertIn("205759 is your one-time password", parsed["text_body"] or "")
        self.assertNotIn("sendgrid", parsed["text_body"] or "")

    def test_single_presentation_table_with_tracking_pixel_preserves_original_html(self) -> None:
        html = """
        <!doctype html>
        <html><head>
          <style>p { margin: 0 0 8px 0 !important; line-height: 20px !important; }</style>
        </head><body>
          <div style="display:none">Just a few more fields.</div>
          <table width="100%" cellspacing="0" cellpadding="0" role="presentation">
            <tbody><tr><td style="font-family: Arial; font-size: 14px; line-height: 20px;">
              <p>Hey,</p>
              <p>Looks like you started a speedrun application but didn't hit submit.</p>
              <p>Finish your app here: <a href="https://speedrun.example">SR007</a></p>
            </td></tr></tbody>
          </table>
          <img src="https://go2.a16z.com/trk?t=1" width="1" height="1" style="display:none !important;" alt="">
        </body></html>
        """
        parsed = parse_gmail_message(
            {
                "id": "msg-speedrun",
                "threadId": "thread-speedrun",
                "payload": {
                    "headers": [{"name": "Subject", "value": "your speedrun app is almost done"}],
                    "mimeType": "text/html",
                    "body": {"data": encoded(html)},
                },
            },
            user_id="user-1",
        )

        self.assertIsNotNone(parsed["html_render_document"])
        self.assertIsNone(parsed["html_body_sanitized"])
        self.assertIn("go2.a16z.com/trk", parsed["html_render_document"] or "")
        self.assertIn("Looks like you started a speedrun application", parsed["text_body"] or "")
        self.assertNotIn("Just a few more fields", parsed["text_body"] or "")
        self.assertNotIn("go2.a16z.com/trk", parsed["text_body"] or "")

    def test_multipart_alternative_chooses_best_html_part(self) -> None:
        parsed = parse_gmail_message(
            {
                "id": "msg-alt",
                "threadId": "thread-alt",
                "payload": {
                    "headers": [{"name": "Subject", "value": "Alternative"}],
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": encoded("Plain body")}},
                        {
                            "mimeType": "text/html",
                            "body": {"data": encoded('<html><body><table style="width:100%"><tr><td><img src="https://example.com/first.png">First rich body</td></tr></table></body></html>')},
                        },
                        {
                            "mimeType": "text/html",
                            "body": {"data": encoded('<html><body><table style="width:100%"><tr><td><img src="https://example.com/second.png">Second richer body with more content</td></tr></table></body></html>')},
                        },
                    ],
                },
            },
            user_id="user-1",
        )

        self.assertIn("Second richer body", parsed["html_render_document"] or "")
        self.assertNotIn("First rich body</td></tr></table></body></html>\n<html", parsed["html_render_document"] or "")

    def test_render_sanitizer_removes_unsafe_markup_but_keeps_layout_css(self) -> None:
        html = """
        <html><head>
          <meta http-equiv="refresh" content="0; url=https://evil.example">
          <style>td { font-family: Arial; }</style>
        </head><body onload="bad()">
          <form><input value="x"></form>
          <a href="javascript:alert(1)">bad</a>
          <table><tr><td>Safe</td></tr></table>
        </body></html>
        """
        cleaned = sanitize_email_render_document(html)

        self.assertIn("<style>td { font-family: Arial; }</style>", cleaned)
        self.assertIn("<table>", cleaned)
        self.assertNotIn("http-equiv", cleaned)
        self.assertNotIn("<form", cleaned)
        self.assertNotIn("onload", cleaned)
        self.assertNotIn("javascript:", cleaned)

    def test_renderable_body_check_rejects_snippet_only_metadata(self) -> None:
        self.assertFalse(
            has_persisted_renderable_body(
                text_body="Short Gmail snippet",
                html_body=None,
                html_render_document=None,
                raw_payload={"payload": {"headers": []}},
            )
        )
        self.assertTrue(
            has_persisted_renderable_body(
                text_body="Full text body",
                html_body=None,
                html_render_document=None,
                raw_payload={
                    "payload": {
                        "mimeType": "text/plain",
                        "body": {"data": encoded("Full text body")},
                    }
                },
            )
        )

    def test_thread_message_reader_splits_outlook_thread_chrome(self) -> None:
        outlook_html = """
        <html><body>
          <div>Classification - Internal</div>
          <div>Hi</div>
          <div>Kindly confirm once the account is funded to set limit of $10,000</div>
          <div>Please mark your relationship manager in all mails.</div>
          <div>Best Regards,</div>
          <div>Test User</div>
          <div>Northstar Bank</div>
          <div>Contacts:</div>
          <div>Providing Rate for CCIL deal - Test Person on 123 4567890</div>
          <div>From: TestUser &lt;demo@example.test&gt; Sent: Tuesday, May 12, 2026 10:18 AM To: NorthstarFXclearretail &lt;northstarfx@northstar.example&gt; Subject: Re: Regarding trading limit request</div>
          <div><b>(Warning) -This is an External Email: Be very careful before clicking on any Links/ Sharing data / Downloading attachments.</b></div>
          <blockquote>
            <div>Hi,</div>
            <div>Please enable/increase the limit for FX Retail transactions on my account.</div>
          </blockquote>
          <div>IMPORTANT COMMUNICATION UPDATE</div>
          <div>Official email domains: @northstar.example | @northstarbank.example</div>
          <div>Disclaimer: This message is confidential and intended only for the recipient.</div>
        </body></html>
        """

        reader = build_thread_message_reader(
            html_render_document=outlook_html,
            html_body=outlook_html,
            text_body=None,
            snippet=None,
            headers={},
        )

        self.assertEqual(
            reader["primary_text"],
            "Hi\n\nKindly confirm once the account is funded to set limit of $10,000\n\nPlease mark your relationship manager in all mails.",
        )
        self.assertEqual(
            reader["markers"],
            [
                {"kind": "classification", "label": "Internal", "text": "Classification - Internal"},
                {
                    "kind": "external_warning",
                    "label": "External",
                    "text": "(Warning) -This is an External Email: Be very careful before clicking on any Links/ Sharing data / Downloading attachments.",
                },
            ],
        )
        self.assertIn("Best Regards", reader["signature_text"] or "")
        self.assertIn("From: TestUser", reader["quoted_text"] or "")
        self.assertIn("Please enable/increase the limit", reader["quoted_text"] or "")
        self.assertIn("IMPORTANT COMMUNICATION UPDATE", reader["footer_text"] or "")
        self.assertIn("Disclaimer:", reader["footer_text"] or "")
        self.assertTrue(reader["original_html_available"])
        self.assertEqual(reader["render_mode"], "plain_conversation")
        self.assertFalse(reader["html_is_rich"])
        self.assertTrue(reader["quote_detected"])

    def test_thread_message_reader_classifies_plain_html_reply_as_conversation(self) -> None:
        html = """
        <html><body>
          <div>Hello TestUser,</div>
          <div>Yes, you may request reconsideration in StudentPortal.</div>
          <div>Test Person</div>
          <div>From: "TestUser" &lt;hi@example.com&gt;</div>
          <div>Sent: Friday, May 29, 2026 4:33 AM</div>
          <div>To: university-admissions@example.edu</div>
          <div>Subject: Reconsideration request</div>
          <div>Hi,</div>
          <div>I was admitted to State University Campus for Engineering for Fall Semester.</div>
        </body></html>
        """

        reader = build_thread_message_reader(
            html_render_document=html,
            html_body=html,
            text_body=None,
            snippet=None,
            headers={},
        )

        self.assertEqual(
            reader["primary_text"],
            "Hello TestUser,\n\nYes, you may request reconsideration in StudentPortal.\n\nTest Person",
        )
        self.assertIn('From: "TestUser"', reader["quoted_text"] or "")
        self.assertEqual(reader["render_mode"], "plain_conversation")
        self.assertFalse(reader["html_is_rich"])
        self.assertTrue(reader["quote_detected"])

    def test_thread_message_reader_classifies_designed_email_as_rich_html(self) -> None:
        html = """
        <html><body>
          <table class="wrapper" role="presentation">
            <tr><td><img src="https://example.com/hero.png" width="640" height="260"></td></tr>
            <tr><td class="headline" style="font-size:32px">Product digest</td></tr>
            <tr><td><a style="background:#111;color:#fff" href="https://example.com">Read update</a></td></tr>
          </table>
        </body></html>
        """

        reader = build_thread_message_reader(
            html_render_document=html,
            html_body=html,
            text_body=None,
            snippet=None,
            headers={},
        )

        self.assertEqual(reader["render_mode"], "rich_html")
        self.assertTrue(reader["html_is_rich"])

    def test_thread_message_reader_classifies_styled_single_table_notice_as_rich_html(self) -> None:
        html = """
        <!doctype html>
        <html><body style="margin:0;background:#f5f5f5">
          <table class="renewal-card" width="100%" role="presentation" style="max-width:640px;margin:0 auto;background:#ffffff">
            <tbody>
              <tr><td class="eyebrow" style="padding:24px 32px 8px;color:#666;letter-spacing:1px">RENEWAL NOTICE</td></tr>
              <tr><td class="headline" style="padding:0 32px;font-size:30px;line-height:36px">Your domains need attention</td></tr>
              <tr><td class="body-copy" style="padding:20px 32px;font-size:16px;line-height:24px">Some domains in your account have expired and are about to be suspended. Renew them to keep your websites and services online.</td></tr>
              <tr><td class="callout" style="padding:16px 32px;background:#fff4d6">Review the expiration dates and renewal status for each affected domain.</td></tr>
              <tr><td class="button-row" style="padding:24px 32px 32px"><a href="https://domain-harbor.example/account" style="display:inline-block;padding:12px 18px;background:#ef476f;color:#fff">Review domains</a></td></tr>
            </tbody>
          </table>
        </body></html>
        """

        reader = build_thread_message_reader(
            html_render_document=html,
            html_body=html,
            text_body=None,
            snippet="RENEWAL NOTICE Your domains need attention.",
            headers={},
        )

        self.assertEqual(reader["render_mode"], "rich_html")
        self.assertTrue(reader["html_is_rich"])


if __name__ == "__main__":
    unittest.main()
