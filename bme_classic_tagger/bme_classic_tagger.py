#!/usr/bin/env python3
"""
BME Classic Customer Tagger
Tags NXG customers in ChartMogul with the bme_classic custom attribute.

For each customer in ChartMogul where bme_classic is blank:
  - Sets bme_classic = true  if their email matches the BME Classic customer list
  - Sets bme_classic = false if their email does not match

The BME Classic customer list is a static snapshot of active, paying BME Classic
customers as of January 30, 2026, stored in bme_customers.csv.

Uses the POST /v1/customers/{uuid}/attributes/custom endpoint to create the
bme_classic attribute on each customer record. POST is required (not PUT) because
the attribute does not exist on customer records until explicitly set.

Required environment variables:
  CHARTMOGUL_API_KEY_RW  - ChartMogul API key with read-write access
  SLACK_BOT_TOKEN        - Slack Bot token for DM notifications

Usage:
  # Normal run — tags all untagged customers in ChartMogul (live)
  python3 bme_classic_tagger.py

  # Dry run — shows what would happen for all untagged customers, no changes made
  python3 bme_classic_tagger.py --dry-run

  # Test email — shows what would happen for one customer, no changes made
  python3 bme_classic_tagger.py --test-email customer@example.com

  # Live email — real live run for one specific customer only
  python3 bme_classic_tagger.py --live-email customer@example.com
"""

import os
import sys
import csv
import json
import time
import urllib.request
import urllib.error
import urllib.parse
import base64
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────

CHARTMOGUL_API_KEY_RW = os.environ.get("CHARTMOGUL_API_KEY_RW", "")
SLACK_BOT_TOKEN       = os.environ.get("SLACK_BOT_TOKEN", "")

# Slack member ID to receive direct message notifications
SLACK_DM_USER_ID      = "U03BRPGNUG6"

# Path to the BME Classic customer list, relative to this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH   = os.path.join(SCRIPT_DIR, "bme_customers.csv")

# ChartMogul API base URL
API_BASE   = "https://api.chartmogul.com/v1"

# Number of customers to fetch per page (200 is the maximum)
PAGE_SIZE  = 200


# ── ChartMogul API helpers ────────────────────────────────────────────────────

def _get_credentials() -> str:
    """Returns the Base64-encoded credentials for ChartMogul Basic Auth."""
    return base64.b64encode(f"{CHARTMOGUL_API_KEY_RW}:".encode()).decode()


def _chartmogul_get(url: str) -> dict:
    """Makes an authenticated GET request to the ChartMogul API and returns parsed JSON."""
    credentials = _get_credentials()
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ChartMogul GET error {e.code}: {e.read().decode()}") from e


def _chartmogul_post(url: str, payload: dict) -> dict:
    """
    Makes an authenticated POST request to the ChartMogul API and returns parsed JSON.
    Uses the Add Custom Attributes endpoint, which creates the attribute on the customer
    record. POST is required for customers who have never had the attribute set — the
    attribute does not exist on their record until explicitly created via this call.
    """
    credentials = _get_credentials()
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ChartMogul POST error {e.code}: {e.read().decode()}") from e


# ── Slack helper ──────────────────────────────────────────────────────────────

def _send_slack_dm(message: str) -> None:
    """Sends a direct message to the configured Slack user."""
    if not SLACK_BOT_TOKEN:
        print("  (Slack notification skipped — SLACK_BOT_TOKEN not set)")
        return

    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = json.dumps({
        "channel": SLACK_DM_USER_ID,
        "text": message,
    }).encode()

    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            resp = json.loads(r.read().decode())
        if not resp.get("ok"):
            print(f"  (Slack notification failed: {resp.get('error')})", file=sys.stderr)
    except Exception as e:
        print(f"  (Slack notification error: {e})", file=sys.stderr)


# ── Core logic ────────────────────────────────────────────────────────────────

def load_bme_emails(csv_path: str) -> set:
    """
    Loads BME Classic customer email addresses from the CSV file into a set
    for fast lookups. Emails are lowercased for case-insensitive matching.
    """
    emails = set()
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = row.get("Email", "").strip().lower()
            if email:
                emails.add(email)
    return emails


def fetch_customer_by_email(email: str) -> dict | None:
    """
    Looks up a single ChartMogul customer by email address.
    Returns the customer dict if found, or None if not found.
    """
    url = f"{API_BASE}/customers?email={urllib.parse.quote(email)}&per_page=1"
    data = _chartmogul_get(url)
    entries = data.get("entries", [])
    return entries[0] if entries else None


def fetch_untagged_customers() -> list:
    """
    Fetches all ChartMogul customers where bme_classic is blank by paging
    through the List Customers endpoint and filtering in code.
    Returns a list of dicts, each containing the customer's uuid and email.
    """
    untagged = []
    url = f"{API_BASE}/customers?per_page={PAGE_SIZE}"
    page = 1

    while True:
        print(f"  Fetching page {page}...")
        data = _chartmogul_get(url)
        entries = data.get("entries", [])

        for customer in entries:
            custom = customer.get("attributes", {}).get("custom", {})
            # bme_classic is blank if the key is absent or the value is None
            if "bme_classic" not in custom or custom["bme_classic"] is None:
                uuid  = customer.get("uuid", "")
                email = customer.get("email", "").strip().lower()
                if uuid and email:
                    untagged.append({"uuid": uuid, "email": email})

        if not data.get("has_more", False):
            break

        url = f"{API_BASE}/customers?per_page={PAGE_SIZE}&cursor={data['cursor']}"
        page += 1

    return untagged


def tag_customer(uuid: str, value: bool) -> None:
    """
    Creates the bme_classic custom attribute on a ChartMogul customer using POST.
    Only the bme_classic field is included in the payload — no other fields
    or attributes are affected.
    """
    url = f"{API_BASE}/customers/{uuid}/attributes/custom"
    payload = {
        "custom": [
            {"type": "Boolean", "key": "bme_classic", "value": value}
        ]
    }
    _chartmogul_post(url, payload)


def print_summary_and_notify(
    mode_label: str,
    total_untagged: int,
    tagged_true: int,
    tagged_false: int,
    errors: int,
    details: list,
    start_time: datetime,
    notify_slack: bool,
) -> None:
    """Prints the run summary and optionally sends a Slack DM."""
    elapsed = (datetime.now() - start_time).seconds

    print()
    print("─" * 50)
    print(f"{mode_label}. Processed {total_untagged:,} customer(s) in {elapsed}s:")
    print(f"  → {tagged_true:,}  set to TRUE  (BME Classic)")
    print(f"  → {tagged_false:,}  set to FALSE (New)")
    if errors:
        print(f"  → {errors:,}  errors (see above)")
    print("─" * 50)

    if not notify_slack or not SLACK_BOT_TOKEN:
        return

    print()
    print("Sending Slack notification...")

    # Build customer detail lines (cap at 50 to keep message readable)
    detail_lines = []
    for email, value in details[:50]:
        detail_lines.append(f"  • {email} → {value}")
    if len(details) > 50:
        detail_lines.append(f"  • ... and {len(details) - 50} more (see full log)")

    # Build the GitHub Actions run URL if available
    run_url = ""
    github_server = os.environ.get("GITHUB_SERVER_URL", "")
    github_repo   = os.environ.get("GITHUB_REPOSITORY", "")
    github_run_id = os.environ.get("GITHUB_RUN_ID", "")
    if github_server and github_repo and github_run_id:
        run_url = f"\n\n<{github_server}/{github_repo}/actions/runs/{github_run_id}|View full log in GitHub Actions>"

    status = "✅ Completed successfully" if not errors else f"⚠️ Completed with {errors} error(s)"
    message = (
        f"*BME Classic Tagger — {start_time.strftime('%B %d, %Y')}*\n"
        f"{status}\n\n"
        f"*Summary:*\n"
        f"  • {total_untagged:,} customer(s) evaluated\n"
        f"  • {tagged_true:,} tagged TRUE (BME Classic)\n"
        f"  • {tagged_false:,} tagged FALSE (New)\n"
        f"  • Completed in {elapsed}s\n\n"
        f"*Customers updated:*\n"
        f"{chr(10).join(detail_lines)}"
        f"{run_url}"
    )

    _send_slack_dm(message)
    print("  → Slack notification sent!")


# ── Run modes ─────────────────────────────────────────────────────────────────

def run_test_email(email: str, bme_emails: set) -> None:
    """
    Tests a single email address — looks up the customer in ChartMogul,
    checks against the CSV, and shows what would happen. No changes made.
    """
    print(f"⚠️  TEST EMAIL MODE — no changes will be made to ChartMogul.")
    print(f"  Testing email: {email}")
    print()

    email_lower = email.strip().lower()
    in_csv = email_lower in bme_emails
    print(f"  CSV match:     {'✓ YES — would be tagged TRUE' if in_csv else '✗ NO  — would be tagged FALSE'}")

    print(f"  Looking up customer in ChartMogul...")
    customer = fetch_customer_by_email(email_lower)

    if not customer:
        print(f"  ChartMogul:    ✗ No customer found with this email")
        return

    uuid          = customer.get("uuid", "")
    custom        = customer.get("attributes", {}).get("custom", {})
    current_value = custom.get("bme_classic", "BLANK")

    print(f"  ChartMogul:    ✓ Customer found (UUID: {uuid})")
    print(f"  Current value: bme_classic = {current_value}")
    print()

    if current_value != "BLANK" and current_value is not None:
        print(f"  Result: This customer is already tagged — no action would be taken.")
    else:
        new_value = "TRUE" if in_csv else "FALSE"
        print(f"  Result: bme_classic would be set to {new_value}")


def run_live_email(email: str, bme_emails: set, start_time: datetime) -> None:
    """
    Live run for a single specific email address — looks up the customer
    in ChartMogul, checks against the CSV, and makes the real update.
    Sends a Slack DM with the result.
    """
    print(f"🔴 LIVE EMAIL MODE — this will make a real change in ChartMogul.")
    print(f"  Processing email: {email}")
    print()

    email_lower = email.strip().lower()
    in_csv = email_lower in bme_emails
    print(f"  CSV match:     {'✓ YES — will be tagged TRUE' if in_csv else '✗ NO  — will be tagged FALSE'}")

    print(f"  Looking up customer in ChartMogul...")
    customer = fetch_customer_by_email(email_lower)

    if not customer:
        print(f"  ChartMogul:    ✗ No customer found with this email — nothing to do.")
        return

    uuid          = customer.get("uuid", "")
    custom        = customer.get("attributes", {}).get("custom", {})
    current_value = custom.get("bme_classic", "BLANK")

    print(f"  ChartMogul:    ✓ Customer found (UUID: {uuid})")
    print(f"  Current value: bme_classic = {current_value}")

    if current_value != "BLANK" and current_value is not None:
        print(f"  Result: This customer is already tagged — no action taken.")
        return

    # Make the live update
    value = in_csv
    print(f"  Updating bme_classic to {'TRUE' if value else 'FALSE'}...")
    try:
        tag_customer(uuid, value)
        print(f"  ✓ Successfully updated in ChartMogul.")
        print(f"  Please verify in the ChartMogul UI that bme_classic = {'TRUE' if value else 'FALSE'} for {email}")

        print_summary_and_notify(
            mode_label="Live email run complete",
            total_untagged=1,
            tagged_true=1 if value else 0,
            tagged_false=0 if value else 1,
            errors=0,
            details=[(email, "TRUE" if value else "FALSE")],
            start_time=start_time,
            notify_slack=True,
        )
    except RuntimeError as e:
        print(f"  ✗ Error updating customer: {e}", file=sys.stderr)


def run_full(dry_run: bool, bme_emails: set, start_time: datetime) -> None:
    """
    Runs the full tagging process — fetches all untagged customers and tags them.
    In dry run mode, shows what would happen without making any changes.
    Sends a Slack DM with the results (live runs only).
    """
    print("Fetching untagged customers from ChartMogul...")
    untagged = fetch_untagged_customers()
    print(f"  → {len(untagged):,} customers with blank bme_classic found")
    print()

    if not untagged:
        print("Nothing to do — all customers are already tagged.")
        return

    action = "Would tag" if dry_run else "Tagging"
    print(f"{action} customers...")
    tagged_true  = 0
    tagged_false = 0
    errors       = 0
    details      = []

    for customer in untagged:
        uuid  = customer["uuid"]
        email = customer["email"]
        value = email in bme_emails

        try:
            if not dry_run:
                tag_customer(uuid, value)
                time.sleep(0.05)  # Small delay to be respectful of API rate limits
            if value:
                tagged_true += 1
                label = "TRUE "
            else:
                tagged_false += 1
                label = "FALSE"
            print(f"  → {label}  {email}")
            details.append((email, "TRUE" if value else "FALSE"))
        except RuntimeError as e:
            errors += 1
            print(f"  → ERROR  {email}: {e}", file=sys.stderr)
            details.append((email, f"ERROR: {e}"))

    mode_label = "DRY RUN complete — no changes made" if dry_run else "Complete"
    print_summary_and_notify(
        mode_label=mode_label,
        total_untagged=len(untagged),
        tagged_true=tagged_true,
        tagged_false=tagged_false,
        errors=errors,
        details=details,
        start_time=start_time,
        notify_slack=not dry_run,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Parse arguments
    dry_run    = "--dry-run"    in sys.argv
    test_email = None
    live_email = None

    if "--test-email" in sys.argv:
        idx = sys.argv.index("--test-email")
        if idx + 1 < len(sys.argv):
            test_email = sys.argv[idx + 1]
        else:
            print("ERROR: --test-email requires an email address argument", file=sys.stderr)
            sys.exit(1)

    if "--live-email" in sys.argv:
        idx = sys.argv.index("--live-email")
        if idx + 1 < len(sys.argv):
            live_email = sys.argv[idx + 1]
        else:
            print("ERROR: --live-email requires an email address argument", file=sys.stderr)
            sys.exit(1)

    # Validate environment
    if not CHARTMOGUL_API_KEY_RW:
        print("ERROR: Missing environment variable: CHARTMOGUL_API_KEY_RW", file=sys.stderr)
        sys.exit(1)

    # Validate CSV exists
    if not os.path.exists(CSV_PATH):
        print(f"ERROR: BME Classic customer list not found at: {CSV_PATH}", file=sys.stderr)
        sys.exit(1)

    start_time = datetime.now()
    print(f"BME Classic Tagger started at {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    if dry_run:
        print("⚠️  DRY RUN MODE — no changes will be made to ChartMogul.")
    print()

    # Load BME Classic emails from CSV
    print("Loading BME Classic customer list from CSV...")
    bme_emails = load_bme_emails(CSV_PATH)
    print(f"  → {len(bme_emails):,} BME Classic emails loaded")
    print()

    # ── Route to the appropriate run mode ──
    if test_email:
        run_test_email(test_email, bme_emails)
    elif live_email:
        run_live_email(live_email, bme_emails, start_time)
    else:
        run_full(dry_run, bme_emails, start_time)


if __name__ == "__main__":
    main()
