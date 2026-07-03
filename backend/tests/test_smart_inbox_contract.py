from __future__ import annotations

import json
import os
import random
import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.db.mail_groups import AppSessionSnapshotRecord, GmailMessageRecord, MailObjectBundle, MailObjectRecord, replace_smart_inbox_projection, upsert_gmail_messages
from app.schemas.domain import (
    AppSessionResponse,
    AppSessionSyncState,
    AppSessionUser,
    AttentionItem,
    DashboardResponse,
    FeedResponse,
    GmailThreadRow,
    GoogleAuthState,
    MailboxResponse,
    PostLoginReadinessResponse,
    SmartInboxRow,
    SmartInboxResponse,
    SmartReadinessResponse,
    SmartRelatedSuggestion,
)
from app.services.mail_groups import (
    APP_SESSION_MAILBOX_LIMIT,
    FIRST_BATCH_SIZE,
    MailboxDisplayClusterEntry,
    SMART_FIRST_READY_TARGET_MESSAGES,
    SMART_HOT_WINDOW_DAYS,
    SMART_HOT_WINDOW_MESSAGE_CAP,
    SMART_HOT_WINDOW_THREAD_CAP,
    enqueue_first_run,
    enqueue_app_session_snapshot_refresh,
    _enqueue_body_fetch_for_smart_inbox,
    _gmail_row_from_canonical_thread,
    _gmail_row_from_mailbox_display_cluster,
    _mailbox_display_cluster_key,
    _mailbox_display_cluster_rows,
    _mailbox_rows_absorbing_exact_references,
    _mailbox_rows_absorbing_trade_references,
    _smart_inbox_from_mailbox,
    _smart_inbox_from_mailbox_and_objects,
    _smart_inbox_response_from_rows,
    _smart_inbox_storage_rows,
    _smart_inbox_with_offline_status,
    _smart_related_storage_suggestions,
    _smart_readiness_from_mailbox,
    _smart_workflow_cluster_rows,
    _smart_work_storage_items,
    _smart_work_queue_from_dashboard,
    _smart_work_queue_from_snapshot,
    _smart_work_queue_from_smart_inbox,
    _recent_visible_days,
)
from app.services.mail_group_config import SMART_INBOX_DISPLAY_THREAD_LIMIT
from app.services.gmail_importer import FIRST_BATCH_SIZE as IMPORTER_FIRST_BATCH_SIZE, FIRST_RUN_RECENT_DAYS, FIRST_RUN_RECENT_MAX_MESSAGES
from app.workers.main import _run_job


SMART_INBOX_GENERATED_BASE_SEEDS = (20260613, 130719, 991027)
SMART_INBOX_PROVIDER_ROOTS = (
    "auroraledger",
    "brightcargo",
    "cedarcloud",
    "emberpay",
    "frostdesk",
    "harborloop",
    "lumenmarket",
    "northmint",
    "orchidworks",
    "pixelrail",
)
SMART_INBOX_STREAM_TOPICS = (
    "Design system workspace digest",
    "Database backup completed",
    "Prototype sharing changes",
    "Weekly project milestone update",
    "Security checklist reminder",
    "New integration rollout",
    "Quarterly usage report",
    "Team invite accepted",
)
SMART_INBOX_REFERENCE_CASES = (
    ("ticket_id", "support_case", "case"),
    ("invoice_id", "billing", "invoice"),
    ("tracking_id", "logistics", "tracking"),
    ("booking_id", "logistics", "booking"),
    ("application_id", "application", "application"),
    ("order_id", "logistics", "order"),
)


class SmartInboxContractTests(unittest.TestCase):
    def test_app_session_defaults_include_smart_contract(self) -> None:
        response = AppSessionResponse(
            user=AppSessionUser(id="user-1", email="me@example.com"),
            readiness=PostLoginReadinessResponse(
                mode="first_time",
                stage="importing_recent_gmail",
                ready_to_enter=False,
                dashboard_ready=False,
                mailbox_ready=False,
                ready_dashboard_count=0,
                ready_mail_group_count=0,
                full_import_running=True,
                full_import_completed=False,
            ),
            dashboard=DashboardResponse(auth=GoogleAuthState(available=True, connected=True), feed=FeedResponse()),
            mailbox=MailboxResponse(label="inbox", total_threads=0),
            sync=AppSessionSyncState(full_import_running=True),
        )

        payload = response.model_dump(mode="json")

        self.assertIn("smart_inbox", payload)
        self.assertIn("smart_work_queue", payload)
        self.assertIn("smart_readiness", payload)
        self.assertEqual(payload["smart_inbox"]["total_rows"], 0)
        self.assertEqual(payload["smart_work_queue"]["total_open"], 0)

    def test_snapshot_record_keeps_old_test_constructors_compatible(self) -> None:
        record = AppSessionSnapshotRecord(
            user_id="user-1",
            dashboard={},
            mailbox={},
            sync={},
            updated_at="2026-06-11T00:00:00+00:00",
        )

        self.assertEqual(record.smart_inbox, {})
        self.assertEqual(record.smart_work_queue, {})
        self.assertEqual(record.smart_readiness, {})

    def test_mailbox_rows_become_smart_inbox_rows_with_source_links(self) -> None:
        mailbox = MailboxResponse(
            label="inbox",
            total_threads=2,
            loaded_threads=2,
            sections=[
                {
                    "id": "today",
                    "title": "Today",
                    "rows": [
                        _thread_row(
                            thread_id="thread-1",
                            message_count=3,
                            ai_group_id="group-1",
                            presentation_status="ai_ready",
                            children=[("msg-1", "thread-1"), ("msg-2", "thread-1")],
                        ),
                        _thread_row(
                            thread_id="thread-2",
                            message_count=1,
                            ai_group_id=None,
                            presentation_status="fallback",
                            children=[],
                        ),
                    ],
                }
            ],
        )

        smart = _smart_inbox_from_mailbox(mailbox)

        self.assertEqual(smart.total_rows, 2)
        self.assertEqual(smart.ready_count, 1)
        self.assertEqual(smart.partial_count, 1)
        self.assertEqual(smart.sections[0].rows[0].row_type, "verified_group")
        self.assertEqual(smart.sections[0].rows[0].reader_thread_id, "thread-1")
        self.assertEqual(smart.sections[0].rows[0].source_thread_ids, ["thread-1"])
        self.assertEqual(smart.sections[0].rows[0].source_message_ids, ["latest-thread-1", "msg-1", "msg-2"])
        self.assertEqual(smart.sections[0].rows[1].row_type, "normal")
        self.assertEqual(smart.sections[0].rows[1].reader_thread_id, "thread-2")

    def test_smart_inbox_prefers_ai_title_and_summary_over_raw_mailbox_text(self) -> None:
        mailbox = MailboxResponse(
            label="inbox",
            total_threads=1,
            loaded_threads=1,
            sections=[
                {
                    "id": "today",
                    "title": "Today",
                    "rows": [
                        GmailThreadRow(
                            thread_id="thread-noisy-ai-ready",
                            entity_id="thread-noisy-ai-ready",
                            title="Raw provider subject line",
                            href="/v1/mailbox/threads/thread-noisy-ai-ready",
                            latest_source_record_id="message-noisy",
                            latest_received_at="2026-06-11T10:00:00+00:00",
                            latest_message_at="2026-06-11T10:00:00+00:00",
                            latest_subject="Raw provider subject line",
                            latest_sender="Provider <alerts@example.com>",
                            sender="Provider",
                            participants=["Provider"],
                            message_count=1,
                            summary="Translation missing: en.mailers.appointment.alt_text.base Continue verifying your identity Please have your ORIGINAL documents ready.",
                            ai_title="Identity verification still needs completion",
                            ai_summary="ID.me says identity verification is still incomplete and asks you to continue with original documents ready.",
                            snippet="Translation missing: en.mailers.appointment.alt_text.base",
                            label_ids=["INBOX"],
                            labels=["INBOX"],
                            action_type="open",
                            action_type_key="open",
                            priority=40,
                            current_state="waiting",
                            lifecycle_state="scheduled",
                            enrichment_status="ready",
                            presentation_status="ai_ready",
                        )
                    ],
                }
            ],
        )

        smart = _smart_inbox_from_mailbox(mailbox)
        rows = [row for section in smart.sections for row in section.rows]

        self.assertEqual(rows[0].title, "Identity verification still needs completion")
        self.assertEqual(
            rows[0].summary,
            "ID.me says identity verification is still incomplete and asks you to continue with original documents ready.",
        )
        self.assertNotIn("Translation missing", rows[0].summary)

    def test_canonical_thread_summary_cleans_template_noise_for_ai_titled_message(self) -> None:
        message = _gmail_message(
            message_id="identity-message",
            gmail_thread_id="identity-thread",
            subject="Action Required: Continue verifying your identity",
            sender="ID.me <help@id.me>",
            ai_title="Identity verification still needs to be completed",
            snippet="Translation missing: en.mailers.appointment.alt_text.base Continue verifying your identity Please have your ORIGINAL documents ready.",
            text_body="Continue verifying your identity Please have your ORIGINAL documents ready to show an ID.me Trusted Referee.",
            extracted_signals={"sender_domain": "id.me"},
            headers={},
        )

        row = _gmail_row_from_canonical_thread("identity-thread", [message], "inbox", None)

        self.assertEqual(row.title, "Identity verification still needs to be completed")
        self.assertEqual(
            row.summary,
            "ID.me says identity verification still needs to be completed and asks you to continue with original documents ready.",
        )
        self.assertNotIn("Translation missing", row.summary or "")

    def test_canonical_thread_title_normalizes_internal_connector_casing(self) -> None:
        message = _gmail_message(
            message_id="market-update",
            gmail_thread_id="market-thread",
            subject="Market update",
            sender='"Screener Test" <test@screener.in>',
            ai_title="Market update for Government Of Singapore holdings",
            snippet="Government of Singapore holdings update.",
            extracted_signals={"sender_domain": "screener.in"},
            headers={},
        )

        row = _gmail_row_from_canonical_thread("market-thread", [message], "inbox", None)

        self.assertEqual(row.sender, "Screener Test")
        self.assertEqual(row.title, "Market update for Government of Singapore holdings")

    def test_canonical_thread_summary_compresses_statement_boilerplate_for_ai_titled_message(self) -> None:
        message = _gmail_message(
            message_id="statement-message",
            gmail_thread_id="statement-thread",
            subject="Statements for Accounts under Customer ID 499-XXXXXX",
            sender="HSBC <bank-notification@example.com>",
            ai_title="HSBC statement for your account",
            snippet="Dear Customer, We enclose the statement for your account in the name of MR DEMO USER.",
            text_body="Dear Customer, We enclose the statement for your account in the name of MR DEMO USER. The statement comes to you in an easy to view PDF format. Password protection This statement is secured by a unique password.",
            extracted_signals={"sender_domain": "notification.bank.example"},
            headers={},
        )

        row = _gmail_row_from_canonical_thread("statement-thread", [message], "inbox", None)

        self.assertEqual(row.title, "HSBC account statement PDF")
        self.assertEqual(
            row.summary,
            "HSBC sent an account statement PDF for your records. The statement is password-protected.",
        )
        self.assertNotIn("Dear Customer", row.summary or "")

    def test_canonical_thread_title_polishes_rawish_single_message_titles(self) -> None:
        cases = [
            (
                _gmail_message(
                    message_id="aws-cost-anomaly",
                    gmail_thread_id="aws-cost-anomaly-thread",
                    subject="Getting started with AWS Cost Anomaly Detection - Electronic Mail (165098158252)",
                    sender="AWS <anomalydetection@costalerts.amazonaws.com>",
                    ai_title="AWS Cost Anomaly Detection set up for your account",
                    snippet="AWS Account: Electronic Mail (165098158252). As part of your enablement of AWS Cost Explorer, AWS Cost Anomaly Detection was enabled.",
                    extracted_signals={"sender_domain": "costalerts.amazonaws.com"},
                    headers={},
                ),
                "AWS Cost Anomaly Detection enabled",
            ),
            (
                _gmail_message(
                    message_id="fx-summary",
                    gmail_thread_id="fx-summary-thread",
                    subject="FX-Retail - Trade Summary for the day 02-Jun-2026",
                    sender="CCIL India <fxnoreply@ccilindia.co.in>",
                    ai_title="Daily FX-Retail trade summary",
                    snippet="Kindly find attached the FX-Retail trade summary for the day 02-Jun-2026.",
                    extracted_signals={"sender_domain": "ccilindia.co.in"},
                    headers={},
                ),
                "FX-Retail trade summary for 02-Jun-2026",
            ),
            (
                _gmail_message(
                    message_id="pycon-ticket",
                    gmail_thread_id="pycon-ticket-thread",
                    subject="[Action required] Claim your ticket for PyCon DE & PyData 2026",
                    sender="PyCon DE Ticket Office <tickets@pycon.de>",
                    ai_title="Claim your PyCon DE & PyData ticket",
                    snippet="You are invited to take part in PyCon DE & PyData 2026 remotely.",
                    extracted_signals={"sender_domain": "pycon.de"},
                    headers={},
                ),
                "PyCon DE & PyData 2026 ticket available",
            ),
        ]

        for message, expected_title in cases:
            with self.subTest(message=message.message_id):
                row = _gmail_row_from_canonical_thread(message.gmail_thread_id, [message], "inbox", None)
                self.assertEqual(row.title, expected_title)
                self.assertNotIn("your", (row.title or "").lower())

    def test_canonical_thread_summary_compresses_support_auto_ack_boilerplate(self) -> None:
        message = _gmail_message(
            message_id="complaint-auto-ack",
            gmail_thread_id="complaint-auto-thread",
            subject="<Auto> '1788458582' Unauthorized Credit Card Consent Request and Data Privacy Concern",
            sender="Support Department <grievance.redressalcc@northstar.example>",
            ai_title="Northstar Bank: auto-acknowledgment for grievance 105715521",
            snippet="Dear Customer, Thank you for writing to us. This is a system generated response to acknowledge receipt of your e-mail to Grievance Redressal Cell.",
            text_body=(
                "Dear Customer, Thank you for writing to us. This is a system generated response to acknowledge receipt of your e-mail "
                "to Grievance Redressal Cell. Our Bank official will respond to your e-mail in 3 working days. "
                "The subject line contains the reference number pertaining to this query through which all your future Email correspondence can be tracked."
            ),
            extracted_signals={"sender_domain": "northstar.example"},
            headers={},
        )

        row = _gmail_row_from_canonical_thread("complaint-auto-thread", [message], "inbox", None)

        self.assertEqual(row.title, "Northstar Bank: auto-acknowledgment for grievance 105715521 awaiting response")
        self.assertEqual(row.summary, "Northstar Bank acknowledged your grievance and says someone will respond within 3 working days.")
        self.assertNotIn("Dear Customer", row.summary or "")
        self.assertNotIn("system generated", (row.summary or "").lower())

    def test_canonical_thread_summary_compresses_account_deletion_warning(self) -> None:
        message = _gmail_message(
            message_id="capacities-delete",
            gmail_thread_id="capacities-delete-thread",
            subject="Your account is scheduled for deletion",
            sender="Capacities <noreply@capacities.io>",
            ai_title="Deletion warning for Capacities account",
            snippet="You haven't been active on Capacities for more than a year.",
            text_body=(
                "Your account is scheduled for deletion Your account is scheduled for deletion "
                "Account email: user@example.com Quick heads-up: Your Capacities account hasn’t had any activity for a long time. "
                "Therefore, we scheduled your account for deletion. Cancel scheduled deletion. "
                "If you do nothing, your account and all associated data will be permanently deleted after 30 days."
            ),
            extracted_signals={"sender_domain": "capacities.io"},
            headers={},
        )

        row = _gmail_row_from_canonical_thread("capacities-delete-thread", [message], "inbox", None)

        self.assertEqual(row.title, "Deletion warning for Capacities account")
        self.assertEqual(
            row.summary,
            "Capacities says your account is scheduled for deletion after inactivity. Cancel the scheduled deletion if you want to keep the account and its data.",
        )
        self.assertNotIn("Your account is scheduled for deletion Your account is scheduled for deletion", row.summary or "")

    def test_smart_inbox_promotes_direct_reference_mailbox_cluster_to_exact_verified_group(self) -> None:
        mailbox = MailboxResponse(
            label="inbox",
            total_threads=1,
            loaded_threads=1,
            sections=[
                {
                    "id": "today",
                    "title": "Today",
                    "rows": [
                        _thread_row(
                            thread_id="mailbox-cluster:case-106400420",
                            message_count=2,
                            ai_group_id=None,
                            presentation_status="ai_ready",
                            children=[("case-opened", "thread-opened"), ("case-updated", "thread-updated")],
                        ).model_copy(
                            update={
                                "title": "Bank service request 106400420",
                                "summary": "Service request 106400420: 2 Bank updates.",
                                "grouping_metadata": {
                                    "source": "mailbox_display_cluster",
                                    "cluster_key": "support_case:ref:bank:ticket_id:106400420",
                                    "family": "support_case",
                                    "reference": {
                                        "provider": "bank",
                                        "signal_name": "ticket_id",
                                        "value": "106400420",
                                        "direct_message_count": 2,
                                        "message_count": 2,
                                    },
                                },
                            }
                        ),
                    ],
                }
            ],
        )

        smart = _smart_inbox_from_mailbox(mailbox)
        row = smart.sections[0].rows[0]

        self.assertEqual(row.row_type, "verified_group")
        self.assertEqual(row.confidence_tier, "exact")
        self.assertEqual(row.confidence, 0.96)
        self.assertEqual(row.grouping_reason["metadata"]["reference"]["value"], "106400420")

    def test_smart_inbox_promotes_bridged_reference_mailbox_cluster_without_exact_confidence(self) -> None:
        mailbox = MailboxResponse(
            label="inbox",
            total_threads=1,
            loaded_threads=1,
            sections=[
                {
                    "id": "today",
                    "title": "Today",
                    "rows": [
                        _thread_row(
                            thread_id="mailbox-cluster:case-bridged",
                            message_count=3,
                            ai_group_id=None,
                            presentation_status="ai_ready",
                            children=[("case-opened", "thread-opened"), ("case-auto", "thread-auto"), ("case-updated", "thread-updated")],
                        ).model_copy(
                            update={
                                "title": "Bank service request 106400420",
                                "summary": "Service request 106400420: 3 Bank updates.",
                                "grouping_metadata": {
                                    "source": "mailbox_display_cluster",
                                    "cluster_key": "support_case:ref:bank:ticket_id:106400420",
                                    "family": "support_case",
                                    "reference": {
                                        "provider": "bank",
                                        "signal_name": "ticket_id",
                                        "value": "106400420",
                                        "direct_message_count": 2,
                                        "message_count": 3,
                                    },
                                },
                            }
                        ),
                    ],
                }
            ],
        )

        smart = _smart_inbox_from_mailbox(mailbox)
        row = smart.sections[0].rows[0]

        self.assertEqual(row.row_type, "verified_group")
        self.assertEqual(row.confidence_tier, "strong")
        self.assertEqual(row.confidence, 0.9)
        self.assertEqual(row.grouping_reason["metadata"]["reference"]["direct_message_count"], 2)

    def test_smart_inbox_hides_low_signal_non_reference_mailbox_cluster(self) -> None:
        mailbox = MailboxResponse(
            label="inbox",
            total_threads=1,
            loaded_threads=1,
            sections=[
                {
                    "id": "today",
                    "title": "Today",
                    "rows": [
                        _thread_row(
                            thread_id="mailbox-cluster:newsletter",
                            message_count=3,
                            ai_group_id=None,
                            presentation_status="ai_ready",
                            children=[("news-1", "thread-news-1"), ("news-2", "thread-news-2"), ("news-3", "thread-news-3")],
                        ).model_copy(
                            update={
                                "title": "Provider updates",
                                "summary": "Latest: policy changes; also product launch and pricing notice.",
                                "grouping_metadata": {
                                    "source": "mailbox_display_cluster",
                                    "cluster_key": "newsletter:provider-stream:provider",
                                    "family": "newsletter",
                                },
                            }
                        ),
                    ],
                }
            ],
        )

        smart = _smart_inbox_from_mailbox(mailbox)

        self.assertEqual(smart.total_rows, 0)
        self.assertEqual(smart.sections, [])

    def test_reference_mailbox_display_cluster_becomes_active_conversation_work_item(self) -> None:
        row = _gmail_row_from_mailbox_display_cluster(
            "support_case:ref:northstar:ticket_id:106756996",
            [
                _gmail_message(
                    message_id="case-opened",
                    gmail_thread_id="thread-opened",
                    subject="Northstar service request 106756996 registered",
                    snippet="Service request number 106756996 has been registered.",
                    extracted_signals={
                        "sender_domain": "northstar.example",
                        "ticket_id": "106756996",
                        "normalized_subject": "service request 106756996 registered",
                    },
                    headers={},
                ),
                _gmail_message(
                    message_id="case-updated",
                    gmail_thread_id="thread-updated",
                    subject="Northstar response on service request 106756996",
                    snippet="Northstar replied on your service request 106756996.",
                    extracted_signals={
                        "sender_domain": "northstar.example",
                        "ticket_id": "106756996",
                        "normalized_subject": "response on service request 106756996",
                    },
                    headers={},
                ),
            ],
            mailbox_label="inbox",
        )

        smart = _smart_inbox_from_mailbox(
            MailboxResponse(label="inbox", total_threads=1, loaded_threads=1, sections=[{"id": "today", "title": "Today", "rows": [row]}])
        )
        queue = _smart_work_queue_from_smart_inbox(smart)

        self.assertEqual(row.sender, "Northstar Bank")
        self.assertEqual(row.title, "Northstar Bank service request 106756996 registered")
        self.assertEqual(row.priority, 48)
        self.assertEqual(smart.sections[0].rows[0].row_type, "verified_group")
        self.assertEqual(smart.sections[0].rows[0].priority, 48)
        self.assertEqual([item.smart_row_id for item in queue.active_conversations], [smart.sections[0].rows[0].id])

    def test_newsletter_mailbox_display_cluster_stays_out_of_work_queue(self) -> None:
        row = _gmail_row_from_mailbox_display_cluster(
            "newsletter:provider-stream:upwork:job-alert",
            [
                _gmail_message(
                    message_id="job-1",
                    gmail_thread_id="thread-job-1",
                    sender="Upwork <freelance-alerts@example.com>",
                    subject="Job Alert: SwiftUI project",
                    snippet="These jobs match your alert settings.",
                    extracted_signals={"sender_domain": "freelance.example", "normalized_subject": "job alert swiftui project"},
                    headers={},
                ),
                _gmail_message(
                    message_id="job-2",
                    gmail_thread_id="thread-job-2",
                    sender="Upwork <freelance-alerts@example.com>",
                    subject="Job Alert: macOS project",
                    snippet="These jobs match your alert settings.",
                    extracted_signals={"sender_domain": "freelance.example", "normalized_subject": "job alert macos project"},
                    headers={},
                ),
            ],
            mailbox_label="inbox",
        )

        smart = _smart_inbox_from_mailbox(
            MailboxResponse(label="inbox", total_threads=1, loaded_threads=1, sections=[{"id": "today", "title": "Today", "rows": [row]}])
        )
        queue = _smart_work_queue_from_smart_inbox(smart)

        self.assertEqual(row.priority, 0)
        self.assertEqual(smart.total_rows, 0)
        self.assertEqual(queue.total_open, 0)

    def test_newsletter_mailbox_display_cluster_uses_repeated_subject_provider_title(self) -> None:
        row = _gmail_row_from_mailbox_display_cluster(
            "newsletter:provider-stream:example-retailer",
            [
                _gmail_message(
                    message_id="rbi-auction",
                    gmail_thread_id="thread-rbi-auction",
                    sender="Support <support@example-retailer.example>",
                    subject="RBI Retail Direct - Auction Announcement Notification",
                    snippet="Auction announcement notification.",
                    extracted_signals={"sender_domain": "example-retailer.example", "normalized_subject": "auction announcement notification"},
                    headers={},
                ),
                _gmail_message(
                    message_id="rbi-access",
                    gmail_thread_id="thread-rbi-access",
                    sender="Support <support@example-retailer.example>",
                    subject="RBI Retail Direct - Access to NDS OM is Granted",
                    snippet="Access to NDS OM is granted.",
                    extracted_signals={"sender_domain": "example-retailer.example", "normalized_subject": "access to nds om is granted"},
                    headers={},
                ),
            ],
            mailbox_label="inbox",
        )

        self.assertEqual(row.sender, "RBI Retail Direct")
        self.assertEqual(row.title, "Auction and NDS OM access updates")
        self.assertEqual(
            row.summary,
            "Latest: Auction Announcement Notification; also Access to NDS OM is Granted.",
        )

    def test_newsletter_mailbox_display_cluster_compresses_large_bundle_title(self) -> None:
        row = _gmail_row_from_mailbox_display_cluster(
            "newsletter:provider-stream:example-retailer",
            [
                _gmail_message(
                    message_id="rbi-auction",
                    gmail_thread_id="thread-rbi-auction",
                    sender="Support <support@example-retailer.example>",
                    subject="RBI auction opportunities announced",
                    snippet="Auction opportunities announced.",
                    extracted_signals={"sender_domain": "example-retailer.example", "normalized_subject": "rbi auction opportunities announced"},
                    headers={},
                    internal_date="2026-06-12T12:00:00+00:00",
                ),
                _gmail_message(
                    message_id="rbi-auction-notice",
                    gmail_thread_id="thread-rbi-auction-notice",
                    sender="Support <support@example-retailer.example>",
                    subject="Auction notice for RBI Retail Direct",
                    snippet="Auction notice for RBI Retail Direct.",
                    extracted_signals={"sender_domain": "example-retailer.example", "normalized_subject": "auction notice for rbi retail direct"},
                    headers={},
                    internal_date="2026-06-12T11:00:00+00:00",
                ),
                _gmail_message(
                    message_id="rbi-access",
                    gmail_thread_id="thread-rbi-access",
                    sender="Support <support@example-retailer.example>",
                    subject="NDS OM access approved",
                    snippet="NDS OM access was approved.",
                    extracted_signals={"sender_domain": "example-retailer.example", "normalized_subject": "nds om access approved"},
                    headers={},
                    internal_date="2026-06-12T10:00:00+00:00",
                ),
            ],
            mailbox_label="inbox",
        )

        self.assertEqual(row.sender, "RBI Retail Direct")
        self.assertEqual(row.title, "Auction and NDS OM access updates")
        self.assertNotIn(";", row.title)
        self.assertNotIn("+", row.title)

    def test_generated_acronym_platform_bundle_titles_keep_acronym_context(self) -> None:
        acronym_cases = [
            ("NDS OM", "access", "Auction", "Auction and NDS OM access updates"),
            ("FX", "setup", "Trade", "Trade and FX setup updates"),
            ("API", "status", "Billing", "Billing and API status updates"),
        ]
        for seed in _smart_inbox_generated_seed_values():
            rng = random.Random(seed + 73)
            for index, (platform, anchor, other_topic, expected_title) in enumerate(acronym_cases):
                provider = _generated_provider(rng, index + 70)
                messages = [
                    _generated_provider_message(
                        provider,
                        message_id=f"acronym-{seed}-{index}-topic",
                        subject_topic=f"{other_topic} notice",
                        snippet=f"{other_topic} notice.",
                        labels=["INBOX", "CATEGORY_PROMOTIONS"],
                        extracted_signals={
                            "sender_domain": provider["domain"],
                            "list_id": f"{provider['root']}.updates.example",
                            "normalized_subject": f"{other_topic.lower()} notice",
                        },
                        internal_date="2026-06-12T10:00:00+00:00",
                    ),
                    _generated_provider_message(
                        provider,
                        message_id=f"acronym-{seed}-{index}-platform",
                        subject_topic=f"{platform} {anchor} approved",
                        snippet=f"{platform} {anchor} approved.",
                        labels=["INBOX", "CATEGORY_PROMOTIONS"],
                        extracted_signals={
                            "sender_domain": provider["domain"],
                            "list_id": f"{provider['root']}.updates.example",
                            "normalized_subject": f"{platform.lower()} {anchor} approved",
                        },
                        internal_date="2026-06-12T09:00:00+00:00",
                    ),
                ]

                with self.subTest(seed=seed, provider=provider["root"], platform=platform, anchor=anchor):
                    row = _gmail_row_from_mailbox_display_cluster(
                        f"newsletter:provider-stream:{provider['root']}",
                        messages,
                        mailbox_label="all",
                    )

                    self.assertEqual(row.title, expected_title)
                    self.assertNotIn(";", row.title)
                    self.assertNotIn("+", row.title)

    def test_job_alert_mailbox_display_cluster_compresses_large_bundle_title(self) -> None:
        row = _gmail_row_from_mailbox_display_cluster(
            "newsletter:provider-stream:upwork:job-alert",
            [
                _gmail_message(
                    message_id="job-1",
                    gmail_thread_id="thread-job-1",
                    sender="Upwork <freelance-alerts@example.com>",
                    subject="New job alert: AI automation engineer",
                    snippet="AI automation engineer role.",
                    extracted_signals={"sender_domain": "freelance.example", "normalized_subject": "new job alert ai automation engineer"},
                    headers={},
                    internal_date="2026-06-12T12:00:00+00:00",
                ),
                _gmail_message(
                    message_id="job-2",
                    gmail_thread_id="thread-job-2",
                    sender="Upwork <freelance-alerts@example.com>",
                    subject="Alert: AI consultant job posting",
                    snippet="AI consultant job posting.",
                    extracted_signals={"sender_domain": "freelance.example", "normalized_subject": "alert ai consultant job posting"},
                    headers={},
                    internal_date="2026-06-12T11:00:00+00:00",
                ),
                _gmail_message(
                    message_id="job-3",
                    gmail_thread_id="thread-job-3",
                    sender="Upwork <freelance-alerts@example.com>",
                    subject="New job alert: AI engineering transformation consultant",
                    snippet="AI engineering transformation consultant.",
                    extracted_signals={"sender_domain": "freelance.example", "normalized_subject": "new job alert ai engineering transformation consultant"},
                    headers={},
                    internal_date="2026-06-12T10:00:00+00:00",
                ),
            ],
            mailbox_label="inbox",
        )

        self.assertEqual(row.sender, "Upwork")
        self.assertEqual(row.title, "AI job alerts")
        self.assertNotIn(";", row.title)
        self.assertNotIn("+", row.title)

    def test_promotion_mailbox_display_cluster_uses_promotion_label(self) -> None:
        row = _gmail_row_from_mailbox_display_cluster(
            "newsletter:provider-stream:acmedeals",
            [
                _gmail_message(
                    message_id="deal-1",
                    gmail_thread_id="thread-deal-1",
                    sender="Acme Deals <offers@acmedeals.example>",
                    subject="Promotion for voucher and cashback",
                    snippet="Voucher and cashback promotion.",
                    extracted_signals={"sender_domain": "acmedeals.example", "normalized_subject": "promotion for voucher and cashback"},
                    headers={},
                    internal_date="2026-06-12T12:00:00+00:00",
                ),
                _gmail_message(
                    message_id="deal-2",
                    gmail_thread_id="thread-deal-2",
                    sender="Acme Deals <offers@acmedeals.example>",
                    subject="Get a free flight voucher",
                    snippet="Free flight voucher offer.",
                    extracted_signals={"sender_domain": "acmedeals.example", "normalized_subject": "get a free flight voucher"},
                    headers={},
                    internal_date="2026-06-12T11:00:00+00:00",
                ),
                _gmail_message(
                    message_id="deal-3",
                    gmail_thread_id="thread-deal-3",
                    sender="Acme Deals <offers@acmedeals.example>",
                    subject="Cashback reward reminder",
                    snippet="Cashback reward reminder.",
                    extracted_signals={"sender_domain": "acmedeals.example", "normalized_subject": "cashback reward reminder"},
                    headers={},
                    internal_date="2026-06-12T10:00:00+00:00",
                ),
            ],
            mailbox_label="inbox",
        )

        self.assertIn(row.sender, {"Acme Deals", "Acmedeals"})
        self.assertEqual(row.title, "Voucher and Cashback promotions")
        self.assertNotIn(";", row.title)
        self.assertNotIn("+", row.title)

    def test_newsletter_mailbox_display_cluster_prefers_repeated_ai_organization_title_over_acronym_domain(self) -> None:
        row = _gmail_row_from_mailbox_display_cluster(
            "newsletter:provider-stream:psu",
            [
                _gmail_message(
                    message_id="psu-application",
                    gmail_thread_id="thread-psu-application",
                    sender="State University Undergraduate Admissions <university-admissions@example.edu>",
                    subject="Confirming Your Application Information",
                    snippet="Review your State University application information.",
                    ai_title="State University confirms your application details",
                    extracted_signals={"sender_domain": "university.example", "normalized_subject": "confirming your application information"},
                    headers={},
                ),
                _gmail_message(
                    message_id="psu-pin",
                    gmail_thread_id="thread-psu-pin",
                    sender="academic-adviser@example.edu",
                    subject="Limited Services PIN",
                    snippet="Your limited services PIN is available.",
                    ai_title="State University limited services PIN",
                    extracted_signals={"sender_domain": "university.example", "normalized_subject": "limited services pin"},
                    headers={},
                ),
            ],
            mailbox_label="inbox",
        )

        self.assertEqual(row.sender, "State University")
        self.assertEqual(row.title, "Confirms your application details and Limited services PIN")
        self.assertEqual(
            row.summary,
            "Latest: Confirms your application details; also Limited services PIN.",
        )

    def test_newsletter_mailbox_display_cluster_summaries_use_generated_subject_examples(self) -> None:
        examples = [
            (
                "figma",
                "Figma",
                ["Design systems weekly", "New branch review workflow", "Prototype sharing changes"],
            ),
            (
                "railway",
                "Railway",
                ["Usage alert for project api", "Database backup completed", "Team invite accepted"],
            ),
            (
                "linear",
                "Linear",
                ["Triage digest for workspace", "Project milestone moved", "Issue SLA report"],
            ),
        ]
        for provider, display_name, subjects in random.sample(examples, k=len(examples)):
            with self.subTest(provider=provider):
                messages = [
                    _gmail_message(
                        message_id=f"{provider}-{index}",
                        gmail_thread_id=f"thread-{provider}-{index}",
                        sender=f"{display_name} <updates@{provider}.example>",
                        subject=f"{display_name} - {subject}",
                        snippet=subject,
                        extracted_signals={"sender_domain": f"{provider}.example", "normalized_subject": subject.lower()},
                        headers={},
                    )
                    for index, subject in enumerate(subjects, start=1)
                ]
                row = _gmail_row_from_mailbox_display_cluster(
                    f"newsletter:provider-stream:{provider}",
                    messages,
                    mailbox_label="inbox",
                )

                self.assertNotEqual(row.title, f"{display_name} updates")
                self.assertNotIn(";", row.title)
                self.assertNotIn("+", row.title)
                self.assertTrue(row.title.endswith("updates"))
                self.assertTrue(row.summary.startswith("Latest: "))
                for subject in subjects:
                    self.assertIn(subject, row.summary)
                self.assertNotIn("related", row.summary.lower())
                self.assertNotIn("update emails covering", row.summary.lower())

    def test_newsletter_mailbox_display_cluster_summaries_fall_back_from_generic_ai_titles(self) -> None:
        row = _gmail_row_from_mailbox_display_cluster(
            "newsletter:provider-stream:acmedeals",
            [
                _gmail_message(
                    message_id="acme-flight",
                    gmail_thread_id="thread-acme-flight",
                    sender="Acme Deals <offers@acmedeals.example>",
                    subject="Acme Deals - Free flight voucher with card",
                    snippet="Claim a travel voucher and cashback from Acme Deals.",
                    ai_title="Generic deposit activity update",
                    extracted_signals={"sender_domain": "acmedeals.example", "normalized_subject": "free flight voucher with card"},
                    headers={},
                ),
                _gmail_message(
                    message_id="acme-cashback",
                    gmail_thread_id="thread-acme-cashback",
                    sender="Acme Deals <offers@acmedeals.example>",
                    subject="Acme Deals - Weekend rewards",
                    snippet="Weekend rewards include a cashback voucher.",
                    ai_title="Acme Deals cashback voucher offer",
                    extracted_signals={"sender_domain": "acmedeals.example", "normalized_subject": "weekend rewards"},
                    headers={},
                ),
            ],
            mailbox_label="inbox",
        )

        self.assertEqual(row.title, "Flight and Cashback promotions")
        self.assertIn("Free flight voucher with card", row.summary)
        self.assertIn("Cashback voucher offer", row.summary)
        self.assertNotIn("Generic deposit activity update", row.summary)

    def test_newsletter_mailbox_display_cluster_compresses_two_item_mixed_event_title(self) -> None:
        row = _gmail_row_from_mailbox_display_cluster(
            "newsletter:provider-stream:hsbc",
            [
                _gmail_message(
                    message_id="hsbc-reward",
                    gmail_thread_id="thread-hsbc-reward",
                    sender="HSBC India <bank-mail@example.com>",
                    subject="HSBC SimplyPay reward promotion",
                    snippet="Reward promotion.",
                    extracted_signals={"sender_domain": "bank.example", "normalized_subject": "hsbc simplypay reward promotion"},
                    headers={},
                ),
                _gmail_message(
                    message_id="hsbc-webinar",
                    gmail_thread_id="thread-hsbc-webinar",
                    sender="HSBC India <bank-mail@example.com>",
                    subject="HSBC invitation to an emerging markets webinar",
                    snippet="Webinar invitation.",
                    extracted_signals={"sender_domain": "bank.example", "normalized_subject": "hsbc invitation to an emerging markets webinar"},
                    headers={},
                ),
            ],
            mailbox_label="inbox",
        )

        self.assertEqual(row.title, "SimplyPay and Webinar updates")
        self.assertLessEqual(len(row.title), 48)
        self.assertNotIn("invitation to an emerging markets webinar", row.title.lower())
        self.assertIn("SimplyPay reward promotion", row.summary)
        self.assertIn("Invitation to an emerging markets webinar", row.summary)

    def test_newsletter_mailbox_display_cluster_titles_event_invitation_bundle(self) -> None:
        row = _gmail_row_from_mailbox_display_cluster(
            "newsletter:provider-stream:hack2skill",
            [
                _gmail_message(
                    message_id="hackathon-isro",
                    gmail_thread_id="thread-hackathon-isro",
                    sender="Hack2Skill <events@hack2skill.com>",
                    subject="Invitation to Participate in the ISRO Bharatiya Antariksh Hackathon 2026",
                    snippet="Check out the ISRO hackathon.",
                    ai_title="Hackathon invitation from ISRO",
                    extracted_signals={"sender_domain": "hack2skill.com", "normalized_subject": "isro hackathon invitation"},
                    headers={},
                ),
                _gmail_message(
                    message_id="private-ai-event",
                    gmail_thread_id="thread-private-ai-event",
                    sender="Hack2Skill <events@hack2skill.com>",
                    subject="Join us for AMD Slingshot Grand Finale!",
                    snippet="Exclusive by-invitation access for a private AI event.",
                    ai_title="Invitation to a private AI event",
                    extracted_signals={"sender_domain": "hack2skill.com", "normalized_subject": "amd slingshot finale invitation"},
                    headers={},
                ),
            ],
            mailbox_label="inbox",
        )

        self.assertEqual(row.title, "Hackathon and AI event invitations")
        self.assertNotIn("Private", row.title)
        self.assertIn("Hackathon invitation from ISRO", row.summary)
        self.assertIn("Invitation to a private AI event", row.summary)

    def test_primary_inbox_provider_stream_does_not_merge_stale_mail(self) -> None:
        messages = [
            _gmail_message(
                message_id="provider-old",
                gmail_thread_id="thread-provider-old",
                sender="Provider <updates@provider.example>",
                subject="Provider - Account activated",
                snippet="Your account is activated.",
                extracted_signals={"sender_domain": "provider.example", "list_id": "provider.example"},
                internal_date="2022-07-27T09:00:00+00:00",
                headers={},
            ),
            _gmail_message(
                message_id="provider-recent-1",
                gmail_thread_id="thread-provider-recent-1",
                sender="Provider <updates@provider.example>",
                subject="Provider - Product update",
                snippet="Product update.",
                extracted_signals={"sender_domain": "provider.example", "list_id": "provider.example"},
                internal_date="2026-06-01T09:00:00+00:00",
                headers={},
            ),
            _gmail_message(
                message_id="provider-recent-2",
                gmail_thread_id="thread-provider-recent-2",
                sender="Provider <updates@provider.example>",
                subject="Provider - Profile setup reminder",
                snippet="Profile setup reminder.",
                extracted_signals={"sender_domain": "provider.example", "list_id": "provider.example"},
                internal_date="2026-06-03T09:00:00+00:00",
                headers={},
            ),
        ]

        rows = _mailbox_display_cluster_rows([_mailbox_display_entry(message) for message in messages], mailbox_label="inbox")
        clustered_rows = [row for row in rows if row.thread_id.startswith("mailbox-cluster:")]

        self.assertEqual(clustered_rows, [])
        self.assertEqual({row.thread_id for row in rows}, {message.gmail_thread_id for message in messages})
        self.assertTrue(all(row.grouping_metadata == {} for row in rows))

    def test_reference_cluster_provider_title_rejects_greeting_snippet(self) -> None:
        row = _gmail_row_from_mailbox_display_cluster(
            "support_case:ref:hsbc:ticket_id:2260262693",
            [
                _gmail_message(
                    message_id="hsbc-ack",
                    gmail_thread_id="thread-hsbc-ack",
                    sender="Head Retail Banking INM <retail-banking@example.com>",
                    subject="[Restricted] Your Savings account complaint",
                    snippet="Dear Mr Pandey, Complaint Reference Number 2260262693. We thank you for writing to us.",
                    ai_title="HSBC acknowledges complaint and asks for time",
                    extracted_signals={"sender_domain": "bank.example", "ticket_id": "2260262693"},
                    headers={},
                ),
                _gmail_message(
                    message_id="hsbc-followup",
                    gmail_thread_id="thread-hsbc-followup",
                    sender="Sm Response INM <bank-response@example.com>",
                    subject="[Restricted] Your HSBC Savings account complaint",
                    snippet="Dear Mr Pandey, Complaint Reference Number 2260262693. This is further to our earlier email.",
                    ai_title="HSBC follows up on branch-change request",
                    extracted_signals={"sender_domain": "bank.example", "ticket_id": "2260262693"},
                    headers={},
                ),
            ],
            mailbox_label="inbox",
        )

        self.assertEqual(row.sender, "HSBC")
        self.assertEqual(row.title, "HSBC service request 2260262693 registered")
        self.assertNotIn("Dear Mr Pandey", row.summary or "")

    def test_fx_retail_trade_id_cluster_groups_northstar_and_ccil_messages(self) -> None:
        messages = [
            _gmail_message(
                message_id="northstar-trade-confirmation",
                gmail_thread_id="thread-northstar-trade-confirmation",
                sender="NorthstarFXclearretail <northstarfx@northstar.example>",
                subject="FX Retail Deal Confirmation - CCIL Reference Number 202606029000072",
                snippet="FX Retail Trade No:202606029000072.",
                ai_title="Northstar confirms FX retail trade 202606029000072",
                extracted_signals={
                    "sender_domain": "northstar.example",
                    "trade_id": "202606029000072",
                    "normalized_subject": "fx retail deal confirmation ccil reference number 202606029000072",
                },
                headers={},
            ),
            _gmail_message(
                message_id="ccil-trade-confirmation",
                gmail_thread_id="thread-ccil-trade-confirmation",
                sender="FxNoReply@ccilindia.co.in",
                subject="FX-Retail - Trade Confirmation of TestUser | INPP29022231",
                snippet="FX-Retail Trade No. 202606029000072. Relationship Bank Northstar BANK LIMITED.",
                ai_title="FX-Retail trade confirmation for 202606029000072",
                extracted_signals={
                    "sender_domain": "ccilindia.co.in",
                    "trade_id": "202606029000072",
                    "normalized_subject": "fx-retail trade confirmation of demo user inpp29022231",
                },
                headers={},
            ),
        ]

        rows = _mailbox_display_cluster_rows([_mailbox_display_entry(message) for message in messages], mailbox_label="inbox")
        clustered_rows = [row for row in rows if row.thread_id.startswith("mailbox-cluster:")]

        self.assertEqual(len(clustered_rows), 1)
        row = clustered_rows[0]
        self.assertEqual(row.title, "FX Retail trade 202606029000072 confirmed")
        self.assertEqual({child.message_id for child in row.children}, {"northstar-trade-confirmation", "ccil-trade-confirmation"})
        self.assertEqual(row.grouping_metadata["family"], "financial_transfer")
        self.assertEqual(row.grouping_metadata["reference"]["signal_name"], "trade_id")
        self.assertEqual(row.grouping_metadata["reference"]["provider"], "fx-retail")

    def test_mailbox_rows_absorb_trade_reference_after_ai_group_rendering(self) -> None:
        northstar_group = GmailThreadRow(
            thread_id="northstar-ai-group",
            entity_id="northstar-ai-group",
            title="FX retail trade validation issue",
            href="/v1/mailbox/threads/northstar-ai-group",
            latest_source_record_id="northstar-trade-confirmation",
            latest_received_at="2026-06-02T14:20:39+05:30",
            latest_message_at="2026-06-02T14:20:39+05:30",
            latest_subject="FX Retail Deal Confirmation - CCIL Reference Number 202606029000072",
            latest_sender="NorthstarFXclearretail <northstarfx@northstar.example>",
            sender="Northstar Bank",
            participants=["Northstar Bank"],
            message_count=3,
            summary="Northstar Bank confirmed FX retail trade 202606029000072, and the related support thread shows validation trouble.",
            ai_group_id="northstar-ai-group",
            ai_title="FX retail trade validation issue",
            ai_summary="Northstar Bank confirmed FX retail trade 202606029000072, and the related support thread shows validation trouble.",
            snippet="FX Retail Trade No:202606029000072.",
            label_ids=["INBOX"],
            labels=["INBOX"],
            action_needed=False,
            action_type="open",
            action_type_key="open",
            priority=60,
            dashboard_visible=True,
            current_state="open",
            lifecycle_state="active",
            children=[
                {
                    "message_id": "northstar-trade-confirmation",
                    "gmail_thread_id": "thread-northstar-trade-confirmation",
                    "sender": "NorthstarFXclearretail <northstarfx@northstar.example>",
                    "subject": "FX Retail Deal Confirmation - CCIL Reference Number 202606029000072",
                    "snippet": "FX Retail Trade No:202606029000072.",
                    "received_at": "2026-06-02T14:20:39+05:30",
                },
                {
                    "message_id": "northstar-support-reply",
                    "gmail_thread_id": "thread-validation",
                    "sender": "Northstar Bank <care@northstar.example>",
                    "subject": "FX retail trade validation issue",
                    "snippet": "We could not validate trade 202606029000072.",
                    "received_at": "2026-06-02T14:18:39+05:30",
                },
            ],
            enrichment_status="ready",
            presentation_status="ai_ready",
        )
        ccil_row = GmailThreadRow(
            thread_id="thread-ccil-trade-confirmation",
            entity_id="thread-ccil-trade-confirmation",
            title="FX trade confirmed",
            href="/v1/mailbox/threads/thread-ccil-trade-confirmation",
            latest_source_record_id="ccil-trade-confirmation",
            latest_received_at="2026-06-02T14:19:39+05:30",
            latest_message_at="2026-06-02T14:19:39+05:30",
            latest_subject="FX-Retail - Trade Confirmation of TestUser | INPP29022231",
            latest_sender="FxNoReply@ccilindia.co.in",
            sender="CCIL India",
            participants=["CCIL India"],
            message_count=1,
            summary="A foreign exchange retail trade was executed and confirmed for trade 202606029000072.",
            ai_group_id=None,
            ai_title="FX trade confirmed",
            ai_summary="A foreign exchange retail trade was executed and confirmed for trade 202606029000072.",
            snippet="FX-Retail Trade No. 202606029000072. Relationship Bank Northstar BANK LIMITED.",
            label_ids=["INBOX"],
            labels=["INBOX"],
            action_needed=False,
            action_type="open",
            action_type_key="open",
            priority=40,
            dashboard_visible=True,
            current_state="open",
            lifecycle_state="active",
            children=[
                {
                    "message_id": "ccil-trade-confirmation",
                    "gmail_thread_id": "thread-ccil-trade-confirmation",
                    "sender": "FxNoReply@ccilindia.co.in",
                    "subject": "FX-Retail - Trade Confirmation of TestUser | INPP29022231",
                    "snippet": "FX-Retail Trade No. 202606029000072. Relationship Bank Northstar BANK LIMITED.",
                    "received_at": "2026-06-02T14:19:39+05:30",
                }
            ],
            enrichment_status="ready",
            presentation_status="ai_ready",
        )

        rows = _mailbox_rows_absorbing_trade_references([northstar_group, ccil_row], mailbox_label="inbox")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].sender, "FX Retail")
        self.assertEqual(rows[0].title, "FX Retail trade 202606029000072")
        self.assertEqual(rows[0].ai_title, "FX Retail trade 202606029000072")
        self.assertEqual(rows[0].message_count, 3)
        self.assertEqual({child.message_id for child in rows[0].children}, {"northstar-trade-confirmation", "northstar-support-reply", "ccil-trade-confirmation"})
        self.assertEqual(rows[0].grouping_metadata["reference"]["signal_name"], "trade_id")

    def test_mailbox_rows_absorb_ticket_reference_after_ai_group_rendering(self) -> None:
        northstar_group = GmailThreadRow(
            thread_id="northstar-ai-group-105715521",
            entity_id="northstar-ai-group-105715521",
            title="Northstar Bank grievance case 105715521",
            href="/v1/mailbox/threads/northstar-ai-group-105715521",
            latest_source_record_id="northstar-ticket-reply",
            latest_received_at="2026-06-05T10:00:00+05:30",
            latest_message_at="2026-06-05T10:00:00+05:30",
            latest_subject="Northstar update on service request 105715521",
            latest_sender="Support Department <grievance.redressalcc@northstar.example>",
            sender="Northstar Bank",
            participants=["Northstar Bank"],
            message_count=2,
            summary="Northstar Bank replied on grievance case 105715521.",
            ai_group_id="northstar-ai-group-105715521",
            ai_title="Northstar Bank grievance case 105715521",
            ai_summary="Northstar Bank replied on grievance case 105715521.",
            snippet="Northstar replied on case 105715521.",
            label_ids=["INBOX"],
            labels=["INBOX"],
            action_needed=False,
            action_type="review",
            action_type_key="review",
            priority=65,
            dashboard_visible=True,
            current_state="open",
            lifecycle_state="active",
            children=[
                {
                    "message_id": "northstar-ticket-ack",
                    "gmail_thread_id": "thread-ticket-ack",
                    "sender": "Support Department <grievance.redressalcc@northstar.example>",
                    "subject": "Northstar acknowledgement of service request 105715521",
                    "snippet": "Northstar acknowledged service request 105715521.",
                    "received_at": "2026-05-30T09:00:00+05:30",
                },
                {
                    "message_id": "northstar-ticket-reply",
                    "gmail_thread_id": "thread-ticket-reply",
                    "sender": "Support Department <grievance.redressalcc@northstar.example>",
                    "subject": "Northstar update on service request 105715521",
                    "snippet": "Northstar replied on service request 105715521.",
                    "received_at": "2026-06-05T10:00:00+05:30",
                },
            ],
            enrichment_status="ready",
            presentation_status="ai_ready",
        )
        northstar_followup = GmailThreadRow(
            thread_id="thread-northstar-privacy-105715521",
            entity_id="thread-northstar-privacy-105715521",
            title="Northstar credit card consent/privacy case 105715521",
            href="/v1/mailbox/threads/thread-northstar-privacy-105715521",
            latest_source_record_id="northstar-privacy-followup",
            latest_received_at="2026-05-29T11:00:00+05:30",
            latest_message_at="2026-05-29T11:00:00+05:30",
            latest_subject="Northstar credit card consent/privacy case 105715521",
            latest_sender="Northstar Bank <alerts@northstar.example>",
            sender="Northstar Bank",
            participants=["Northstar Bank"],
            message_count=1,
            summary="Northstar referenced service request 105715521 in the consent/privacy complaint.",
            ai_group_id=None,
            ai_title="Northstar credit card consent/privacy case 105715521",
            ai_summary="Northstar referenced service request 105715521 in the consent/privacy complaint.",
            snippet="Service request 105715521 is under review.",
            label_ids=["INBOX"],
            labels=["INBOX"],
            action_needed=False,
            action_type="open",
            action_type_key="open",
            priority=45,
            dashboard_visible=True,
            current_state="open",
            lifecycle_state="active",
            children=[
                {
                    "message_id": "northstar-privacy-followup",
                    "gmail_thread_id": "thread-northstar-privacy-105715521",
                    "sender": "Northstar Bank <alerts@northstar.example>",
                    "subject": "Northstar credit card consent/privacy case 105715521",
                    "snippet": "Service request 105715521 is under review.",
                    "received_at": "2026-05-29T11:00:00+05:30",
                }
            ],
            enrichment_status="ready",
            presentation_status="ai_ready",
        )

        rows = _mailbox_rows_absorbing_exact_references([northstar_group, northstar_followup], mailbox_label="inbox")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].sender, "Northstar Bank")
        self.assertEqual(rows[0].title, "Northstar Bank grievance case 105715521")
        self.assertEqual(rows[0].ai_title, "Northstar Bank grievance case 105715521")
        self.assertEqual(rows[0].message_count, 3)
        self.assertEqual({child.message_id for child in rows[0].children}, {"northstar-ticket-ack", "northstar-ticket-reply", "northstar-privacy-followup"})
        self.assertEqual(rows[0].grouping_metadata["family"], "support_case")
        self.assertEqual(rows[0].grouping_metadata["reference"]["signal_name"], "ticket_id")
        self.assertEqual(rows[0].grouping_metadata["reference"]["provider"], "northstar")

    def test_mailbox_ticket_reference_title_prefers_outcome_over_registration(self) -> None:
        conversation = _validated_thread_row(
            _thread_row(
                thread_id="northstar-remittance-conversation",
                message_count=3,
                ai_group_id="ai-northstar-remittance",
                presentation_status="ai_ready",
                children=[("wire-opened", "thread-wire"), ("wire-registered", "thread-wire"), ("wire-processed", "thread-wire")],
            ).model_copy(
                update={
                    "sender": "Northstar Bank",
                    "latest_sender": "Northstar Bank <care@northstar.example>",
                    "title": "Northstar Bank registers wire status case 106400420",
                    "ai_title": "Northstar Bank registers wire status case 106400420",
                    "summary": "Northstar Bank registered and then processed the remittance support case.",
                    "ai_summary": "Northstar Bank registered and then processed the remittance support case.",
                    "latest_subject": "Re: Request for Status of International Wire Sent 24 Hours Ago",
                    "children": [
                        {
                            "message_id": "wire-opened",
                            "gmail_thread_id": "thread-wire",
                            "sender": "Northstar Bank",
                            "subject": "Request for Status of International Wire Sent 24 Hours Ago",
                            "ai_title": "Northstar Bank auto-acknowledgment for wire status request",
                            "snippet": "The bank acknowledged the wire status request.",
                            "received_at": "2026-06-03T15:47:41+05:30",
                        },
                        {
                            "message_id": "wire-registered",
                            "gmail_thread_id": "thread-wire",
                            "sender": "Northstar Bank Care",
                            "subject": "[Registered] - Service Request 106400420",
                            "ai_title": "Northstar: service request 106400420 registered",
                            "snippet": "Service request 106400420 has been registered.",
                            "received_at": "2026-06-04T22:08:40+05:30",
                        },
                        {
                            "message_id": "wire-processed",
                            "gmail_thread_id": "thread-wire",
                            "sender": "Northstar Bank",
                            "subject": "Re: Request for Status of International Wire Sent 24 Hours Ago",
                            "ai_title": "Northstar Bank confirms outward remittance processed",
                            "snippet": "Northstar confirmed the outward remittance was processed.",
                            "received_at": "2026-06-06T17:10:57+05:30",
                        },
                    ],
                }
            )
        )
        registration = _validated_thread_row(
            _thread_row(
                thread_id="northstar-remittance-registration",
                message_count=1,
                ai_group_id=None,
                presentation_status="ai_ready",
                children=[("service-request-106400420", "thread-registration")],
            ).model_copy(
                update={
                    "sender": "Northstar Bank Care",
                    "latest_sender": "Northstar Bank Care <care@northstar.example>",
                    "title": "Northstar Bank registers remittance case 106400420",
                    "ai_title": "Northstar Bank registers remittance case 106400420",
                    "summary": "Northstar Bank registered service request 106400420.",
                    "ai_summary": "Northstar Bank registered service request 106400420.",
                    "latest_subject": "[Registered] - Service Request 106400420",
                }
            )
        )

        rows = _mailbox_rows_absorbing_exact_references([conversation, registration], mailbox_label="inbox")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "Northstar: remittance processed for case 106400420")
        self.assertEqual(rows[0].message_count, 4)

    def test_mailbox_exact_reference_absorption_counts_unique_explicit_children(self) -> None:
        primary = GmailThreadRow(
            thread_id="provider-ai-group",
            entity_id="provider-ai-group",
            title="Northwind support case 123456789",
            href="/v1/mailbox/threads/provider-ai-group",
            latest_source_record_id="case-message-b",
            latest_received_at="2026-06-05T10:00:00+05:30",
            latest_message_at="2026-06-05T10:00:00+05:30",
            latest_subject="Northwind support case 123456789",
            latest_sender="Northwind Support <support@northwind.example.com>",
            sender="Northwind Support",
            participants=["Northwind Support"],
            message_count=5,
            summary="Northwind replied on support case 123456789.",
            ai_group_id="provider-ai-group",
            ai_title="Northwind support case 123456789",
            ai_summary="Northwind replied on support case 123456789.",
            snippet="Northwind support case 123456789 was updated.",
            label_ids=["INBOX"],
            labels=["INBOX"],
            action_needed=False,
            action_type="open",
            action_type_key="open",
            priority=55,
            dashboard_visible=True,
            current_state="open",
            lifecycle_state="active",
            children=[
                {
                    "message_id": "case-message-a",
                    "gmail_thread_id": "thread-case-a",
                    "sender": "Northwind Support <support@northwind.example.com>",
                    "subject": "Northwind support case 123456789 opened",
                    "snippet": "Case 123456789 was opened.",
                    "received_at": "2026-06-04T10:00:00+05:30",
                },
                {
                    "message_id": "case-message-b",
                    "gmail_thread_id": "thread-case-b",
                    "sender": "Northwind Support <support@northwind.example.com>",
                    "subject": "Northwind support case 123456789 updated",
                    "snippet": "Case 123456789 was updated.",
                    "received_at": "2026-06-05T10:00:00+05:30",
                },
            ],
            enrichment_status="ready",
            presentation_status="ai_ready",
        )
        overlapping = GmailThreadRow.model_validate(
            {
                **primary.model_dump(mode="python"),
                "thread_id": "provider-visible-group",
                "entity_id": "provider-visible-group",
                "latest_source_record_id": "case-message-c",
                "latest_received_at": "2026-06-06T10:00:00+05:30",
                "latest_message_at": "2026-06-06T10:00:00+05:30",
                "latest_subject": "Northwind support case 123456789 follow-up",
                "message_count": 2,
                "children": [
                    {
                        "message_id": "case-message-b",
                        "gmail_thread_id": "thread-case-b",
                        "sender": "Northwind Support <support@northwind.example.com>",
                        "subject": "Northwind support case 123456789 updated",
                        "snippet": "Case 123456789 was updated.",
                        "received_at": "2026-06-05T10:00:00+05:30",
                    },
                    {
                        "message_id": "case-message-c",
                        "gmail_thread_id": "thread-case-c",
                        "sender": "Northwind Support <support@northwind.example.com>",
                        "subject": "Northwind support case 123456789 follow-up",
                        "snippet": "Case 123456789 follow-up.",
                        "received_at": "2026-06-06T10:00:00+05:30",
                    },
                ],
            }
        )

        rows = _mailbox_rows_absorbing_exact_references([primary, overlapping], mailbox_label="inbox")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].message_count, 3)
        self.assertEqual(len(rows[0].children), 3)
        self.assertEqual(rows[0].grouping_metadata["reference"]["message_count"], 3)
        self.assertEqual(rows[0].grouping_metadata["reference"]["direct_message_count"], 3)

    def test_mailbox_ticket_reference_absorption_requires_same_provider(self) -> None:
        northstar_row = GmailThreadRow(
            thread_id="thread-northstar-case",
            entity_id="thread-northstar-case",
            title="Northstar service request 123456789",
            href="/v1/mailbox/threads/thread-northstar-case",
            latest_source_record_id="northstar-case",
            latest_received_at="2026-06-05T10:00:00+05:30",
            latest_message_at="2026-06-05T10:00:00+05:30",
            latest_subject="Northstar service request 123456789",
            latest_sender="Northstar Bank <alerts@northstar.example>",
            sender="Northstar Bank",
            participants=["Northstar Bank"],
            message_count=1,
            summary="Northstar replied on service request 123456789.",
            ai_title="Northstar service request 123456789",
            ai_summary="Northstar replied on service request 123456789.",
            snippet="Service request 123456789.",
            label_ids=["INBOX"],
            labels=["INBOX"],
            action_needed=False,
            action_type="open",
            action_type_key="open",
            priority=10,
            dashboard_visible=True,
            current_state="open",
            lifecycle_state="active",
            enrichment_status="ready",
            presentation_status="ai_ready",
        )
        hsbc_row = northstar_row.model_copy(
            update={
                "thread_id": "thread-hsbc-case",
                "entity_id": "thread-hsbc-case",
                "latest_source_record_id": "hsbc-case",
                "latest_sender": "HSBC India <bank-support@example.com>",
                "sender": "HSBC India",
                "participants": ["HSBC India"],
                "title": "HSBC service request 123456789",
                "ai_title": "HSBC service request 123456789",
                "summary": "HSBC replied on service request 123456789.",
                "ai_summary": "HSBC replied on service request 123456789.",
                "snippet": "Service request 123456789.",
            }
        )

        rows = _mailbox_rows_absorbing_exact_references([northstar_row, hsbc_row], mailbox_label="inbox")

        self.assertEqual([row.thread_id for row in rows], ["thread-northstar-case", "thread-hsbc-case"])

    def test_mailbox_ticket_reference_absorption_keeps_concise_ai_title(self) -> None:
        raw_reminder = GmailThreadRow(
            thread_id="thread-aws-reminder",
            entity_id="thread-aws-reminder",
            title="Attention required on case 177954757700130: [Amazon Q] Amazon Bedrock Claude Model Access Authorization Error",
            href="/v1/mailbox/threads/thread-aws-reminder",
            latest_source_record_id="aws-reminder",
            latest_received_at="2026-06-13T02:04:11+05:30",
            latest_message_at="2026-06-13T02:04:11+05:30",
            latest_subject="Attention required on case 177954757700130: [Amazon Q] Amazon Bedrock Claude Model Access Authorization Error",
            latest_sender="Amazon Web Services <no-reply-aws@amazon.com>",
            sender="Amazon Web Services <no-reply-aws@amazon.com>",
            participants=["Amazon Web Services"],
            message_count=1,
            summary="AWS sent a final reminder on case 177954757700130.",
            ai_title="Attention required on case 177954757700130: [Amazon Q] Amazon Bedrock Claude Model Access Authorization Error",
            ai_summary="AWS sent a final reminder on case 177954757700130.",
            snippet="We have not heard back from you regarding case 177954757700130.",
            label_ids=["INBOX"],
            labels=["INBOX"],
            action_needed=False,
            action_type="open",
            action_type_key="open",
            priority=20,
            dashboard_visible=True,
            current_state="open",
            lifecycle_state="active",
            children=[
                {
                    "message_id": "aws-reminder",
                    "gmail_thread_id": "thread-aws-reminder",
                    "sender": "Amazon Web Services <no-reply-aws@amazon.com>",
                    "subject": "Attention required on case 177954757700130: [Amazon Q] Amazon Bedrock Claude Model Access Authorization Error",
                    "ai_title": "AWS final reminder for case 177954757700130",
                    "snippet": "We have not heard back from you regarding case 177954757700130.",
                    "received_at": "2026-06-13T02:04:11+05:30",
                }
            ],
            enrichment_status="ready",
            presentation_status="ai_ready",
        )
        case_updates = GmailThreadRow(
            thread_id="thread-aws-bedrock-case",
            entity_id="thread-aws-bedrock-case",
            title="AWS support case resolved",
            href="/v1/mailbox/threads/thread-aws-bedrock-case",
            latest_source_record_id="aws-bedrock-update",
            latest_received_at="2026-06-03T01:43:20+05:30",
            latest_message_at="2026-06-03T01:43:20+05:30",
            latest_subject="RE:[CASE 177954757700130] [Amazon Q] Amazon Bedrock Claude Model Access Authorization Error",
            latest_sender="Amazon Web Services <no-reply-aws@amazon.com>",
            sender="Amazon Web Services <no-reply-aws@amazon.com>",
            participants=["Amazon Web Services"],
            message_count=2,
            summary="AWS resolved the Bedrock access support case 177954757700130.",
            ai_title="AWS support case resolved",
            ai_summary="AWS resolved the Bedrock access support case 177954757700130.",
            snippet="AWS updated case 177954757700130.",
            label_ids=["INBOX"],
            labels=["INBOX"],
            action_needed=False,
            action_type="review",
            action_type_key="review",
            priority=60,
            dashboard_visible=True,
            current_state="open",
            lifecycle_state="active",
            children=[
                {
                    "message_id": "aws-bedrock-update",
                    "gmail_thread_id": "thread-aws-bedrock-case",
                    "sender": "Amazon Web Services <no-reply-aws@amazon.com>",
                    "subject": "RE:[CASE 177954757700130] [Amazon Q] Amazon Bedrock Claude Model Access Authorization Error",
                    "ai_title": "AWS updates Bedrock access case 177954757700130",
                    "snippet": "AWS reviewed the Bedrock access case.",
                    "received_at": "2026-06-03T01:43:20+05:30",
                },
                {
                    "message_id": "aws-bedrock-resolved",
                    "gmail_thread_id": "thread-aws-bedrock-case",
                    "sender": "Amazon Web Services <no-reply-aws@amazon.com>",
                    "subject": "RE:[CASE 177954757700130] [Amazon Q] Amazon Bedrock Claude Model Access Authorization Error",
                    "ai_title": "AWS says Bedrock restriction was removed",
                    "snippet": "AWS removed the Bedrock restriction.",
                    "received_at": "2026-05-24T00:40:23+05:30",
                },
            ],
            enrichment_status="ready",
            presentation_status="ai_ready",
        )

        rows = _mailbox_rows_absorbing_exact_references([raw_reminder, case_updates], mailbox_label="inbox")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "AWS Bedrock access case 177954757700130")
        self.assertEqual(rows[0].ai_title, "AWS Bedrock access case 177954757700130")
        self.assertLessEqual(len(rows[0].title or ""), 72)
        self.assertEqual(rows[0].message_count, 3)

    def test_mailbox_exact_reference_absorption_groups_booking_lifecycle(self) -> None:
        booking_ack = GmailThreadRow(
            thread_id="thread-budgetair-request",
            entity_id="thread-budgetair-request",
            title="Flight booking request acknowledged",
            href="/v1/mailbox/threads/thread-budgetair-request",
            latest_source_record_id="budgetair-request",
            latest_received_at="2023-02-15T09:00:00+05:30",
            latest_message_at="2023-02-15T09:00:00+05:30",
            latest_subject="Your BudgetAir booking request BIN-6699166",
            latest_sender='"Budgetair.com" <booking@e.budgetair.in>',
            sender='"Budgetair.com" <booking@e.budgetair.in>',
            participants=["Budgetair.com"],
            message_count=1,
            summary="BudgetAir confirmed receipt of the booking request for BIN-6699166.",
            ai_title="Flight booking request acknowledged",
            ai_summary="BudgetAir confirmed receipt of the booking request for BIN-6699166.",
            snippet="Your booking BIN-6699166 has been received.",
            label_ids=["INBOX"],
            labels=["INBOX"],
            action_needed=False,
            action_type="open",
            action_type_key="open",
            priority=20,
            dashboard_visible=True,
            current_state="open",
            lifecycle_state="active",
            enrichment_status="ready",
            presentation_status="ai_ready",
        )
        ticket = booking_ack.model_copy(
            update={
                "thread_id": "thread-budgetair-ticket",
                "entity_id": "thread-budgetair-ticket",
                "latest_source_record_id": "budgetair-ticket",
                "latest_received_at": "2023-02-16T09:00:00+05:30",
                "latest_message_at": "2023-02-16T09:00:00+05:30",
                "latest_subject": "E-ticket for booking BIN-6699166",
                "latest_sender": '"Budgetair.com" <flightchange-notification@e.budgetair.in>',
                "sender": '"Budgetair.com" <flightchange-notification@e.budgetair.in>',
                "title": "Flight e-ticket and travel details",
                "summary": "You received an e-ticket for booking BIN-6699166.",
                "ai_title": "Flight e-ticket and travel details",
                "ai_summary": "You received an e-ticket for booking BIN-6699166.",
                "snippet": "Booking BIN-6699166 e-ticket details.",
            }
        )

        rows = _mailbox_rows_absorbing_exact_references([booking_ack, ticket], mailbox_label="inbox")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "Budgetair.com booking BIN6699166")
        self.assertEqual(rows[0].message_count, 2)
        self.assertEqual(rows[0].grouping_metadata["family"], "logistics")
        self.assertEqual(rows[0].grouping_metadata["reference"]["signal_name"], "booking_id")

    def test_mailbox_exact_reference_absorption_groups_tracking_lifecycle(self) -> None:
        out_for_delivery = GmailThreadRow(
            thread_id="thread-fedex-out",
            entity_id="thread-fedex-out",
            title="Parcel out for delivery today",
            href="/v1/mailbox/threads/thread-fedex-out",
            latest_source_record_id="fedex-out",
            latest_received_at="2023-04-07T08:00:00+05:30",
            latest_message_at="2023-04-07T08:00:00+05:30",
            latest_subject="FedEx tracking 396384298158 is out for delivery",
            latest_sender="FedEx India <india@fedex.com>",
            sender="FedEx India <india@fedex.com>",
            participants=["FedEx India"],
            message_count=1,
            summary="FedEx tracking 396384298158 is out for delivery.",
            ai_title="Parcel out for delivery today",
            ai_summary="FedEx tracking 396384298158 is out for delivery.",
            snippet="Tracking number 396384298158 is out for delivery.",
            label_ids=["INBOX"],
            labels=["INBOX"],
            action_needed=False,
            action_type="track",
            action_type_key="track",
            priority=30,
            dashboard_visible=True,
            current_state="open",
            lifecycle_state="active",
            enrichment_status="ready",
            presentation_status="ai_ready",
        )
        delivered = out_for_delivery.model_copy(
            update={
                "thread_id": "thread-fedex-delivered",
                "entity_id": "thread-fedex-delivered",
                "latest_source_record_id": "fedex-delivered",
                "latest_received_at": "2023-04-07T18:00:00+05:30",
                "latest_message_at": "2023-04-07T18:00:00+05:30",
                "latest_subject": "FedEx tracking 396384298158 delivered",
                "latest_sender": '"donotreply@fedex.com" <donotreply@fedex.com>',
                "sender": '"donotreply@fedex.com" <donotreply@fedex.com>',
                "title": "FedEx package delivered",
                "summary": "FedEx tracking 396384298158 was delivered.",
                "ai_title": "FedEx package delivered",
                "ai_summary": "FedEx tracking 396384298158 was delivered.",
                "snippet": "Tracking number 396384298158 was delivered.",
            }
        )

        rows = _mailbox_rows_absorbing_exact_references([out_for_delivery, delivered], mailbox_label="inbox")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "FedEx India tracking 396384298158")
        self.assertEqual(rows[0].message_count, 2)
        self.assertEqual(rows[0].grouping_metadata["family"], "logistics")
        self.assertEqual(rows[0].grouping_metadata["reference"]["signal_name"], "tracking_id")

    def test_mailbox_exact_reference_absorption_groups_repair_lifecycle(self) -> None:
        work_authorization = GmailThreadRow(
            thread_id="thread-apple-work-auth",
            entity_id="thread-apple-work-auth",
            title="Apple Genius Bar service update",
            href="/v1/mailbox/threads/thread-apple-work-auth",
            latest_source_record_id="apple-work-auth",
            latest_received_at="2025-06-23T10:00:00+05:30",
            latest_message_at="2025-06-23T10:00:00+05:30",
            latest_subject="Your Apple Store Work Authorization",
            latest_sender="Apple Saket <apple-noreply@example.com>",
            sender="Apple",
            participants=["Apple Saket"],
            message_count=2,
            summary="Apple logged a Genius Bar work authorization for Repair No: R673869971.",
            ai_title="Apple Genius Bar service update",
            ai_summary="Apple logged a Genius Bar work authorization for Repair No: R673869971.",
            snippet="Apple Saket Tel: 0008000404503 Genius Bar Work Authorisation Repair No: R673869971.",
            label_ids=["INBOX"],
            labels=["INBOX"],
            action_needed=False,
            action_type="open",
            action_type_key="open",
            priority=30,
            dashboard_visible=True,
            current_state="open",
            lifecycle_state="active",
            enrichment_status="ready",
            presentation_status="ai_ready",
        )
        service_confirmation = work_authorization.model_copy(
            update={
                "thread_id": "thread-apple-service-confirmation",
                "entity_id": "thread-apple-service-confirmation",
                "latest_source_record_id": "apple-service-confirmation",
                "latest_received_at": "2025-06-24T10:00:00+05:30",
                "latest_message_at": "2025-06-24T10:00:00+05:30",
                "latest_sender": "Apple Saket <apple-noreply@example.com>",
                "sender": "Apple Saket <apple-noreply@example.com>",
                "participants": ["Apple Saket"],
                "title": "Apple Store service confirmation",
                "summary": "Apple sent the completed service record for Repair No: R673869971.",
                "ai_title": "Apple Store service confirmation",
                "ai_summary": "Apple sent the completed service record for Repair No: R673869971.",
                "snippet": "Apple Saket Tel: 0008000404503 Repair No: R673869971.",
                "message_count": 1,
            }
        )
        pickup = work_authorization.model_copy(
            update={
                "thread_id": "thread-apple-ready",
                "entity_id": "thread-apple-ready",
                "latest_source_record_id": "apple-ready",
                "latest_received_at": "2025-06-25T10:00:00+05:30",
                "latest_message_at": "2025-06-25T10:00:00+05:30",
                "latest_sender": "Apple Saket <apple-noreply@example.com>",
                "sender": "Apple Saket <apple-noreply@example.com>",
                "participants": ["Apple Saket"],
                "title": "Apple repair ready for pickup",
                "summary": "Apple says the product for Repair number: R673869971 is ready for pickup.",
                "ai_title": "Apple repair ready for pickup",
                "ai_summary": "Apple says the product for Repair number: R673869971 is ready for pickup.",
                "snippet": "Your product is ready for pickup. Repair number: R673869971.",
                "message_count": 1,
            }
        )

        rows = _mailbox_rows_absorbing_exact_references([work_authorization, service_confirmation, pickup], mailbox_label="inbox")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "Apple Saket repair R673869971")
        self.assertEqual(rows[0].message_count, 4)
        self.assertEqual(rows[0].grouping_metadata["family"], "support_case")
        self.assertEqual(rows[0].grouping_metadata["reference"]["signal_name"], "repair_id")
        self.assertNotIn("0008000404503", rows[0].title or "")

    def test_generated_newsletter_display_clusters_use_content_titles(self) -> None:
        for seed in _smart_inbox_generated_seed_values():
            rng = random.Random(seed)
            for index in range(6):
                provider = _generated_provider(rng, index)
                topics = rng.sample(list(SMART_INBOX_STREAM_TOPICS), k=3)
                messages = [
                    _generated_provider_message(
                        provider,
                        message_id=f"stream-{seed}-{index}-{topic_index}",
                        subject_topic=topic,
                        labels=["INBOX", "CATEGORY_PROMOTIONS"],
                        extracted_signals={
                            "sender_domain": provider["domain"],
                            "list_id": f"{provider['root']}.updates.example",
                            "normalized_subject": topic.lower(),
                        },
                        ai_title="Generic activity update" if topic_index == 0 else f"{provider['display']} {topic.lower()}",
                        internal_date=f"2026-06-{10 + topic_index:02d}T09:00:00+00:00",
                    )
                    for topic_index, topic in enumerate(topics, start=1)
                ]

                with self.subTest(seed=seed, provider=provider["root"], topics=topics):
                    row = _gmail_row_from_mailbox_display_cluster(
                        f"newsletter:provider-stream:{provider['root']}",
                        messages,
                        mailbox_label="all",
                    )

                    self.assertTrue(row.thread_id.startswith("mailbox-cluster:"))
                    self.assertEqual(row.sender, provider["display"])
                    self.assertNotEqual(row.title, f"{provider['display']} updates")
                    self.assertTrue(row.title.endswith("updates"))
                    self.assertNotIn(";", row.title)
                    self.assertNotIn("+", row.title)
                    self.assertEqual(row.message_count, len(messages))
                    self.assertNotIn("generic activity update", (row.summary or "").lower())
                    for topic in topics:
                        self.assertIn(topic, row.summary or "")

    def test_primary_inbox_keeps_provider_stream_rows_separate(self) -> None:
        messages = [
            _gmail_message(
                message_id="provider-product",
                gmail_thread_id="thread-provider-product",
                sender="Provider <updates@provider.example>",
                subject="Provider - Product update",
                snippet="Product update.",
                label_ids=["INBOX", "CATEGORY_PROMOTIONS"],
                extracted_signals={"sender_domain": "provider.example", "list_id": "provider.example"},
                internal_date="2026-06-12T10:00:00+00:00",
                headers={},
            ),
            _gmail_message(
                message_id="provider-profile",
                gmail_thread_id="thread-provider-profile",
                sender="Provider <updates@provider.example>",
                subject="Provider - Profile setup reminder",
                snippet="Profile setup reminder.",
                label_ids=["INBOX", "CATEGORY_PROMOTIONS"],
                extracted_signals={"sender_domain": "provider.example", "list_id": "provider.example"},
                internal_date="2026-06-12T09:00:00+00:00",
                headers={},
            ),
        ]

        rows = _mailbox_display_cluster_rows([_mailbox_display_entry(message) for message in messages], mailbox_label="inbox")

        self.assertEqual({row.thread_id for row in rows}, {message.gmail_thread_id for message in messages})
        self.assertTrue(all(not row.thread_id.startswith("mailbox-cluster:") for row in rows))
        self.assertTrue(all(row.grouping_metadata == {} for row in rows))

    def test_generated_reference_display_clusters_do_not_merge_distinct_references(self) -> None:
        for seed in _smart_inbox_generated_seed_values():
            rng = random.Random(seed + 17)
            for index in range(6):
                provider = _generated_provider(rng, index + 20)
                signal_name, family, label = rng.choice(SMART_INBOX_REFERENCE_CASES)
                references = [_generated_reference_value(rng, index, offset) for offset in range(2)]
                messages: list[GmailMessageRecord] = []
                expected_groups: list[set[str]] = []
                for reference_index, reference in enumerate(references):
                    group_message_ids: set[str] = set()
                    for update_index, verb in enumerate(["registered", "updated"], start=1):
                        message_id = f"ref-{seed}-{index}-{reference_index}-{update_index}"
                        group_message_ids.add(message_id)
                        messages.append(
                            _generated_provider_message(
                                provider,
                                message_id=message_id,
                                subject_topic=f"{label.title()} {reference} {verb}",
                                snippet=f"{provider['display']} {label} {reference} was {verb}.",
                                extracted_signals={"sender_domain": provider["domain"], signal_name: reference},
                                internal_date=f"2026-06-{12 + reference_index:02d}T0{update_index}:00:00+00:00",
                            )
                        )
                    expected_groups.append(group_message_ids)
                normal = _generated_provider_message(
                    provider,
                    message_id=f"ref-{seed}-{index}-normal",
                    subject_topic="General profile update",
                    snippet="A general profile update without a durable reference.",
                    extracted_signals={"sender_domain": provider["domain"], "normalized_subject": "general profile update"},
                    internal_date="2026-06-15T09:00:00+00:00",
                )

                with self.subTest(seed=seed, provider=provider["root"], family=family, signal_name=signal_name):
                    entries = [_mailbox_display_entry(message) for message in [*messages, normal]]
                    rows = _mailbox_display_cluster_rows(entries, mailbox_label="inbox")
                    clustered_rows = [row for row in rows if row.thread_id.startswith("mailbox-cluster:")]
                    raw_rows = [row for row in rows if not row.thread_id.startswith("mailbox-cluster:")]
                    actual_groups = [set(child.message_id for child in row.children) for row in clustered_rows]

                    self.assertEqual(len(clustered_rows), 2)
                    self.assertEqual(len(raw_rows), 1)
                    self.assertEqual(raw_rows[0].thread_id, normal.gmail_thread_id)
                    self.assertEqual({frozenset(group) for group in actual_groups}, {frozenset(group) for group in expected_groups})
                    self.assertTrue(all(row.grouping_metadata.get("family") == family for row in clustered_rows))
                    for row in clustered_rows:
                        summary = row.summary or ""
                        self.assertRegex(summary, r"^[A-Z][^:]+: 2 .+ updates; latest: .+\.$")
                        self.assertNotIn("related", summary.lower())
                        self.assertNotIn("emails around", summary.lower())
                        self.assertLessEqual(len(summary), 160)

    def test_generated_action_required_job_alerts_remain_separate_rows(self) -> None:
        for seed in _smart_inbox_generated_seed_values():
            rng = random.Random(seed + 31)
            for index in range(6):
                provider = _generated_provider(rng, index + 40)
                roles = rng.sample(["AI engineer", "Automation consultant", "Data analyst", "ML reviewer"], k=2)
                messages = [
                    _generated_provider_message(
                        provider,
                        message_id=f"job-{seed}-{index}-{role_index}",
                        subject_topic=f"New job alert: {role}",
                        snippet=f"This job matches your alert settings. Please reply to confirm availability for {role}.",
                        labels=["INBOX"],
                        extracted_signals={"sender_domain": provider["domain"], "normalized_subject": f"new job alert {role.lower()}"},
                        internal_date=f"2026-06-1{role_index}T09:00:00+00:00",
                    )
                    for role_index, role in enumerate(roles, start=1)
                ]

                with self.subTest(seed=seed, provider=provider["root"], roles=roles):
                    entries = [_mailbox_display_entry(message) for message in messages]
                    rows = _mailbox_display_cluster_rows(entries, mailbox_label="inbox")

                    self.assertEqual({row.thread_id for row in rows}, {message.gmail_thread_id for message in messages})
                    self.assertTrue(all(not row.thread_id.startswith("mailbox-cluster:") for row in rows))
                    self.assertTrue(all(row.grouping_metadata == {} for row in rows))

    def test_newsletter_mailbox_display_cluster_humanizes_compact_provider_domains(self) -> None:
        general_intelligence_row = _gmail_row_from_mailbox_display_cluster(
            "newsletter:provider-stream:generalintelligencecompany",
            [
                _gmail_message(
                    message_id="gic-fellowship",
                    gmail_thread_id="thread-gic-fellowship",
                    sender="Andrew Pignanelli <fellowship@generalintelligencecompany.com>",
                    subject="Decision: The General Intelligence Fellowship",
                    snippet="Fellowship decision update.",
                    extracted_signals={"sender_domain": "generalintelligencecompany.com", "normalized_subject": "decision the general intelligence fellowship"},
                    headers={},
                ),
                _gmail_message(
                    message_id="gic-update",
                    gmail_thread_id="thread-gic-update",
                    sender="Andrew Pignanelli <andrew@generalintelligencecompany.com>",
                    subject="Cofounder Updates - Marketing Department V2",
                    snippet="Company update.",
                    extracted_signals={"sender_domain": "generalintelligencecompany.com", "normalized_subject": "cofounder updates marketing department v2"},
                    headers={},
                ),
            ],
            mailbox_label="inbox",
        )
        github_row = _gmail_row_from_mailbox_display_cluster(
            "newsletter:provider-stream:github",
            [
                _gmail_message(
                    message_id="github-education",
                    gmail_thread_id="thread-github-education",
                    sender="GitHub Education <edu-github-noreply@example.com>",
                    subject="[GitHub Education] Benefits update",
                    snippet="GitHub Education update.",
                    extracted_signals={"sender_domain": "github.com", "normalized_subject": "github education benefits update"},
                    headers={},
                ),
                _gmail_message(
                    message_id="github-sponsors",
                    gmail_thread_id="thread-github-sponsors",
                    sender="Github",
                    subject="Finish setting up your GitHub Sponsors Profile",
                    snippet="GitHub Sponsors setup reminder.",
                    extracted_signals={"sender_domain": "github.com", "normalized_subject": "finish setting up your github sponsors profile"},
                    headers={},
                ),
            ],
            mailbox_label="inbox",
        )

        self.assertEqual(general_intelligence_row.sender, "General Intelligence Company")
        self.assertNotEqual(general_intelligence_row.title, "General Intelligence Company updates")
        self.assertIn("Fellowship", general_intelligence_row.title)
        self.assertEqual(github_row.sender, "GitHub")
        self.assertNotEqual(github_row.title, "GitHub updates")
        self.assertIn("Sponsors", github_row.title)

    def test_provider_stream_does_not_merge_github_operational_updates(self) -> None:
        messages = [
            _gmail_message(
                message_id="github-education",
                gmail_thread_id="thread-github-education",
                sender="GitHub Education <edu-github-noreply@example.com>",
                subject="[GitHub Education] @demo-user",
                snippet="Your request to join GitHub Education has been approved.",
                ai_title="GitHub Education approval notice",
                label_ids=["INBOX", "CATEGORY_UPDATES"],
                extracted_signals={"sender_domain": "github.com", "normalized_subject": "github education approved"},
                headers={},
            ),
            _gmail_message(
                message_id="github-sponsors",
                gmail_thread_id="thread-github-sponsors",
                sender="GitHub <github-noreply@example.com>",
                subject="Finish setting up your GitHub Sponsors Profile",
                snippet="Do not forget to finish setting up your GitHub Sponsors profile.",
                ai_title="Complete GitHub Sponsors profile setup",
                label_ids=["INBOX", "CATEGORY_UPDATES"],
                extracted_signals={"sender_domain": "github.com", "normalized_subject": "finish setting up github sponsors profile"},
                headers={},
            ),
            _gmail_message(
                message_id="github-workflow",
                gmail_thread_id="thread-github-workflow",
                sender="gaurav-archive <github-notifications@example.com>",
                subject="[gaurav-archive/site] Run succeeded: Update Browserslist database - main",
                snippet="The GitHub workflow run completed successfully.",
                ai_title="Browserslist update completed successfully",
                label_ids=["INBOX", "CATEGORY_UPDATES"],
                extracted_signals={"sender_domain": "github.com", "normalized_subject": "run succeeded update browserslist database"},
                headers={},
            ),
        ]

        rows = _mailbox_display_cluster_rows([_mailbox_display_entry(message) for message in messages], mailbox_label="inbox")

        self.assertEqual({row.thread_id for row in rows}, {message.gmail_thread_id for message in messages})
        self.assertTrue(all(not row.thread_id.startswith("mailbox-cluster:") for row in rows))
        self.assertTrue(all(row.grouping_metadata == {} for row in rows))

    def test_generic_inbox_sender_falls_back_to_domain_owner_title(self) -> None:
        northstar_bank_message = _gmail_message(
            sender="Support <support@northstar.example>",
            subject="Northstar Bank international wire N26153141140349",
            snippet="Your Northstar Bank international wire has been processed.",
            extracted_signals={"sender_domain": "northstar.example", "normalized_subject": "international wire n26153141140349"},
            headers={},
        )
        compressed_northstar_message = _gmail_message(
            message_id="northstar-compressed",
            gmail_thread_id="thread-northstar-compressed",
            sender="TradeQualityUnit@northstarbank.example",
            subject="Outward remittance processed",
            snippet="Northstar Bank says an outward remittance was processed.",
            extracted_signals={"sender_domain": "northstarbank.example", "normalized_subject": "outward remittance processed"},
            headers={},
        )
        compressed_rbi_message = _gmail_message(
            message_id="rbi-compressed",
            gmail_thread_id="thread-rbi-compressed",
            sender="support@example-retailer.example",
            subject="RBI Retail Direct account approved",
            snippet="RBI Retail Direct account opening request was processed.",
            extracted_signals={"sender_domain": "example-retailer.example", "normalized_subject": "rbi retail direct account approved"},
            headers={},
        )
        default_user_message = _gmail_message(
            message_id="northstar-default-user",
            gmail_thread_id="thread-northstar-default-user",
            sender="Default User <care@northstarbank.example>",
            subject="Northstar Bank service request resolved",
            snippet="Northstar Bank marked the service request resolved.",
            extracted_signals={"sender_domain": "northstarbank.example", "normalized_subject": "service request resolved"},
            headers={},
        )
        compressed_display_message = _gmail_message(
            message_id="northstar-fx-compressed",
            gmail_thread_id="thread-northstar-fx-compressed",
            sender="NorthstarFXclearretail <northstarfx@northstar.example>",
            subject="FX retail trade validation issue",
            snippet="Northstar Bank confirmed the FX retail trade.",
            extracted_signals={"sender_domain": "northstar.example", "normalized_subject": "fx retail trade validation issue"},
            headers={},
        )
        compressed_india_domain_message = _gmail_message(
            message_id="ccil-compressed",
            gmail_thread_id="thread-ccil-compressed",
            sender="FxNoReply@ccilindia.co.in",
            subject="FX-Retail - Trading Limit Notification",
            snippet="FX-Retail sent a trading limit notification.",
            extracted_signals={"sender_domain": "ccilindia.co.in", "normalized_subject": "fx retail trading limit notification"},
            headers={},
        )
        service_channel_message = _gmail_message(
            message_id="northstar-grievance",
            gmail_thread_id="thread-northstar-grievance",
            sender="Support Department <grievance.redressalcc@northstar.example>",
            subject="Northstar Bank grievance case 105715521",
            snippet="Northstar Bank replied on the grievance case.",
            extracted_signals={"sender_domain": "northstar.example", "normalized_subject": "northstar bank grievance case 105715521"},
            headers={},
        )
        aws_service_message = _gmail_message(
            message_id="aws-cost-alert",
            gmail_thread_id="thread-aws-cost-alert",
            sender="anomalydetection@costalerts.amazonaws.com",
            subject="Getting started with AWS Cost Anomaly Detection",
            snippet="AWS Cost Anomaly Detection was enabled.",
            extracted_signals={"sender_domain": "costalerts.amazonaws.com", "normalized_subject": "getting started with aws cost anomaly detection"},
            headers={},
        )
        cal_state_apply_message = _gmail_message(
            message_id="calstateapply",
            gmail_thread_id="thread-calstateapply",
            sender="support@calstateapply.myliaison.com",
            subject="Welcome to the Cal State Apply application",
            snippet="Cal State Apply welcome message.",
            extracted_signals={"sender_domain": "calstateapply.myliaison.com", "normalized_subject": "welcome to the cal state apply application"},
            headers={},
        )
        ccc_mypath_message = _gmail_message(
            message_id="cccmypath",
            gmail_thread_id="thread-cccmypath",
            sender="no-reply@cccmypath.org",
            subject="Congratulations - your CCC account was successfully created!",
            snippet="CCC account created.",
            extracted_signals={"sender_domain": "cccmypath.org", "normalized_subject": "ccc account created"},
            headers={},
        )
        id_me_message = _gmail_message(
            message_id="id-me",
            gmail_thread_id="thread-id-me",
            sender='"ID.me" <hello@id.me>',
            subject="Action Required: Continue verifying your identity",
            snippet="ID.me says identity verification still needs to be completed.",
            extracted_signals={"sender_domain": "id.me", "normalized_subject": "action required continue verifying your identity"},
            headers={},
        )
        psu_message = _gmail_message(
            message_id="psu-next-steps",
            gmail_thread_id="thread-psu-next-steps",
            sender="international-office@example.edu",
            subject="Visa document next steps",
            snippet="State University needs visa document information.",
            ai_title="State University visa document next steps",
            extracted_signals={"sender_domain": "university.example", "normalized_subject": "visa document next steps"},
            headers={},
        )
        neo_greeting_message = _gmail_message(
            message_id="neo-reference",
            gmail_thread_id="thread-neo-reference",
            sender="Neo <neo-noreply@example.com>",
            subject="Reference received from Shivay Lamba",
            snippet="Hi TestUser, Shivay Lamba has submitted their reference for your Neo Residency application.",
            ai_title="Reference submitted for the residency application",
            extracted_signals={"sender_domain": "neo.com", "normalized_subject": "reference received"},
            headers={},
        )
        hack_club_greeting_message = _gmail_message(
            message_id="hackclub-donation",
            gmail_thread_id="thread-hackclub-donation",
            sender="HCB <hcb@hackclub.com>",
            subject="Receipt for your donation to Hackaccino",
            snippet="Hi TestUser, thank you for your generous donation to Hackaccino.",
            ai_title="Donation receipt for Hackaccino",
            extracted_signals={"sender_domain": "hackclub.com", "normalized_subject": "donation receipt"},
            headers={},
        )
        ibkr_fyi_message = _gmail_message(
            message_id="ibkr-wire",
            gmail_thread_id="thread-ibkr-wire",
            sender="IBKR FYI <brokerage-noreply@example.com>",
            subject="Deposit Activity Update",
            snippet="Interactive Brokers says a USD wire deposit was received.",
            ai_title="IBKR deposit received",
            extracted_signals={"sender_domain": "brokerage.example", "normalized_subject": "deposit activity update"},
            headers={},
        )

        northstar_bank_row = _gmail_row_from_canonical_thread("northstar-wire", [northstar_bank_message], "inbox", None)
        compressed_northstar_row = _gmail_row_from_canonical_thread("northstar-compressed", [compressed_northstar_message], "inbox", None)
        compressed_rbi_row = _gmail_row_from_canonical_thread("rbi-compressed", [compressed_rbi_message], "inbox", None)
        default_user_row = _gmail_row_from_canonical_thread("northstar-default-user", [default_user_message], "inbox", None)
        compressed_display_row = _gmail_row_from_canonical_thread("northstar-fx-compressed", [compressed_display_message], "inbox", None)
        compressed_india_domain_row = _gmail_row_from_canonical_thread("ccil-compressed", [compressed_india_domain_message], "inbox", None)
        service_channel_row = _gmail_row_from_canonical_thread("northstar-grievance", [service_channel_message], "inbox", None)
        aws_service_row = _gmail_row_from_canonical_thread("aws-cost-alert", [aws_service_message], "inbox", None)
        cal_state_apply_row = _gmail_row_from_canonical_thread("calstateapply", [cal_state_apply_message], "inbox", None)
        ccc_mypath_row = _gmail_row_from_canonical_thread("cccmypath", [ccc_mypath_message], "inbox", None)
        id_me_row = _gmail_row_from_canonical_thread("id-me", [id_me_message], "inbox", None)
        psu_row = _gmail_row_from_canonical_thread("psu-next-steps", [psu_message], "inbox", None)
        neo_greeting_row = _gmail_row_from_canonical_thread("neo-reference", [neo_greeting_message], "inbox", None)
        hack_club_greeting_row = _gmail_row_from_canonical_thread("hackclub-donation", [hack_club_greeting_message], "inbox", None)
        ibkr_fyi_row = _gmail_row_from_canonical_thread("ibkr-wire", [ibkr_fyi_message], "inbox", None)

        self.assertEqual(northstar_bank_row.sender, "Northstar Bank")
        self.assertEqual(compressed_northstar_row.sender, "Northstar Bank")
        self.assertEqual(compressed_rbi_row.sender, "RBI Retail Direct")
        self.assertEqual(default_user_row.sender, "Northstar Bank")
        self.assertEqual(compressed_display_row.sender, "Northstar Bank")
        self.assertEqual(compressed_india_domain_row.sender, "CCIL India")
        self.assertEqual(service_channel_row.sender, "Northstar Bank")
        self.assertEqual(aws_service_row.sender, "AWS")
        self.assertEqual(cal_state_apply_row.sender, "Cal State Apply")
        self.assertEqual(ccc_mypath_row.sender, "CCC MyPath")
        self.assertEqual(id_me_row.sender, "ID.me")
        self.assertEqual(psu_row.sender, "State University")
        self.assertEqual(neo_greeting_row.sender, "Neo")
        self.assertEqual(hack_club_greeting_row.sender, "Hack Club")
        self.assertEqual(ibkr_fyi_row.sender, "Interactive Brokers")

    def test_inbox_thread_sender_prefers_latest_inbound_when_latest_message_is_sent(self) -> None:
        inbound = _gmail_message(
            message_id="northstar-inbound",
            gmail_thread_id="thread-northstar-card",
            sender="Support Department <grievance.redressalcc@northstar.example>",
            subject="Re: Credit Limit Increase & Card Upgrade Request",
            snippet="Northstar Bank asks for income tax return documents.",
            ai_title="Bank requests income tax return documents",
            label_ids=["INBOX"],
            internal_date="2026-04-23T23:58:10+05:30",
            extracted_signals={"sender_domain": "northstar.example", "normalized_subject": "credit limit card upgrade request"},
            headers={},
        )
        sent_reply = _gmail_message(
            message_id="northstar-sent-reply",
            gmail_thread_id="thread-northstar-card",
            sender="TestUser <owner@example.test>",
            subject="Re: Credit Limit Increase & Card Upgrade Request",
            snippet="PFA I have only last one year of ITR available.",
            ai_title="Update on income documents for card request",
            label_ids=["SENT", "INBOX"],
            internal_date="2026-04-25T00:37:30+05:30",
            extracted_signals={"sender_domain": "pandey.family", "normalized_subject": "credit limit card upgrade request"},
            headers={},
        )

        row = _gmail_row_from_canonical_thread("thread-northstar-card", [inbound, sent_reply], "inbox", None)

        self.assertEqual(row.sender, "Northstar Bank")
        self.assertEqual(row.latest_sender, "TestUser <owner@example.test>")
        self.assertEqual(row.children[-1].sender, "TestUser")

    def test_inbox_sent_only_row_uses_recipient_as_sender(self) -> None:
        sent_message = _gmail_message(
            message_id="northstar-sent-only",
            gmail_thread_id="thread-northstar-sent-only",
            sender="TestUser <owner@example.test>",
            subject="Credit Limit Increase & Card Upgrade Request",
            snippet="I would like to request a credit limit increase.",
            ai_title="Credit limit increase and card upgrade request",
            label_ids=["SENT", "INBOX"],
            internal_date="2026-04-03T17:37:58+05:30",
            extracted_signals={"sender_domain": "pandey.family", "normalized_subject": "credit limit increase card upgrade request"},
            headers={"to": "priorityredressal.creditcards@northstar.example"},
            recipients={"to": "priorityredressal.creditcards@northstar.example"},
        )

        row = _gmail_row_from_canonical_thread("thread-northstar-sent-only", [sent_message], "inbox", None)

        self.assertEqual(row.sender, "Northstar Bank")
        self.assertEqual(row.latest_sender, "TestUser <owner@example.test>")
        self.assertEqual(row.children[0].sender, "TestUser")

    def test_smart_inbox_does_not_cap_loaded_hot_window_threads(self) -> None:
        row_count = SMART_INBOX_DISPLAY_THREAD_LIMIT + 5
        mailbox = MailboxResponse(
            label="inbox",
            total_threads=row_count,
            loaded_threads=row_count,
            sections=[
                {
                    "id": "today",
                    "title": "Today",
                    "rows": [
                        _thread_row(
                            thread_id=f"thread-{index}",
                            message_count=1,
                            ai_group_id=f"group-{index}",
                            presentation_status="ai_ready",
                            children=[],
                        )
                        for index in range(row_count)
                    ],
                }
            ],
        )

        smart = _smart_inbox_from_mailbox(mailbox)

        self.assertEqual(smart.total_rows, row_count)
        self.assertEqual(sum(len(section.rows) for section in smart.sections), row_count)
        self.assertEqual(smart.sections[0].rows[0].row_key, "thread-0")
        self.assertEqual(smart.sections[0].rows[-1].row_key, f"thread-{row_count - 1}")

    def test_smart_inbox_does_not_cap_loaded_hot_window_source_messages(self) -> None:
        message_count = 6
        row_count = SMART_INBOX_DISPLAY_THREAD_LIMIT
        mailbox = MailboxResponse(
            label="inbox",
            total_threads=row_count,
            loaded_threads=row_count,
            sections=[
                {
                    "id": "today",
                    "title": "Today",
                    "rows": [
                        _thread_row(
                            thread_id=f"thread-{index}",
                            message_count=message_count,
                            ai_group_id=f"group-{index}",
                            presentation_status="ai_ready",
                            children=[],
                        )
                        for index in range(row_count)
                    ],
                }
            ],
        )

        smart = _smart_inbox_from_mailbox(mailbox)

        self.assertEqual(smart.total_rows, row_count)
        self.assertEqual(
            sum(int(row.grouping_reason["message_count"]) for section in smart.sections for row in section.rows),
            row_count * message_count,
        )

    def test_smart_inbox_hot_window_excludes_old_history_rows(self) -> None:
        current = _thread_row(
            thread_id="thread-current",
            message_count=1,
            ai_group_id="group-current",
            presentation_status="ai_ready",
            children=[],
        )
        old = _thread_row(
            thread_id="thread-old",
            message_count=1,
            ai_group_id="group-old",
            presentation_status="ai_ready",
            children=[],
        ).model_copy(
            update={
                "latest_message_at": "2020-01-01T10:00:00+00:00",
                "latest_received_at": "2020-01-01T10:00:00+00:00",
            }
        )
        mailbox = MailboxResponse(
            label="inbox",
            total_threads=2,
            loaded_threads=2,
            sections=[{"id": "today", "title": "Today", "rows": [current, old]}],
        )

        smart = _smart_inbox_from_mailbox(mailbox)

        self.assertEqual(smart.total_rows, 1)
        self.assertEqual([row.row_key for section in smart.sections for row in section.rows], ["thread-current"])
        self.assertEqual(smart.hot_window_days, SMART_HOT_WINDOW_DAYS)

    @patch("app.services.mail_groups.list_mail_object_bundles")
    def test_mail_object_bundle_replaces_duplicate_mailbox_rows(self, mock_bundles: Mock) -> None:
        mailbox = MailboxResponse(
            label="inbox",
            total_threads=3,
            loaded_threads=3,
            sections=[
                {
                    "id": "today",
                    "title": "Today",
                    "rows": [
                        _thread_row(thread_id="thread-1", message_count=1, ai_group_id=None, presentation_status="fallback", children=[]),
                        _thread_row(thread_id="thread-2", message_count=1, ai_group_id=None, presentation_status="fallback", children=[]),
                        _thread_row(thread_id="thread-3", message_count=1, ai_group_id=None, presentation_status="fallback", children=[]),
                    ],
                }
            ],
        )
        mock_bundles.return_value = [
            MailObjectBundle(
                object=MailObjectRecord(
                    id="object-1",
                    user_id="user-1",
                    object_type="ticket",
                    canonical_key="ticket_id:provider:106756996",
                    title="Provider ticket 106756996",
                    summary="Mail related to Provider ticket 106756996.",
                    lifecycle_state="active",
                    confidence=1.0,
                    evidence={"signal_name": "ticket_id"},
                    created_at="2026-06-11T10:00:00+00:00",
                    updated_at="2026-06-11T10:00:00+00:00",
                ),
                messages=[
                    _gmail_message(message_id="latest-thread-1", gmail_thread_id="thread-1", extracted_signals={"sender_domain": "support.provider.example.com", "ticket_id": "106756996"}, headers={}),
                    _gmail_message(message_id="latest-thread-2", gmail_thread_id="thread-2", extracted_signals={"sender_domain": "mail.provider.example.com", "ticket_id": "106756996"}, headers={}),
                ],
            )
        ]

        smart = _smart_inbox_from_mailbox_and_objects("postgresql://example/db", user_id="user-1", mailbox=mailbox)
        rows = [row for section in smart.sections for row in section.rows]

        self.assertEqual(smart.total_rows, 2)
        self.assertEqual(rows[0].row_type, "verified_group")
        self.assertEqual(rows[0].row_key, "mail-object:ticket_id:provider:106756996")
        self.assertEqual(rows[0].reader_thread_id, "smart-row:smart-object:object-1")
        self.assertEqual(rows[0].source_thread_ids, ["thread-1", "thread-2"])
        self.assertEqual(rows[0].source_message_ids, ["latest-thread-1", "latest-thread-2"])
        self.assertEqual(rows[0].grouping_reason["source"], "mail_object")
        self.assertEqual([row.row_key for row in rows[1:]], ["thread-3"])

    @patch("app.services.mail_groups.list_mail_object_bundles")
    def test_mail_object_bundle_prefers_specific_sender_over_default_user(self, mock_bundles: Mock) -> None:
        mailbox = MailboxResponse(label="inbox", total_threads=0, loaded_threads=0, sections=[])
        mock_bundles.return_value = [
            MailObjectBundle(
                object=MailObjectRecord(
                    id="object-105991218",
                    user_id="user-1",
                    object_type="ticket",
                    canonical_key="ticket_id:northstarbank:105991218",
                    title="Northstar Bank service request resolved",
                    summary="Northstar Bank registered and resolved service request 105991218.",
                    lifecycle_state="active",
                    confidence=1.0,
                    evidence={"signal_name": "ticket_id"},
                    created_at="2026-06-11T10:00:00+00:00",
                    updated_at="2026-06-11T10:00:00+00:00",
                ),
                messages=[
                    _gmail_message(
                        message_id="resolved",
                        gmail_thread_id="thread-resolved",
                        sender="Default User <care@northstarbank.example>",
                        extracted_signals={"sender_domain": "northstarbank.example", "ticket_id": "105991218"},
                        headers={},
                    ),
                    _gmail_message(
                        message_id="registered",
                        gmail_thread_id="thread-registered",
                        sender="Northstar Bank Care <care@northstarbank.example>",
                        extracted_signals={"sender_domain": "northstarbank.example", "ticket_id": "105991218"},
                        headers={},
                    ),
                ],
            )
        ]

        smart = _smart_inbox_from_mailbox_and_objects("postgresql://example/db", user_id="user-1", mailbox=mailbox)
        rows = [row for section in smart.sections for row in section.rows]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].primary_sender, "Northstar Bank Care")

    @patch("app.services.mail_groups.list_mail_object_bundles")
    def test_trade_mail_object_absorbs_overlapping_validation_group_with_fx_sender(self, mock_bundles: Mock) -> None:
        mailbox = MailboxResponse(
            label="inbox",
            total_threads=1,
            loaded_threads=1,
            sections=[
                {
                    "id": "today",
                    "title": "Today",
                    "rows": [
                        GmailThreadRow(
                            thread_id="thread-validation",
                            entity_id="group-validation",
                            title="FX retail trade validation issue",
                            href="/v1/mailbox/threads/thread-validation",
                            latest_source_record_id="northstar-trade-confirmation",
                            latest_received_at="2026-06-11T10:00:00+00:00",
                            latest_message_at="2026-06-11T10:00:00+00:00",
                            latest_subject="FX retail trade validation issue",
                            latest_sender="Northstar Bank <care@northstar.example>",
                            sender="Northstar Bank",
                            participants=["Northstar Bank"],
                            message_count=3,
                            summary="Northstar support said the FX retail trade could not be validated in netbanking.",
                            ai_group_id="group-validation",
                            ai_title="FX retail trade validation issue",
                            ai_summary="Northstar support said the FX retail trade could not be validated in netbanking.",
                            snippet="Trade validation support conversation.",
                            label_ids=["INBOX"],
                            labels=["INBOX"],
                            action_needed=False,
                            action_type="open",
                            action_type_key="open",
                            priority=60,
                            dashboard_visible=True,
                            current_state="open",
                            lifecycle_state="active",
                            children=[
                                {
                                    "message_id": "northstar-trade-confirmation",
                                    "gmail_thread_id": "thread-northstar-trade-confirmation",
                                    "sender": "NorthstarFXclearretail <northstarfx@northstar.example>",
                                    "subject": "FX Retail Deal Confirmation - CCIL Reference Number 202606029000072",
                                    "snippet": "FX Retail Trade No:202606029000072.",
                                    "received_at": "2026-06-11T10:00:00+00:00",
                                },
                                {
                                    "message_id": "northstar-support-reply",
                                    "gmail_thread_id": "thread-validation",
                                    "sender": "Northstar Bank <care@northstar.example>",
                                    "subject": "FX retail trade validation issue",
                                    "snippet": "We could not validate trade 202606029000072.",
                                    "received_at": "2026-06-11T10:00:00+00:00",
                                },
                            ],
                            enrichment_status="ready",
                            presentation_status="ai_ready",
                        )
                    ],
                }
            ],
        )
        mock_bundles.return_value = [
            MailObjectBundle(
                object=MailObjectRecord(
                    id="object-trade-202606029000072",
                    user_id="user-1",
                    object_type="trade",
                    canonical_key="trade_id:fx-retail:202606029000072",
                    title="FX Retail trade 202606029000072",
                    summary="",
                    lifecycle_state="active",
                    confidence=1.0,
                    evidence={"signal_name": "trade_id"},
                    created_at="2026-06-11T10:00:00+00:00",
                    updated_at="2026-06-11T10:00:00+00:00",
                ),
                messages=[
                    _gmail_message(
                        message_id="northstar-trade-confirmation",
                        gmail_thread_id="thread-northstar-trade-confirmation",
                        sender="NorthstarFXclearretail <northstarfx@northstar.example>",
                        subject="FX Retail Deal Confirmation - CCIL Reference Number 202606029000072",
                        extracted_signals={"sender_domain": "northstar.example", "trade_id": "202606029000072"},
                        headers={},
                    ),
                    _gmail_message(
                        message_id="ccil-trade-confirmation",
                        gmail_thread_id="thread-ccil-trade-confirmation",
                        sender="FxNoReply@ccilindia.co.in",
                        subject="FX-Retail - Trade Confirmation of TestUser | INPP29022231",
                        extracted_signals={"sender_domain": "ccilindia.co.in", "trade_id": "202606029000072"},
                        headers={},
                    ),
                ],
            )
        ]

        smart = _smart_inbox_from_mailbox_and_objects("postgresql://example/db", user_id="user-1", mailbox=mailbox)
        rows = [row for section in smart.sections for row in section.rows]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].primary_sender, "FX Retail")
        self.assertEqual(rows[0].title, "FX Retail trade 202606029000072")
        self.assertEqual(
            rows[0].summary,
            "FX Retail trade 202606029000072 combines the trade confirmation with the related bank/support validation thread.",
        )
        self.assertEqual(set(rows[0].source_message_ids), {"northstar-trade-confirmation", "ccil-trade-confirmation", "northstar-support-reply"})

    @patch("app.services.mail_groups.list_mail_object_bundles")
    def test_mail_object_bundle_normalizes_service_channel_sender(self, mock_bundles: Mock) -> None:
        mailbox = MailboxResponse(label="inbox", total_threads=0, loaded_threads=0, sections=[])
        mock_bundles.return_value = [
            MailObjectBundle(
                object=MailObjectRecord(
                    id="object-105715521",
                    user_id="user-1",
                    object_type="ticket",
                    canonical_key="ticket_id:northstar:105715521",
                    title="Northstar Bank grievance case 105715521",
                    summary="Northstar Bank replied on the grievance case.",
                    lifecycle_state="active",
                    confidence=1.0,
                    evidence={"signal_name": "ticket_id"},
                    created_at="2026-06-11T10:00:00+00:00",
                    updated_at="2026-06-11T10:00:00+00:00",
                ),
                messages=[
                    _gmail_message(
                        message_id="grievance-ack",
                        gmail_thread_id="thread-grievance-ack",
                        sender="Support Department <grievance.redressalcc@northstar.example>",
                        extracted_signals={"sender_domain": "northstar.example", "ticket_id": "105715521"},
                        headers={},
                    ),
                    _gmail_message(
                        message_id="grievance-update",
                        gmail_thread_id="thread-grievance-update",
                        sender="Support Department <grievance.redressalcc@northstar.example>",
                        extracted_signals={"sender_domain": "northstar.example", "ticket_id": "105715521"},
                        headers={},
                    ),
                ],
            )
        ]

        smart = _smart_inbox_from_mailbox_and_objects("postgresql://example/db", user_id="user-1", mailbox=mailbox)
        rows = [row for section in smart.sections for row in section.rows]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].primary_sender, "Northstar Bank")

    @patch("app.services.mail_groups.list_mail_object_bundles")
    def test_mail_object_absorbs_overlapping_ai_group_without_duplicate_row(self, mock_bundles: Mock) -> None:
        ai_group = _thread_row(
            thread_id="ai-northstar-complaint-group",
            message_count=3,
            ai_group_id="ai-group-105715521",
            presentation_status="ai_ready",
            children=[
                ("complaint-auto", "thread-complaint-auto"),
                ("ticket-reply", "thread-ticket-105715521"),
                ("ticket-followup", "thread-ticket-105715521"),
            ],
        ).model_copy(
            update={
                "title": "Northstar credit card consent complaint 105715521",
                "summary": "Northstar acknowledged the privacy complaint under case 105715521 and later followed up.",
                "ai_title": "Northstar credit card consent complaint 105715521",
                "ai_summary": "Northstar acknowledged the privacy complaint under case 105715521 and later followed up.",
                "latest_source_record_id": "ticket-followup",
                "latest_message_at": "2026-06-05T23:05:01+05:30",
                "latest_received_at": "2026-06-05T23:05:01+05:30",
                "sender": "Support Department",
            }
        )
        mailbox = MailboxResponse(
            label="inbox",
            total_threads=1,
            loaded_threads=1,
            sections=[{"id": "today", "title": "Today", "rows": [ai_group]}],
        )
        mock_bundles.return_value = [
            MailObjectBundle(
                object=MailObjectRecord(
                    id="object-105715521",
                    user_id="user-1",
                    object_type="ticket",
                    canonical_key="ticket_id:northstar:105715521",
                    title="Hdfc ticket 105715521",
                    summary="Mail related to Hdfc ticket 105715521.",
                    lifecycle_state="active",
                    confidence=1.0,
                    evidence={"signal_name": "ticket_id"},
                    created_at="2026-06-11T10:00:00+00:00",
                    updated_at="2026-06-11T10:00:00+00:00",
                ),
                messages=[
                    _gmail_message(message_id="ticket-reply", gmail_thread_id="thread-ticket-105715521", extracted_signals={"sender_domain": "northstar.example", "ticket_id": "105715521"}, headers={}),
                    _gmail_message(message_id="ticket-followup", gmail_thread_id="thread-ticket-105715521", extracted_signals={"sender_domain": "northstar.example", "ticket_id": "105715521"}, headers={}),
                ],
            )
        ]

        smart = _smart_inbox_from_mailbox_and_objects("postgresql://example/db", user_id="user-1", mailbox=mailbox)
        rows = [row for section in smart.sections for row in section.rows]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].row_key, "mail-object:ticket_id:northstar:105715521")
        self.assertEqual(rows[0].title, "Northstar credit card consent complaint 105715521")
        self.assertEqual(
            set(rows[0].source_message_ids),
            {"complaint-auto", "ticket-reply", "ticket-followup"},
        )
        self.assertEqual(
            set(rows[0].source_thread_ids),
            {"thread-complaint-auto", "thread-ticket-105715521"},
        )
        self.assertEqual(rows[0].grouping_reason["absorbed_mailbox_rows"][0]["row_key"], "ai-northstar-complaint-group")

    def test_smart_inbox_absorbs_registration_row_when_ai_titles_share_service_request_reference(self) -> None:
        conversation = _validated_thread_row(
            _thread_row(
                thread_id="ai-northstar-remittance-conversation",
                message_count=3,
                ai_group_id="ai-group-remittance-106400420",
                presentation_status="ai_ready",
                children=[
                    ("wire-opened", "thread-wire-opened"),
                    ("wire-reply", "thread-wire-reply"),
                    ("wire-followup", "thread-wire-followup"),
                ],
            ).model_copy(
                update={
                    "title": "Wire transfer status with Northstar Bank",
                    "summary": "Northstar replied about the outward remittance and next steps.",
                    "ai_title": "Wire transfer status with Northstar Bank",
                    "ai_summary": "Northstar replied about the outward remittance and next steps.",
                    "latest_source_record_id": "wire-reply",
                    "latest_message_at": "2026-06-11T11:00:00+05:30",
                    "latest_received_at": "2026-06-11T11:00:00+05:30",
                    "sender": "Northstar Bank",
                    "latest_sender": "Northstar Bank <care@northstarbank.example>",
                    "children": [
                        {
                            "message_id": "wire-opened",
                            "gmail_thread_id": "thread-wire-opened",
                            "sender": "Northstar Bank",
                            "subject": "Outward remittance processed",
                            "ai_title": "Northstar acknowledges remittance inquiry: case 106400420",
                            "snippet": "Northstar acknowledged the remittance inquiry.",
                            "received_at": "2026-06-11T10:00:00+05:30",
                        },
                        {
                            "message_id": "wire-reply",
                            "gmail_thread_id": "thread-wire-reply",
                            "sender": "Northstar Bank",
                            "subject": "Wire processed",
                            "ai_title": "Northstar Bank: wire processed for case 106400420",
                            "snippet": "Northstar replied with wire transfer status.",
                            "received_at": "2026-06-11T11:00:00+05:30",
                        },
                        {
                            "message_id": "wire-followup",
                            "gmail_thread_id": "thread-wire-followup",
                            "sender": "Northstar Bank",
                            "subject": "Remittance follow-up",
                            "ai_title": "Northstar Bank replies on remittance case 106400420",
                            "snippet": "Northstar replied on the remittance case.",
                            "received_at": "2026-06-10T10:00:00+05:30",
                        },
                    ],
                }
            )
        )
        registration = _validated_thread_row(
            _thread_row(
                thread_id="ai-northstar-service-request-106400420",
                message_count=1,
                ai_group_id="ai-group-registration-106400420",
                presentation_status="ai_ready",
                children=[("service-request-registered", "thread-service-request-registered")],
            ).model_copy(
                update={
                    "title": "Northstar remittance support request",
                    "summary": "Northstar Bank registered service request 106400420.",
                    "ai_title": "Northstar Bank registers remittance case 106400420",
                    "ai_summary": "Northstar Bank registered service request 106400420.",
                    "latest_source_record_id": "service-request-registered",
                    "latest_subject": "[Registered] - Service Request 106400420",
                    "latest_message_at": "2026-06-10T09:00:00+05:30",
                    "latest_received_at": "2026-06-10T09:00:00+05:30",
                    "sender": "Northstar Bank Care <care@northstarbank.example>",
                    "latest_sender": "Northstar Bank Care <care@northstarbank.example>",
                    "children": [
                        {
                            "message_id": "service-request-registered",
                            "gmail_thread_id": "thread-service-request-registered",
                            "sender": "Northstar Bank Care <care@northstarbank.example>",
                            "subject": "[Registered] - Service Request 106400420",
                            "ai_title": "Northstar Bank registers remittance case 106400420",
                            "snippet": "Service request 106400420 has been registered.",
                            "received_at": "2026-06-10T09:00:00+05:30",
                        }
                    ],
                }
            )
        )
        mailbox = MailboxResponse(
            label="inbox",
            total_threads=2,
            loaded_threads=2,
            sections=[{"id": "today", "title": "Today", "rows": [conversation, registration]}],
        )

        smart = _smart_inbox_from_mailbox(mailbox)
        rows = [row for section in smart.sections for row in section.rows]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].row_type, "verified_group")
        self.assertEqual(rows[0].confidence_tier, "exact")
        self.assertEqual(rows[0].title, "Wire transfer status with Northstar Bank")
        self.assertEqual(
            set(rows[0].source_message_ids),
            {"wire-opened", "wire-reply", "wire-followup", "service-request-registered"},
        )
        self.assertEqual(rows[0].grouping_reason["reference_absorption"]["value"], "106400420")

    def test_smart_reference_absorption_rejects_conflicting_visible_service_request(self) -> None:
        target = _validated_thread_row(
            _thread_row(
                thread_id="ai-northstar-service-request-106756996",
                message_count=1,
                ai_group_id="ai-group-registration-106756996",
                presentation_status="ai_ready",
                children=[("service-request-106756996", "thread-service-request-106756996")],
            ).model_copy(
                update={
                    "title": "Northstar service request 106756996 registered",
                    "summary": "Northstar Bank registered service request 106756996.",
                    "ai_title": "Northstar Bank registers service request 106756996",
                    "sender": "Northstar Bank Care <care@northstarbank.example>",
                    "latest_sender": "Northstar Bank Care <care@northstarbank.example>",
                    "children": [
                        {
                            "message_id": "service-request-106756996",
                            "gmail_thread_id": "thread-service-request-106756996",
                            "sender": "Northstar Bank Care <care@northstarbank.example>",
                            "subject": "Service Request 106756996",
                            "ai_title": "Northstar Bank registers service request 106756996",
                            "snippet": "Service request 106756996 has been registered.",
                            "received_at": "2026-06-11T10:00:00+00:00",
                        }
                    ],
                }
            )
        )
        conflicting = _validated_thread_row(
            _thread_row(
                thread_id="ai-northstar-conflicting-visible-reference",
                message_count=2,
                ai_group_id="ai-group-conflicting-reference",
                presentation_status="ai_ready",
                children=[("case-106756996", "thread-case-106756996"), ("case-105715521", "thread-case-105715521")],
            ).model_copy(
                update={
                    "title": "Northstar response on service request 106756996",
                    "summary": "Northstar also references older grievance case 105715521 in the same conversation.",
                    "ai_title": "Northstar response on service request 106756996",
                    "sender": "Northstar Bank",
                    "latest_sender": "Northstar Bank <care@northstarbank.example>",
                    "children": [
                        {
                            "message_id": "case-106756996",
                            "gmail_thread_id": "thread-case-106756996",
                            "sender": "Northstar Bank",
                            "subject": "Northstar response on service request 106756996",
                            "ai_title": "Northstar response on service request 106756996",
                            "snippet": "Northstar replied on service request 106756996.",
                            "received_at": "2026-06-11T10:00:00+00:00",
                        },
                        {
                            "message_id": "case-105715521",
                            "gmail_thread_id": "thread-case-105715521",
                            "sender": "Northstar Bank",
                            "subject": "Northstar acknowledgement of service request 105715521",
                            "ai_title": "Northstar acknowledgement of service request 105715521",
                            "snippet": "Northstar acknowledged service request 105715521.",
                            "received_at": "2026-06-11T10:00:00+00:00",
                        },
                    ],
                }
            )
        )
        mailbox = MailboxResponse(
            label="inbox",
            total_threads=2,
            loaded_threads=2,
            sections=[{"id": "today", "title": "Today", "rows": [target, conflicting]}],
        )

        smart = _smart_inbox_from_mailbox(mailbox)
        rows = [row for section in smart.sections for row in section.rows]

        self.assertEqual(len(rows), 2)
        self.assertFalse(any("reference_absorption" in row.grouping_reason for row in rows))

    def test_smart_workflow_cluster_groups_financial_platform_trading_limit_rows(self) -> None:
        rows = [
            _smart_row_with_workflow_object(
                _smart_row(
                    row_id="smart-row:limit-forwarded",
                    row_key="thread-limit-forwarded",
                    title="New trading limit request forwarded",
                    summary="The FX portal forwarded the new trading limit request to the bank for approval.",
                    primary_sender="FX Portal <noreply@provider.example>",
                ),
                value="fx-retail-limit-setup-2026",
                object_type="trading_limit",
            ),
            _smart_row_with_workflow_object(
                _smart_row(
                    row_id="smart-row:limit-rejected",
                    row_key="thread-limit-rejected",
                    title="Limit request rejected",
                    summary="The FX portal says the new trading limit request was rejected by the bank.",
                    primary_sender="FX Portal <noreply@provider.example>",
                ),
                value="fx-retail-limit-setup-2026",
                object_type="trading_limit",
            ),
            _smart_row_with_workflow_object(
                _smart_row(
                    row_id="smart-row:limit-funding",
                    row_key="thread-limit-funding",
                    title="Confirm funding for trading limit setup",
                    summary="The bank can set the FX Retail trading limit once the account is funded.",
                    primary_sender="Bank FX Retail Team <team@bank.example>",
                ),
                value="fx-retail-limit-setup-2026",
                object_type="trading_limit",
            ),
        ]

        clustered = _smart_workflow_cluster_rows(rows)

        self.assertEqual(len(clustered), 1)
        self.assertEqual(clustered[0].row_type, "related_bundle")
        self.assertEqual(clustered[0].title, "FX Retail trading limit funding needed")
        self.assertEqual(
            set(clustered[0].source_thread_ids),
            {"thread-limit-forwarded", "thread-limit-rejected", "thread-limit-funding"},
        )
        self.assertEqual(clustered[0].grouping_reason["source"], "smart_workflow_cluster")
        self.assertEqual(clustered[0].grouping_reason["shared_object_key"], "trading-limit:fx-retail-limit-setup-2026")

    def test_smart_workflow_cluster_groups_retail_direct_account_setup_rows(self) -> None:
        rows = [
            _smart_row_with_workflow_object(
                _smart_row(
                    row_id="smart-row:account-approved",
                    row_key="thread-account-approved",
                    title="RBI Retail Direct account approved",
                    summary="The account opening request was successfully processed.",
                    primary_sender="RBI Retail Direct <support@example-retailer.example>",
                ),
                value="rbi-retail-direct-account-setup",
                object_type="account_setup",
            ),
            _smart_row_with_workflow_object(
                _smart_row(
                    row_id="smart-row:virtual-account",
                    row_key="thread-virtual-account",
                    title="Virtual account number created",
                    summary="RBI Retail Direct created a virtual account number for payments.",
                    primary_sender="RBI Retail Direct <support@example-retailer.example>",
                ),
                value="rbi-retail-direct-account-setup",
                object_type="account_setup",
            ),
            _smart_row_with_workflow_object(
                _smart_row(
                    row_id="smart-row:kyc-signed",
                    row_key="thread-kyc-signed",
                    title="KYC document signed",
                    summary="The RBI Retail Direct account setup document was signed and attached for records.",
                    primary_sender="RBI Retail Direct Support <support@example-retailer.example>",
                ),
                value="rbi-retail-direct-account-setup",
                object_type="account_setup",
            ),
        ]

        clustered = _smart_workflow_cluster_rows(rows)

        self.assertEqual(len(clustered), 1)
        self.assertEqual(clustered[0].row_type, "related_bundle")
        self.assertEqual(clustered[0].title, "RBI Retail Direct account setup")
        self.assertEqual(
            set(clustered[0].source_thread_ids),
            {"thread-account-approved", "thread-virtual-account", "thread-kyc-signed"},
        )

    def test_smart_workflow_cluster_groups_northstar_wire_remittance_context_without_fx_trade_rows(self) -> None:
        rows = [
            _smart_row_with_workflow_object(
                _smart_row(
                    row_id="smart-row:northstar-wire-status",
                    row_key="thread-northstar-wire-status",
                    title="Wire transfer status with Northstar Bank",
                    summary="You asked Northstar Bank to check the status of an outward remittance, and the bank replied through the same reference thread.",
                    primary_sender="Northstar Bank",
                    source_message_ids=["wire-status-1", "wire-status-2", "wire-status-3", "wire-status-4"],
                    row_type="verified_group",
                    confidence_tier="exact",
                ),
                value="outward-remittance-june-2026",
                object_type="wire_remittance",
            ),
            _smart_row_with_workflow_object(
                _smart_row(
                    row_id="smart-row:northstar-remittance-processed",
                    row_key="thread-northstar-remittance-processed",
                    title="Outward remittance processed",
                    summary="Northstar Bank says an outward remittance was processed and included the SWIFT message copy for reference.",
                    primary_sender="Northstar Bank",
                    source_message_ids=["remittance-advice"],
                ),
                value="outward-remittance-june-2026",
                object_type="wire_remittance",
            ),
            _smart_row(
                row_id="smart-row:northstar-fx-validation",
                row_key="thread-northstar-fx-validation",
                title="FX retail trade validation issue",
                summary="Northstar Bank confirmed FX retail trade 202606029000072 and the related support thread shows trouble validating the trade number.",
                primary_sender="Northstar Bank",
                source_message_ids=["fx-validation-1", "fx-validation-2"],
                row_type="verified_group",
            ),
        ]

        clustered = _smart_workflow_cluster_rows(rows)

        self.assertEqual(len(clustered), 2)
        remittance = next(row for row in clustered if row.row_key.startswith("smart-workflow:northstar-bank:wire-remittance:"))
        self.assertEqual(remittance.row_type, "related_bundle")
        self.assertEqual(remittance.primary_sender, "Northstar Bank")
        self.assertEqual(remittance.title, "Northstar Bank outward remittance processed")
        self.assertEqual(remittance.summary, "5 related emails for Northstar Bank outward remittance processed.")
        self.assertEqual(
            set(remittance.source_message_ids),
            {"wire-status-1", "wire-status-2", "wire-status-3", "wire-status-4", "remittance-advice"},
        )
        remaining_titles = {row.title for row in clustered if row.id != remittance.id}
        self.assertEqual(remaining_titles, {"FX retail trade validation issue"})

    def test_smart_workflow_cluster_groups_penn_state_application_lifecycle_only(self) -> None:
        rows = [
            _smart_row_with_workflow_object(
                _smart_row(
                    row_id="smart-row:penn-next-steps",
                    row_key="thread-penn-next-steps",
                    title="State University application and next steps",
                    summary="State University has updated the application and sent admitted-student next steps.",
                    primary_sender="State University",
                    source_message_ids=["penn-admitted", "penn-update-june"],
                    row_type="verified_group",
                ),
                value="penn-state-application-2026",
                object_type="application",
            ),
            _smart_row_with_workflow_object(
                _smart_row(
                    row_id="smart-row:penn-costs",
                    row_key="thread-penn-costs",
                    title="State University cost estimate available",
                    summary="State University says estimated educational costs are available in LionPATH.",
                    primary_sender="State University",
                    source_message_ids=["penn-costs"],
                ),
                value="penn-state-application-2026",
                object_type="application",
            ),
            _smart_row_with_workflow_object(
                _smart_row(
                    row_id="smart-row:penn-reconsideration",
                    row_key="thread-penn-reconsideration",
                    title="State University reconsideration question answered",
                    summary="State University Campus answered a reconsideration question for the current acceptance.",
                    primary_sender="State University Campus <behrend.university-admissions@example.edu>",
                    source_message_ids=["penn-reconsideration"],
                ),
                value="penn-state-application-2026",
                object_type="application",
            ),
            _smart_row_with_workflow_object(
                _smart_row(
                    row_id="smart-row:penn-application-update",
                    row_key="thread-penn-application-update",
                    title="State University application update",
                    summary="State University Undergraduate Admissions says the application was updated in StudentPortal.",
                    primary_sender="State University",
                    source_message_ids=["penn-application-update"],
                ),
                value="penn-state-application-2026",
                object_type="application",
            ),
            _smart_row(
                row_id="smart-row:um-financial-aid",
                row_key="thread-um-financial-aid",
                title="Financial aid offer available",
                summary="UM Dearborn says the 2026-27 financial aid offer is ready to review.",
                primary_sender="Office of Financial Aid and Scholarships <umd-ask-ofa@campus.umdearborn.edu>",
                source_message_ids=["um-financial-aid"],
            ),
        ]

        clustered = _smart_workflow_cluster_rows(rows)

        self.assertEqual(len(clustered), 2)
        penn = next(row for row in clustered if row.row_key.startswith("smart-workflow:penn-state:application-lifecycle:"))
        self.assertEqual(penn.row_type, "related_bundle")
        self.assertEqual(penn.title, "State University admission, costs, and reconsideration")
        self.assertEqual(penn.summary, "5 related emails for State University admission, costs, and reconsideration.")
        self.assertEqual(
            set(penn.source_message_ids),
            {"penn-admitted", "penn-update-june", "penn-costs", "penn-reconsideration", "penn-application-update"},
        )
        remaining_titles = {row.title for row in clustered if row.id != penn.id}
        self.assertEqual(remaining_titles, {"Financial aid offer available"})

    def test_smart_workflow_cluster_requires_shared_workflow_object(self) -> None:
        rows = [
            _smart_row(
                row_id="smart-row:northstar-wire-status",
                row_key="thread-northstar-wire-status",
                title="Wire transfer status with Northstar Bank",
                summary="Northstar Bank replied about an outward remittance status request.",
                primary_sender="Northstar Bank",
                source_message_ids=["wire-status"],
            ),
            _smart_row(
                row_id="smart-row:northstar-remittance-processed",
                row_key="thread-northstar-remittance-processed",
                title="Outward remittance processed",
                summary="Northstar Bank says an outward remittance was processed.",
                primary_sender="Northstar Bank",
                source_message_ids=["remittance-advice"],
            ),
            _smart_row(
                row_id="smart-row:northstar-remittance-query",
                row_key="thread-northstar-remittance-query",
                title="Northstar outward remittance query received",
                summary="Northstar Bank acknowledged a different outward remittance query.",
                primary_sender="Northstar Bank",
                source_message_ids=["remittance-query"],
            ),
        ]

        clustered = _smart_workflow_cluster_rows(rows)

        self.assertEqual(clustered, rows)
        self.assertTrue(all(row.grouping_reason.get("source") != "smart_workflow_cluster" for row in clustered))

    def test_smart_workflow_cluster_rejects_generic_or_stale_platform_buckets(self) -> None:
        generic_rows = [
            _smart_row(
                row_id="smart-row:noreply-one",
                row_key="thread-noreply-one",
                title="Account approved",
                summary="Your account setup request was processed.",
                primary_sender="Noreply <noreply@example.com>",
            ),
            _smart_row(
                row_id="smart-row:noreply-two",
                row_key="thread-noreply-two",
                title="Virtual account created",
                summary="Your virtual account number was created.",
                primary_sender="Noreply <noreply@example.com>",
            ),
        ]
        stale_rows = [
            _smart_row(
                row_id="smart-row:stale-one",
                row_key="thread-stale-one",
                title="Nimbus Direct account approved",
                summary="The account opening request was successfully processed.",
                primary_sender="Nimbus Direct <support@nimbus.example>",
            ).model_copy(update={"latest_message_at": "2026-01-01T10:00:00+00:00"}),
            _smart_row(
                row_id="smart-row:stale-two",
                row_key="thread-stale-two",
                title="Nimbus Direct virtual account created",
                summary="The virtual account number was created.",
                primary_sender="Nimbus Direct <support@nimbus.example>",
            ).model_copy(update={"latest_message_at": "2026-04-01T10:00:00+00:00"}),
        ]

        self.assertEqual(_smart_workflow_cluster_rows(generic_rows), generic_rows)
        self.assertEqual(_smart_workflow_cluster_rows(stale_rows), stale_rows)

    @patch("app.services.mail_groups.list_mail_object_bundles")
    def test_generated_partial_object_coverage_keeps_uncovered_mailbox_rows(self, mock_bundles: Mock) -> None:
        rng = random.Random(20260612)
        mailbox_rows = []
        object_messages: list[GmailMessageRecord] = []
        fully_covered_threads: set[str] = set()
        partially_covered_threads: set[str] = set()
        uncovered_threads: set[str] = set()

        for index in range(24):
            thread_id = f"generated-thread-{index}"
            children = [(f"{thread_id}-child-{child_index}", thread_id) for child_index in range(2)]
            mailbox_rows.append(_thread_row(thread_id=thread_id, message_count=3, ai_group_id=None, presentation_status="fallback", children=children))
            coverage = rng.choice(("full", "partial", "none"))
            if coverage == "full":
                fully_covered_threads.add(thread_id)
                object_messages.extend(
                    [
                        _gmail_message(message_id=f"latest-{thread_id}", gmail_thread_id=thread_id, extracted_signals={"sender_domain": "provider.example.com", "ticket_id": "generated"}, headers={}),
                        *[
                            _gmail_message(message_id=child_id, gmail_thread_id=thread_id, extracted_signals={"sender_domain": "provider.example.com", "ticket_id": "generated"}, headers={})
                            for child_id, _child_thread_id in children
                        ],
                    ]
                )
            elif coverage == "partial":
                partially_covered_threads.add(thread_id)
                object_messages.append(
                    _gmail_message(message_id=children[0][0], gmail_thread_id=thread_id, extracted_signals={"sender_domain": "provider.example.com", "ticket_id": "generated"}, headers={})
                )
            else:
                uncovered_threads.add(thread_id)

        mock_bundles.return_value = [
            MailObjectBundle(
                object=MailObjectRecord(
                    id="object-generated",
                    user_id="user-1",
                    object_type="ticket",
                    canonical_key="ticket_id:provider:generated",
                    title="Provider generated ticket",
                    summary="Generated object coverage test.",
                    lifecycle_state="active",
                    confidence=1.0,
                    evidence={"seed": 20260612},
                    created_at="2026-06-11T10:00:00+00:00",
                    updated_at="2026-06-11T10:00:00+00:00",
                ),
                messages=object_messages,
            )
        ]
        mailbox = MailboxResponse(
            label="inbox",
            total_threads=len(mailbox_rows),
            loaded_threads=len(mailbox_rows),
            sections=[{"id": "today", "title": "Today", "rows": mailbox_rows}],
        )

        smart = _smart_inbox_from_mailbox_and_objects("postgresql://example/db", user_id="user-1", mailbox=mailbox)
        row_keys = {row.row_key for section in smart.sections for row in section.rows}

        for thread_id in partially_covered_threads | uncovered_threads:
            with self.subTest(thread_id=thread_id):
                self.assertIn(thread_id, row_keys)
        for thread_id in fully_covered_threads:
            with self.subTest(thread_id=thread_id):
                self.assertNotIn(thread_id, row_keys)
        self.assertIn("mail-object:ticket_id:provider:generated", row_keys)

    @patch("app.services.mail_groups.list_mail_object_bundles")
    def test_smart_inbox_object_path_does_not_run_speculative_workflow_clustering(self, mock_bundles: Mock) -> None:
        mock_bundles.return_value = [
            MailObjectBundle(
                object=MailObjectRecord(
                    id="object-generated",
                    user_id="user-1",
                    object_type="ticket",
                    canonical_key="ticket_id:provider:generated",
                    title="Provider generated ticket",
                    summary="Generated object coverage test.",
                    lifecycle_state="active",
                    confidence=1.0,
                    evidence={"seed": 20260628},
                    created_at="2026-06-11T10:00:00+00:00",
                    updated_at="2026-06-11T10:00:00+00:00",
                ),
                messages=[
                    _gmail_message(
                        message_id="object-message-1",
                        gmail_thread_id="object-thread-1",
                        extracted_signals={"sender_domain": "provider.example.com", "ticket_id": "generated"},
                        headers={},
                    ),
                    _gmail_message(
                        message_id="object-message-2",
                        gmail_thread_id="object-thread-2",
                        extracted_signals={"sender_domain": "provider.example.com", "ticket_id": "generated"},
                        headers={},
                    ),
                ],
            )
        ]
        mailbox = MailboxResponse(
            label="inbox",
            total_threads=0,
            loaded_threads=0,
            sections=[{"id": "today", "title": "Today", "rows": []}],
        )

        with patch("app.services.mail_groups._smart_workflow_cluster_rows", side_effect=AssertionError("speculative clustering should not run")) as mock_cluster:
            smart = _smart_inbox_from_mailbox_and_objects("postgresql://example/db", user_id="user-1", mailbox=mailbox)

        mock_cluster.assert_not_called()
        row_keys = {row.row_key for section in smart.sections for row in section.rows}
        self.assertIn("mail-object:ticket_id:provider:generated", row_keys)

    def test_related_suggestions_do_not_invent_weak_topic_matches(self) -> None:
        rows = [
            _smart_row(
                row_id="smart-row:thread-1",
                row_key="thread-1",
                title="Provider service request update",
                summary="Service request update from the bank.",
                primary_sender="Provider <alerts@provider.example.com>",
            ),
            _smart_row(
                row_id="smart-row:thread-2",
                row_key="thread-2",
                title="Provider service request reminder",
                summary="Another service request reminder from the bank.",
                primary_sender="Provider <alerts@provider.example.com>",
            ),
            _smart_row(
                row_id="smart-row:thread-3",
                row_key="thread-3",
                title="Provider service request verified",
                summary="Exact references are handled by object grouping instead.",
                primary_sender="Provider <alerts@provider.example.com>",
                row_type="verified_group",
                confidence_tier="exact",
            ),
        ]

        smart = _smart_inbox_response_from_rows(rows, generated_at="2026-06-11T10:00:00+00:00")

        self.assertEqual(smart.total_rows, 3)
        self.assertEqual(smart.related_suggestions, [])

    def test_dashboard_feed_becomes_mail_derived_work_queue(self) -> None:
        dashboard = DashboardResponse(
            auth=GoogleAuthState(available=True, connected=True),
            feed=FeedResponse(
                now=[_attention_item("reply-1", need_type="decision", action_type="external", current_state="open")],
                today=[_attention_item("waiting-1", need_type="awareness", action_type="none", current_state="waiting")],
                worth_knowing=[_attention_item("update-1", need_type="awareness", action_type="none", current_state="open")],
            ),
        )

        queue = _smart_work_queue_from_dashboard(dashboard)

        self.assertEqual(len(queue.needs_action), 1)
        self.assertEqual(len(queue.waiting), 1)
        self.assertEqual(len(queue.important_updates), 1)
        self.assertEqual(queue.total_open, 3)
        self.assertEqual(queue.needs_action[0].smart_row_id, "smart-row:thread-reply-1")

    def test_smart_inbox_rows_drive_work_queue_without_resolved_open_rows_as_needs_action(self) -> None:
        smart = _smart_inbox_response_from_rows(
            [
                _smart_row(
                    row_id="smart-row:apple-payment",
                    row_key="thread-apple-payment",
                    title="iCloud+ payment issue",
                    summary="Apple could not process the payment method.",
                    primary_sender="Apple <no_reply@email.apple.com>",
                    action_type="pay",
                    priority=74,
                ),
                _smart_row(
                    row_id="smart-row:northstar-resolved",
                    row_key="thread-northstar-resolved",
                    title="Northstar Bank service request resolved",
                    summary="Northstar marked the service request resolved.",
                    primary_sender="Northstar Bank Care <care@northstarbank.example>",
                    row_type="verified_group",
                    action_type="open",
                    priority=98,
                ),
                _smart_row(
                    row_id="smart-row:newsletter",
                    row_key="thread-newsletter",
                    title="Newsletter updates",
                    summary="Bundled newsletter mail.",
                    primary_sender="Newsletter <news@example.com>",
                    row_type="related_bundle",
                    action_type="open",
                    priority=100,
                ),
            ],
            generated_at="2026-06-11T10:00:00+00:00",
        )

        queue = _smart_work_queue_from_smart_inbox(smart)

        self.assertEqual([item.smart_row_id for item in queue.needs_action], ["smart-row:apple-payment"])
        self.assertEqual([item.smart_row_id for item in queue.important_updates], ["smart-row:northstar-resolved"])
        self.assertEqual(queue.active_conversations, [])
        self.assertEqual(queue.total_open, 2)
        self.assertEqual(queue.needs_action[0].reason["source"], "smart_inbox_row")

    def test_dashboard_derived_snapshot_work_queue_is_replaced_by_smart_inbox_rows(self) -> None:
        dashboard = DashboardResponse(
            auth=GoogleAuthState(available=True, connected=True),
            feed=FeedResponse(now=[_attention_item("stale-dashboard-item", need_type="decision", action_type="external", current_state="open")]),
        )
        stale_queue = _smart_work_queue_from_dashboard(dashboard)
        smart = _smart_inbox_response_from_rows(
            [
                _smart_row(
                    row_id="smart-row:icloud-payment",
                    row_key="thread-icloud-payment",
                    title="iCloud+ payment issue",
                    summary="Apple could not process the payment method.",
                    primary_sender="Apple <no_reply@email.apple.com>",
                    action_type="pay",
                    priority=74,
                )
            ],
            generated_at="2026-06-11T10:00:00+00:00",
        )

        queue = _smart_work_queue_from_snapshot(stale_queue.model_dump(mode="json"), dashboard=dashboard, smart_inbox=smart)

        self.assertEqual([item.smart_row_id for item in queue.needs_action], ["smart-row:icloud-payment"])
        self.assertEqual(queue.needs_action[0].reason["source"], "smart_inbox_row")
        self.assertNotEqual(queue.needs_action[0].id, stale_queue.needs_action[0].id)

    def test_storage_ids_are_user_scoped_without_changing_api_ids(self) -> None:
        mailbox = MailboxResponse(
            label="inbox",
            total_threads=1,
            loaded_threads=1,
            sections=[{"id": "today", "title": "Today", "rows": [_thread_row(thread_id="thread-1", message_count=1, ai_group_id="group-1", presentation_status="ai_ready", children=[])]}],
        )
        dashboard = DashboardResponse(
            auth=GoogleAuthState(available=True, connected=True),
            feed=FeedResponse(now=[_attention_item("1", need_type="decision", action_type="external", current_state="open")]),
        )

        smart = _smart_inbox_from_mailbox(mailbox)
        queue = _smart_work_queue_from_dashboard(dashboard)
        stored_rows = _smart_inbox_storage_rows(smart, user_id="user-1")
        stored_items = _smart_work_storage_items(queue, user_id="user-1")

        self.assertEqual(smart.sections[0].rows[0].id, "smart-row:thread-1")
        self.assertEqual(stored_rows[0]["id"], "user-1:smart-row:thread-1")
        self.assertEqual(stored_items[0]["id"], "user-1:smart-work:1")
        self.assertEqual(stored_items[0]["smart_row_id"], "user-1:smart-row:thread-1")

    def test_related_suggestion_storage_ids_are_user_scoped_without_changing_api_ids(self) -> None:
        smart = SmartInboxResponse(
            total_rows=0,
            related_suggestions=[
                SmartRelatedSuggestion(
                    id="smart-related:1",
                    suggestion_key="evidence-backed:test",
                    source_row_id="smart-row:thread-1",
                    related_row_id="smart-row:thread-2",
                    title="Related mail",
                    reason="Injected evidence-backed suggestion.",
                    confidence=0.9,
                    evidence={"source": "test"},
                )
            ],
        )

        stored = _smart_related_storage_suggestions(smart, user_id="user-1")

        self.assertEqual(len(stored), 1)
        self.assertTrue(stored[0]["id"].startswith("user-1:smart-related:"))
        self.assertEqual(stored[0]["source_row_id"], "user-1:smart-row:thread-1")
        self.assertEqual(stored[0]["related_row_id"], "user-1:smart-row:thread-2")

    def test_readiness_does_not_claim_offline_ready_without_offline_pack(self) -> None:
        mailbox = MailboxResponse(label="inbox", total_threads=1, loaded_threads=1, full_import_completed=True)
        smart = _smart_inbox_from_mailbox(mailbox)
        readiness = _smart_readiness_from_mailbox(
            mailbox=mailbox,
            sync=AppSessionSyncState(full_import_completed=True, ready_group_count=1),
            smart_inbox=smart,
        )

        self.assertFalse(readiness.offline_ready)
        self.assertFalse(readiness.first_ready_complete)
        self.assertFalse(readiness.hot_window_complete)
        self.assertEqual(readiness.stage, "empty")
        self.assertEqual(readiness.offline_ready_rows, 0)
        self.assertEqual(readiness.offline_partial_rows, 0)
        self.assertEqual(readiness.offline_failed_rows, 0)

    @patch("app.services.mail_groups.list_messages_by_ids")
    def test_smart_inbox_offline_status_uses_persisted_renderable_bodies(self, mock_messages: Mock) -> None:
        smart = _smart_inbox_response_from_rows(
            [
                _smart_row(
                    row_id="smart-row:thread-ready",
                    row_key="thread-ready",
                    title="Ready thread",
                    summary="Has a render document.",
                    primary_sender="Sender <sender@example.com>",
                    source_message_ids=["msg-ready"],
                ),
                _smart_row(
                    row_id="smart-row:thread-partial",
                    row_key="thread-partial",
                    title="Partial thread",
                    summary="Needs a body fetch.",
                    primary_sender="Sender <sender@example.com>",
                    source_message_ids=["msg-partial"],
                ),
                _smart_row(
                    row_id="smart-row:thread-failed",
                    row_key="thread-failed",
                    title="Failed thread",
                    summary="Body fetch failed.",
                    primary_sender="Sender <sender@example.com>",
                    source_message_ids=["msg-failed"],
                ),
            ],
            generated_at="2026-06-11T10:00:00+00:00",
        )
        mock_messages.return_value = [
            _gmail_message(message_id="msg-ready", extracted_signals={}, headers={}, html_render_document="<article>Ready</article>", body_fetch_status="fetched"),
            _gmail_message(message_id="msg-partial", extracted_signals={}, headers={}, body_fetch_status="queued"),
            _gmail_message(message_id="msg-failed", extracted_signals={}, headers={}, body_fetch_status="failed"),
        ]

        updated = _smart_inbox_with_offline_status("postgresql://example/db", user_id="user-1", smart_inbox=smart)
        rows = [row for section in updated.sections for row in section.rows]

        self.assertEqual([row.offline_status for row in rows], ["ready", "partial", "failed"])
        mock_messages.assert_called_once_with(
            "postgresql://example/db",
            user_id="user-1",
            message_ids=["msg-ready", "msg-partial", "msg-failed"],
        )

    def test_readiness_claims_offline_ready_only_when_hot_window_and_rows_are_ready(self) -> None:
        mailbox = MailboxResponse(label="inbox", total_threads=1, loaded_threads=1, full_import_completed=True)
        smart = _smart_inbox_response_from_rows(
            [
                _smart_row(
                    row_id="smart-row:thread-1",
                    row_key="thread-1",
                    title="Ready thread",
                    summary="Offline-ready thread.",
                    primary_sender="Sender <sender@example.com>",
                ).model_copy(update={"offline_status": "ready"})
            ],
            generated_at="2026-06-11T10:00:00+00:00",
        )
        readiness = _smart_readiness_from_mailbox(
            mailbox=mailbox,
            sync=AppSessionSyncState(full_import_completed=True, ready_group_count=1),
            smart_inbox=smart,
        )

        self.assertTrue(readiness.first_ready_complete)
        self.assertTrue(readiness.hot_window_complete)
        self.assertTrue(readiness.offline_ready)
        self.assertEqual(readiness.stage, "offline_ready")
        self.assertEqual(readiness.offline_ready_rows, 1)
        self.assertEqual(readiness.offline_partial_rows, 0)
        self.assertEqual(readiness.offline_failed_rows, 0)

    def test_readiness_uses_hot_window_completion_without_full_history_completion(self) -> None:
        mailbox = MailboxResponse(label="inbox", total_threads=1, loaded_threads=1, full_import_completed=False)
        smart = _smart_inbox_response_from_rows(
            [
                _smart_row(
                    row_id="smart-row:thread-1",
                    row_key="thread-1",
                    title="Hot window ready thread",
                    summary="Recent mail is ready while older mail continues.",
                    primary_sender="Sender <sender@example.com>",
                )
            ],
            generated_at="2026-06-11T10:00:00+00:00",
        )
        readiness = _smart_readiness_from_mailbox(
            mailbox=mailbox,
            sync=AppSessionSyncState(
                full_import_completed=False,
                hot_window_completed_at="2026-06-11T10:00:00+00:00",
                ready_group_count=1,
            ),
            smart_inbox=smart,
        )

        self.assertTrue(readiness.first_ready_complete)
        self.assertTrue(readiness.hot_window_complete)
        self.assertFalse(readiness.offline_ready)
        self.assertEqual(readiness.stage, "snapshot_ready")

    def test_readiness_uses_processed_thread_coverage_when_grouping_collapses_rows(self) -> None:
        rows: list[SmartInboxRow] = []
        for index in range(286):
            source_thread_ids = [f"thread-{index}"]
            source_message_ids = [f"msg-{index}"]
            if index < 36:
                source_thread_ids.append(f"thread-extra-{index}")
                source_message_ids.append(f"msg-extra-{index}")
            rows.append(
                _smart_row(
                    row_id=f"smart-row:{index}",
                    row_key=f"thread-{index}",
                    title=f"Grouped row {index}",
                    summary="A ready smart row.",
                    primary_sender="Sender <sender@example.com>",
                    source_message_ids=source_message_ids,
                    row_type="verified_group" if index < 36 else "summarized_thread",
                ).model_copy(update={"source_thread_ids": source_thread_ids})
            )
        mailbox = MailboxResponse(label="inbox", total_threads=322, loaded_threads=322, full_import_completed=True)
        smart = _smart_inbox_response_from_rows(rows, generated_at="2026-06-11T10:00:00+00:00")

        readiness = _smart_readiness_from_mailbox(
            mailbox=mailbox,
            sync=AppSessionSyncState(full_import_completed=True, ready_group_count=322),
            smart_inbox=smart,
        )

        self.assertEqual(smart.ready_count, 286)
        self.assertEqual(readiness.processed_threads, 322)
        self.assertEqual(readiness.processed_messages, 322)
        self.assertTrue(readiness.first_ready_complete)
        self.assertTrue(readiness.hot_window_complete)
        self.assertFalse(readiness.offline_ready)
        self.assertEqual(readiness.stage, "snapshot_ready")

    def test_readiness_targets_completed_small_hot_window_not_full_history(self) -> None:
        rows = [
            _smart_row(
                row_id=f"smart-row:{index}",
                row_key=f"recent-thread-{index}",
                title=f"Recent row {index}",
                summary="A ready recent smart row.",
                primary_sender="Sender <sender@example.com>",
            ).model_copy(update={"offline_status": "ready"})
            for index in range(48)
        ]
        mailbox = MailboxResponse(label="inbox", total_threads=323, loaded_threads=323, full_import_completed=True)
        smart = _smart_inbox_response_from_rows(rows, generated_at="2026-06-11T10:00:00+00:00")

        readiness = _smart_readiness_from_mailbox(
            mailbox=mailbox,
            sync=AppSessionSyncState(
                full_import_completed=True,
                hot_window_completed_at="2026-06-11T10:00:00+00:00",
                ready_group_count=48,
            ),
            smart_inbox=smart,
        )

        self.assertEqual(readiness.processed_threads, 48)
        self.assertTrue(readiness.first_ready_complete)
        self.assertTrue(readiness.hot_window_complete)
        self.assertTrue(readiness.offline_ready)
        self.assertEqual(readiness.stage, "offline_ready")

    @patch("app.services.mail_groups.enqueue_job")
    def test_smart_inbox_body_warmup_enqueues_only_offline_partial_rows(self, mock_enqueue: Mock) -> None:
        mock_enqueue.side_effect = [SimpleNamespace(id="job-1"), SimpleNamespace(id="job-2")]
        settings = SimpleNamespace(database_path="postgresql://example/db")
        smart = _smart_inbox_response_from_rows(
            [
                _smart_row(
                    row_id="smart-row:thread-1",
                    row_key="thread-1",
                    title="Partial thread",
                    summary="Needs body fetch.",
                    primary_sender="Sender <sender@example.com>",
                ).model_copy(update={"offline_status": "partial"}),
                _smart_row(
                    row_id="smart-row:thread-2",
                    row_key="thread-2",
                    title="Ready thread",
                    summary="Already cached.",
                    primary_sender="Sender <sender@example.com>",
                ).model_copy(update={"offline_status": "ready"}),
                _smart_row(
                    row_id="smart-row:thread-3",
                    row_key="thread-3",
                    title="Failed thread",
                    summary="Body fetch failed.",
                    primary_sender="Sender <sender@example.com>",
                ).model_copy(update={"offline_status": "failed"}),
                _smart_row(
                    row_id="smart-row:thread-4",
                    row_key="thread-4",
                    title="Another partial thread",
                    summary="Needs body fetch too.",
                    primary_sender="Sender <sender@example.com>",
                ).model_copy(update={"offline_status": "partial"}),
            ],
            generated_at="2026-06-11T10:00:00+00:00",
        )

        job_ids = _enqueue_body_fetch_for_smart_inbox(settings, user_id="user-1", smart_inbox=smart, limit=10, priority=65)

        self.assertEqual(job_ids, ["job-1", "job-2"])
        self.assertEqual(mock_enqueue.call_count, 2)
        payloads = [call.kwargs["payload"] for call in mock_enqueue.call_args_list]
        self.assertEqual(payloads, [{"user_id": "user-1", "gmail_thread_id": "thread-1"}, {"user_id": "user-1", "gmail_thread_id": "thread-4"}])
        self.assertEqual(mock_enqueue.call_args_list[0].kwargs["queue"], "reader")
        self.assertEqual(mock_enqueue.call_args_list[0].kwargs["priority"], 65)

    def test_replace_smart_projection_persists_rows_and_nulls_missing_work_item_fk(self) -> None:
        connection = FakeConnection()
        row = {
            "id": "smart-row:thread-1",
            "row_key": "thread-1",
            "row_type": "summarized_thread",
            "title": "Thread title",
            "summary": "Thread summary",
            "source_thread_ids": ["thread-1"],
            "source_message_ids": ["msg-1"],
            "confidence_tier": "strong",
            "confidence": 0.82,
            "grouping_reason": {"source": "test"},
            "readiness": "ready",
            "section": "today",
        }
        work_item = {
            "id": "smart-work:missing",
            "kind": "needs_action",
            "title": "Reply needed",
            "summary": "Needs a reply",
            "smart_row_id": "smart-row:not-in-current-window",
            "source_thread_ids": ["thread-2"],
            "source_message_ids": [],
            "reason": {"source": "test"},
        }

        with patch("app.db.mail_groups.get_engine", return_value=FakeEngine(connection)):
            replace_smart_inbox_projection(
                "postgresql://example/db",
                user_id="user-1",
                rows=[row],
                work_items=[work_item],
            )

        statements = [call[0] for call in connection.calls]
        params = [call[1] for call in connection.calls]
        self.assertTrue(any("DELETE FROM smart_work_items" in statement for statement in statements))
        self.assertTrue(any("INSERT INTO smart_inbox_rows" in statement for statement in statements))
        self.assertTrue(any("INSERT INTO smart_work_items" in statement for statement in statements))
        inserted_row = next(param for statement, param in connection.calls if "INSERT INTO smart_inbox_rows" in statement)
        inserted_item = next(param for statement, param in connection.calls if "INSERT INTO smart_work_items" in statement)
        self.assertEqual(inserted_row["section"], "today")
        self.assertIsNotNone(inserted_row["generated_from_hash"])
        self.assertIsNone(inserted_item["smart_row_id"])
        self.assertEqual(params[0]["user_id"], "user-1")

    def test_replace_smart_projection_persists_related_suggestions(self) -> None:
        connection = FakeConnection()
        row_1 = {
            "id": "smart-row:thread-1",
            "row_key": "thread-1",
            "row_type": "summarized_thread",
            "title": "Thread one",
            "summary": "",
            "source_thread_ids": ["thread-1"],
            "source_message_ids": ["msg-1"],
        }
        row_2 = {
            "id": "smart-row:thread-2",
            "row_key": "thread-2",
            "row_type": "summarized_thread",
            "title": "Thread two",
            "summary": "",
            "source_thread_ids": ["thread-2"],
            "source_message_ids": ["msg-2"],
        }
        suggestion = {
            "id": "smart-related:1",
            "suggestion_key": "evidence-backed:test",
            "source_row_id": "smart-row:thread-1",
            "related_row_id": "smart-row:thread-2",
            "title": "Related mail",
            "reason": "Injected evidence-backed suggestion.",
            "confidence": 0.66,
            "evidence": {"source": "test"},
            "status": "active",
        }
        stale_suggestion = {
            **suggestion,
            "id": "smart-related:stale",
            "suggestion_key": "evidence-backed:stale",
            "related_row_id": "smart-row:not-active",
        }

        with patch("app.db.mail_groups.get_engine", return_value=FakeEngine(connection)):
            replace_smart_inbox_projection(
                "postgresql://example/db",
                user_id="user-1",
                rows=[row_1, row_2],
                related_suggestions=[suggestion, stale_suggestion],
                work_items=[],
            )

        inserted_suggestions = [param for statement, param in connection.calls if "INSERT INTO related_suggestions" in statement]
        self.assertEqual(len(inserted_suggestions), 1)
        self.assertEqual(inserted_suggestions[0]["source_smart_row_id"], "smart-row:thread-1")
        self.assertEqual(inserted_suggestions[0]["related_smart_row_id"], "smart-row:thread-2")
        self.assertEqual(json_loads(inserted_suggestions[0]["evidence_json"]), {"source": "test"})

    def test_replace_smart_projection_materializes_thread_titles_without_summary_cache(self) -> None:
        connection = FakeConnection()
        row = {
            "id": "smart-object:ticket-1",
            "row_key": "mail-object:ticket:1",
            "row_type": "verified_group",
            "title": "Northstar ticket 106756996",
            "summary": "Two emails are about the same Northstar ticket.",
            "source_thread_ids": ["thread-1", "thread-2"],
            "source_message_ids": ["msg-1", "msg-2"],
            "confidence_tier": "exact",
            "confidence": 1.0,
            "grouping_reason": {"source": "mail_object"},
            "action_type": "open",
        }

        with patch("app.db.mail_groups.get_engine", return_value=FakeEngine(connection)):
            replace_smart_inbox_projection(
                "postgresql://example/db",
                user_id="user-1",
                rows=[row],
                work_items=[],
            )

        summaries = [param for statement, param in connection.calls if "INSERT INTO thread_summaries" in statement]
        self.assertEqual(len(summaries), 2)
        self.assertEqual({summary["gmail_thread_id"] for summary in summaries}, {"thread-1", "thread-2"})
        self.assertEqual({summary["summary_level"] for summary in summaries}, {"group"})
        self.assertEqual({summary["ai_title"] for summary in summaries}, {"Northstar ticket 106756996"})
        self.assertEqual({summary["summary"] for summary in summaries}, {""})
        self.assertEqual(json_loads(summaries[0]["source_message_ids_json"]), ["msg-1", "msg-2"])
        self.assertEqual(json_loads(summaries[0]["evidence_json"])["smart_row_id"], "smart-object:ticket-1")
        self.assertEqual(summaries[0]["summary_version"], "smart-thread-title-v1")

    def test_gmail_message_upsert_materializes_message_signals(self) -> None:
        connection = FakeConnection()
        message = _gmail_message(
            extracted_signals={
                "sender_domain": "northstarbank.example",
                "normalized_subject": "registered service request",
                "ticket_id": "106756996",
                "trade_id": "202606029000072",
                "domains": ["northstarbank.example", "netbanking.northstarbank.example"],
                "list_id": "alerts.northstarbank.example",
                "in_reply_to": "<reply@example.com>",
                "references": "<root@example.com> <reply@example.com>",
            },
            headers={
                "message-id": "<msg@example.com>",
                "list-unsubscribe": "<https://northstarbank.example/unsubscribe>",
            },
        )

        with patch("app.db.mail_groups.get_engine", return_value=FakeEngine(connection)):
            count = upsert_gmail_messages("postgresql://example/db", [message])

        self.assertEqual(count, 1)
        signal_params = next(param for statement, param in connection.calls if "INSERT INTO message_signals" in statement)
        self.assertEqual(signal_params["id"], "message-signal:user-1:msg-1")
        self.assertEqual(signal_params["sender_domain"], "northstarbank.example")
        self.assertEqual(signal_params["normalized_subject"], "registered service request")
        self.assertEqual(
            json_loads(signal_params["reference_ids_json"]),
            {"ticket_id": "106756996", "trade_id": "202606029000072"},
        )
        self.assertEqual(json_loads(signal_params["links_json"]), [{"domain": "northstarbank.example"}, {"domain": "netbanking.northstarbank.example"}])
        self.assertIn("<root@example.com>", json_loads(signal_params["reply_refs_json"]))
        self.assertEqual(
            json_loads(signal_params["list_signals_json"]),
            {"list_id": "alerts.northstarbank.example", "list_unsubscribe": "<https://northstarbank.example/unsubscribe>"},
        )
        self.assertEqual(signal_params["extraction_version"], "message-signals-v1")

    def test_exact_reference_messages_materialize_shared_mail_object(self) -> None:
        connection = FakeConnection()
        first = _gmail_message(
            message_id="provider-1",
            sender="Provider Support <alerts@support.provider.example.com>",
            extracted_signals={
                "sender_domain": "support.provider.example.com",
                "normalized_subject": "registered service request",
                "ticket_id": "106756996",
            },
            headers={},
        )
        second = _gmail_message(
            message_id="provider-2",
            sender="Provider Care <alerts@mail.provider.example.com>",
            extracted_signals={
                "sender_domain": "mail.provider.example.com",
                "normalized_subject": "service request update",
                "ticket_id": "106756996",
            },
            headers={},
        )

        with patch("app.db.mail_groups.get_engine", return_value=FakeEngine(connection)):
            count = upsert_gmail_messages("postgresql://example/db", [first, second])

        self.assertEqual(count, 2)
        object_params = [param for statement, param in connection.calls if "INSERT INTO mail_objects" in statement]
        member_params = [param for statement, param in connection.calls if "INSERT INTO object_members" in statement]
        self.assertEqual(len(object_params), 2)
        self.assertEqual(len(member_params), 2)
        self.assertEqual({param["canonical_key"] for param in object_params}, {"ticket_id:provider:106756996"})
        self.assertEqual({param["object_id"] for param in member_params}, {object_params[0]["id"]})
        self.assertEqual({param["gmail_message_id"] for param in member_params}, {"provider-1", "provider-2"})
        self.assertEqual(member_params[0]["link_type"], "ticket_id")
        self.assertTrue(any("DELETE FROM object_members" in statement for statement, _param in connection.calls))

    def test_trade_id_messages_materialize_shared_cross_provider_mail_object(self) -> None:
        connection = FakeConnection()
        northstar = _gmail_message(
            message_id="northstar-trade",
            sender="Northstar FX Retail <northstarfx@northstar.example>",
            extracted_signals={
                "sender_domain": "northstar.example",
                "normalized_subject": "fx retail deal confirmation ccil reference number 202606029000072",
                "trade_id": "202606029000072",
            },
            headers={},
        )
        ccil = _gmail_message(
            message_id="ccil-trade",
            sender="FxNoReply@ccilindia.co.in",
            extracted_signals={
                "sender_domain": "ccilindia.co.in",
                "normalized_subject": "fx-retail trade confirmation",
                "trade_id": "202606029000072",
            },
            headers={},
        )

        with patch("app.db.mail_groups.get_engine", return_value=FakeEngine(connection)):
            count = upsert_gmail_messages("postgresql://example/db", [northstar, ccil])

        self.assertEqual(count, 2)
        object_params = [param for statement, param in connection.calls if "INSERT INTO mail_objects" in statement]
        member_params = [param for statement, param in connection.calls if "INSERT INTO object_members" in statement]
        self.assertEqual({param["canonical_key"] for param in object_params}, {"trade_id:fx-retail:202606029000072"})
        self.assertEqual({param["object_id"] for param in member_params}, {object_params[0]["id"]})
        self.assertEqual({param["gmail_message_id"] for param in member_params}, {"northstar-trade", "ccil-trade"})
        self.assertEqual(member_params[0]["link_type"], "trade_id")

    @patch("app.services.mail_groups.enqueue_job")
    def test_enqueue_app_session_snapshot_refresh_uses_deduped_default_queue(self, mock_enqueue: Mock) -> None:
        mock_enqueue.return_value = SimpleNamespace(id="job-1")
        settings = SimpleNamespace(database_path="postgresql://example/db")

        job_id = enqueue_app_session_snapshot_refresh(settings, user_id="user-1", priority=44)

        self.assertEqual(job_id, "job-1")
        self.assertEqual(mock_enqueue.call_args.kwargs["kind"], "app_session_snapshot_refresh")
        self.assertEqual(mock_enqueue.call_args.kwargs["queue"], "default")
        self.assertEqual(mock_enqueue.call_args.kwargs["dedupe_key"], "app-session-snapshot:user-1")
        self.assertEqual(mock_enqueue.call_args.kwargs["priority"], 44)

    @patch("app.services.mail_groups.enqueue_job")
    def test_first_run_sync_targets_first_ready_300_messages_and_30_day_hot_window(self, mock_enqueue: Mock) -> None:
        mock_enqueue.return_value = SimpleNamespace(id="job-1")
        settings = SimpleNamespace(database_path="postgresql://example/db")

        job_id = enqueue_first_run(settings, user_id="user-1")

        self.assertEqual(job_id, "job-1")
        self.assertEqual(FIRST_BATCH_SIZE, SMART_FIRST_READY_TARGET_MESSAGES)
        self.assertEqual(IMPORTER_FIRST_BATCH_SIZE, SMART_FIRST_READY_TARGET_MESSAGES)
        self.assertEqual(FIRST_RUN_RECENT_DAYS, 30)
        self.assertIsNone(FIRST_RUN_RECENT_MAX_MESSAGES)
        self.assertEqual(SMART_HOT_WINDOW_MESSAGE_CAP, 0)
        self.assertEqual(SMART_HOT_WINDOW_THREAD_CAP, 0)
        self.assertEqual(APP_SESSION_MAILBOX_LIMIT, SMART_INBOX_DISPLAY_THREAD_LIMIT)
        self.assertEqual(SmartReadinessResponse().hot_window_message_cap, SMART_HOT_WINDOW_MESSAGE_CAP)
        self.assertEqual(mock_enqueue.call_args.kwargs["payload"]["batch_size"], 300)

    def test_recent_visible_days_cannot_exceed_smart_hot_window(self) -> None:
        self.assertEqual(_recent_visible_days(SimpleNamespace(gmail_recent_days=90)), SMART_HOT_WINDOW_DAYS)
        self.assertEqual(_recent_visible_days(SimpleNamespace(gmail_recent_days=7)), 7)

    @patch("app.workers.main.emit_mailbox_event")
    @patch("app.workers.main.refresh_app_session_snapshot")
    def test_worker_runs_app_session_snapshot_refresh(self, mock_refresh: Mock, mock_emit: Mock) -> None:
        settings = SimpleNamespace(database_path="postgresql://example/db")
        job = SimpleNamespace(
            payload_version=1,
            kind="app_session_snapshot_refresh",
            payload={"user_id": "user-1"},
            user_id="user-1",
        )

        _run_job(settings, job)

        mock_refresh.assert_called_once_with(settings, user_id="user-1")
        self.assertEqual(mock_emit.call_args.kwargs["payload"], {"source": "app_session_snapshot_refresh"})


def _thread_row(
    *,
    thread_id: str,
    message_count: int,
    ai_group_id: str | None,
    presentation_status: str,
    children: list[tuple[str, str]],
) -> GmailThreadRow:
    return GmailThreadRow(
        thread_id=thread_id,
        entity_id=ai_group_id or thread_id,
        title=f"Title {thread_id}",
        href=f"/v1/mailbox/threads/{thread_id}",
        latest_source_record_id=f"latest-{thread_id}",
        latest_received_at="2026-06-11T10:00:00+00:00",
        latest_message_at="2026-06-11T10:00:00+00:00",
        latest_subject=f"Subject {thread_id}",
        latest_sender="Sender <sender@example.com>",
        sender="Sender",
        participants=["Sender"],
        message_count=message_count,
        summary=f"Summary {thread_id}",
        ai_group_id=ai_group_id,
        ai_title=f"AI title {thread_id}" if ai_group_id else None,
        ai_summary=f"AI summary {thread_id}" if ai_group_id else None,
        snippet=f"Snippet {thread_id}",
        label_ids=["INBOX"],
        labels=["INBOX"],
        action_needed=ai_group_id is not None,
        action_type="reply" if ai_group_id else "none",
        action_type_key="reply" if ai_group_id else "none",
        priority=80 if ai_group_id else 0,
        dashboard_visible=ai_group_id is not None,
        current_state="open",
        lifecycle_state="active",
        children=[
            {
                "message_id": message_id,
                "gmail_thread_id": gmail_thread_id,
                "sender": "Sender",
                "subject": "Child subject",
                "snippet": "Child snippet",
                "received_at": "2026-06-11T10:00:00+00:00",
            }
            for message_id, gmail_thread_id in children
        ],
        enrichment_status="ready" if presentation_status == "ai_ready" else "pending",
        presentation_status=presentation_status,  # type: ignore[arg-type]
    )


def _validated_thread_row(row: GmailThreadRow) -> GmailThreadRow:
    return GmailThreadRow.model_validate(dict(row.__dict__))


def _smart_inbox_generated_seed_values() -> tuple[int, ...]:
    raw_seed = os.getenv("SMART_INBOX_GENERATED_TEST_SEED", "").strip()
    seeds = list(SMART_INBOX_GENERATED_BASE_SEEDS)
    if raw_seed:
        try:
            seeds.append(int(raw_seed))
        except ValueError:
            seeds.append(sum((index + 1) * ord(character) for index, character in enumerate(raw_seed)))
    if os.getenv("SMART_INBOX_RANDOMIZED_TESTS", "1").strip().lower() not in {"0", "false", "no"}:
        seeds.append(random.SystemRandom().randrange(1, 1_000_000))
    return tuple(dict.fromkeys(seeds))


def _generated_provider(rng: random.Random, index: int) -> dict[str, str]:
    root = rng.choice(SMART_INBOX_PROVIDER_ROOTS)
    suffix = rng.choice(["", "app", "hq", "cloud", "mail"])
    unique_root = f"{root}{index}" if suffix == "" else f"{root}{index}{suffix}"
    display_words = _split_generated_provider_root(root)
    display = " ".join(word.capitalize() for word in display_words)
    return {
        "root": unique_root,
        "display": display,
        "domain": f"{unique_root}.example",
        "sender": f"{display} <updates@{unique_root}.example>",
    }


def _split_generated_provider_root(root: str) -> list[str]:
    known_suffixes = ["ledger", "cargo", "cloud", "pay", "desk", "loop", "market", "mint", "works", "rail"]
    for suffix in known_suffixes:
        if root.endswith(suffix) and len(root) > len(suffix):
            return [root[: -len(suffix)], suffix]
    return [root]


def _generated_reference_value(rng: random.Random, index: int, offset: int) -> str:
    prefix = rng.choice(["SR", "INV", "TRK", "APP", "ORD", "BK"])
    return f"{prefix}{index + 1:02d}{offset + 1}{rng.randrange(1000, 9999)}"


def _generated_provider_message(
    provider: dict[str, str],
    *,
    message_id: str,
    subject_topic: str,
    snippet: str | None = None,
    labels: list[str] | None = None,
    extracted_signals: dict[str, object] | None = None,
    ai_title: str | None = None,
    internal_date: str = "2026-06-11T10:00:00+00:00",
) -> GmailMessageRecord:
    return _gmail_message(
        message_id=message_id,
        gmail_thread_id=f"thread-{message_id}",
        sender=provider["sender"],
        subject=f"{provider['display']} - {subject_topic}",
        snippet=snippet or subject_topic,
        ai_title=ai_title,
        extracted_signals=extracted_signals or {"sender_domain": provider["domain"], "normalized_subject": subject_topic.lower()},
        headers={},
        label_ids=labels or ["INBOX"],
        internal_date=internal_date,
    )


def _mailbox_display_entry(message: GmailMessageRecord) -> MailboxDisplayClusterEntry:
    row = _thread_row(
        thread_id=message.gmail_thread_id or message.message_id,
        message_count=1,
        ai_group_id=None,
        presentation_status="fallback",
        children=[(message.message_id, message.gmail_thread_id or message.message_id)],
    ).model_copy(
        update={
            "latest_source_record_id": message.message_id,
            "latest_received_at": message.internal_date or message.updated_at,
            "latest_message_at": message.internal_date or message.updated_at,
            "latest_subject": message.subject,
            "latest_sender": message.sender,
            "sender": message.sender,
            "title": message.subject,
            "summary": message.snippet,
            "snippet": message.snippet,
            "label_ids": message.label_ids,
            "labels": message.label_ids,
            "grouping_metadata": {},
        }
    )
    return MailboxDisplayClusterEntry(
        thread_id=message.gmail_thread_id or message.message_id,
        messages=[message],
        row=row,
        cluster_key=_mailbox_display_cluster_key(
            thread_id=message.gmail_thread_id or message.message_id,
            messages=[message],
            mailbox_label="inbox",
        ),
    )


def _smart_row(
    *,
    row_id: str,
    row_key: str,
    title: str,
    summary: str,
    primary_sender: str,
    source_message_ids: list[str] | None = None,
    row_type: str = "summarized_thread",
    confidence_tier: str = "strong",
    action_type: str = "open",
    priority: int = 30,
) -> SmartInboxRow:
    return SmartInboxRow(
        id=row_id,
        row_key=row_key,
        row_type=row_type,  # type: ignore[arg-type]
        title=title,
        summary=summary,
        primary_sender=primary_sender,
        latest_message_at="2026-06-11T10:00:00+00:00",
        latest_message_id=f"latest-{row_key}",
        source_thread_ids=[row_key],
        source_message_ids=source_message_ids or [f"latest-{row_key}"],
        confidence_tier=confidence_tier,  # type: ignore[arg-type]
        confidence=0.82,
        grouping_reason={"source": "test"},
        offline_status="partial",
        readiness="ready",
        action_type=action_type,
        priority=priority,
    )


def _smart_row_with_workflow_object(row: SmartInboxRow, *, value: str, object_type: str) -> SmartInboxRow:
    grouping_reason = dict(row.grouping_reason)
    grouping_reason["workflow_object"] = {"type": object_type, "value": value}
    return row.model_copy(update={"grouping_reason": grouping_reason})


def _attention_item(
    item_id: str,
    *,
    need_type: str,
    action_type: str,
    current_state: str,
) -> AttentionItem:
    return AttentionItem(
        id=item_id,
        entity_id=f"entity-{item_id}",
        user_id="user-1",
        need_type=need_type,  # type: ignore[arg-type]
        action_type=action_type,  # type: ignore[arg-type]
        effort_level="quick",
        timing_band="now",
        action_confidence="high",
        primary_action="open",
        fallback_action="open",
        title=f"Item {item_id}",
        why_this_is_here=f"Reason {item_id}",
        due_at=None,
        importance_level="high",
        lifecycle_state="active",
        current_state=current_state,  # type: ignore[arg-type]
        source="gmail",
        gmail_thread_id=f"thread-{item_id}",
        gmail_thread_action=None,
        trace_id=f"trace-{item_id}",
        created_at="2026-06-11T10:00:00+00:00",
    )


def _gmail_message(
    *,
    extracted_signals: dict[str, object],
    headers: dict[str, str],
    message_id: str = "msg-1",
    gmail_thread_id: str = "thread-1",
    sender: str = "Northstar Bank <alerts@northstarbank.example>",
    subject: str = "Registered service request",
    snippet: str = "Reference number 106756996",
    label_ids: list[str] | None = None,
    internal_date: str = "2026-06-11T10:00:00+00:00",
    text_body: str | None = None,
    ai_title: str | None = None,
    html_render_document: str | None = None,
    body_fetch_status: str | None = None,
    recipients: dict[str, str] | None = None,
) -> GmailMessageRecord:
    return GmailMessageRecord(
        user_id="user-1",
        message_id=message_id,
        gmail_thread_id=gmail_thread_id,
        history_id="10",
        label_ids=label_ids or ["INBOX"],
        internal_date=internal_date,
        subject=subject,
        sender=sender,
        recipients=recipients or {"to": "me@example.com"},
        headers=headers,
        snippet=snippet,
        raw_payload={},
        html_body_sanitized=None,
        html_render_document=html_render_document,
        text_body=text_body,
        extracted_signals=extracted_signals,
        body_hash="body-hash-1",
        created_at="",
        updated_at="",
        body_fetch_status=body_fetch_status,
        ai_title=ai_title,
    )


def json_loads(value: object) -> object:
    return json.loads(str(value))


class FakeResult:
    pass


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement, params=None) -> FakeResult:
        self.calls.append((str(statement), dict(params or {})))
        return FakeResult()


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def begin(self) -> FakeConnection:
        return self.connection


if __name__ == "__main__":
    unittest.main()
