"""
Outlook inbox monitor — runs in a background thread.
Polls for new Sales Quote emails and processes them.
"""

import threading
import time
import logging
import re

log = logging.getLogger("cadre.monitor")


def _monitor_loop(email_addr, password, api_key, model, output_xlsx, poll_interval, processed_ids, stop_event, log_fn, status_fn):
    try:
        from exchangelib import Credentials, Account, DELEGATE, Configuration, Q
    except ImportError:
        log_fn("SYSTEM", "error", "exchangelib not installed. Run: pip install exchangelib")
        status_fn("stopped")
        return

    try:
        from utils.extractor import process_msg_bytes
        from utils.excel_io import append_rows, quote_exists

        credentials = Credentials(email_addr, password)
        config = Configuration(server="outlook.office365.com", credentials=credentials)
        account = Account(
            primary_smtp_address=email_addr,
            config=config,
            autodiscover=False,
            access_type=DELEGATE,
        )
        log_fn("SYSTEM", "success", f"Connected to {email_addr}")
        status_fn("running")
    except Exception as e:
        log_fn("SYSTEM", "error", f"Connection failed: {e}")
        status_fn("stopped")
        return

    while not stop_event.is_set():
        try:
            emails = account.inbox.filter(
                Q(is_read=False) & Q(subject__icontains="Sales Quote")
            ).order_by("-datetime_received")[:50]

            for email in emails:
                if email.id in processed_ids:
                    continue

                # Extract quote number from subject
                m = re.search(r"(\d{5,})", email.subject or "")
                q_num = m.group(1) if m else "UNKNOWN"

                if quote_exists(q_num, output_xlsx):
                    log_fn(q_num, "skipped", f"Quote {q_num} already in spreadsheet")
                    processed_ids.add(email.id)
                    continue

                log_fn(q_num, "processing", f"Processing: {email.subject}")

                for attachment in (email.attachments or []):
                    name = (attachment.name or "").lower()
                    if name.endswith(".msg") or name.endswith(".pdf"):
                        try:
                            result = process_msg_bytes(attachment.content, api_key, model)
                            if result["issues"]:
                                log_fn(q_num, "warning", f"Issues: {'; '.join(result['issues'])}")
                            if result["rows"]:
                                count = append_rows(result["rows"], output_xlsx)
                                log_fn(q_num, "success", f"Saved {count} rows for quote {q_num}")
                        except Exception as e:
                            log_fn(q_num, "error", f"Failed: {e}")

                email.is_read = True
                email.save()
                processed_ids.add(email.id)

        except Exception as e:
            log_fn("SYSTEM", "error", f"Poll error: {e}")

        stop_event.wait(poll_interval)

    status_fn("stopped")
    log_fn("SYSTEM", "info", "Monitor stopped")


def start_monitor(email_addr, password, api_key, model, output_xlsx, poll_interval, processed_ids, log_fn, status_fn):
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_monitor_loop,
        args=(email_addr, password, api_key, model, output_xlsx, poll_interval, processed_ids, stop_event, log_fn, status_fn),
        daemon=True,
    )
    thread.start()
    return thread, stop_event


def stop_monitor(stop_event: threading.Event):
    if stop_event:
        stop_event.set()
