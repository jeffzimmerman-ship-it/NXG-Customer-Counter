#!/usr/bin/env python3
"""
ChartMogul → Slack Daily Visual Reporter
Generates a bold image of the paying customer count and uploads it to Slack.

Required environment variables:
  CHARTMOGUL_API_KEY   - Your ChartMogul API key (Settings > API Keys)
  SLACK_BOT_TOKEN      - Slack Bot token (xoxb-...) with files:write + channels:join scope
  SLACK_CHANNEL_ID     - The Slack channel ID to post to (e.g. C012AB3CD)

Install dependency:
  pip install Pillow
"""

import os
import sys
import json
import urllib.request
import urllib.error
import base64
import io
from datetime import date

# ── Config ────────────────────────────────────────────────────────────────────

CHARTMOGUL_API_KEY = os.environ.get("CHARTMOGUL_API_KEY", "")
SLACK_BOT_TOKEN    = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID   = os.environ.get("SLACK_CHANNEL_ID", "")

IMAGE_WIDTH  = 900
IMAGE_HEIGHT = 420
ACCENT_COLOR  = "#751165"  # Left bar + label + date text
NUMBER_COLOR  = "#3C247F"  # Large customer count
BG_COLOR      = "#FEF9F2"  # Background
GRID_COLOR    = "#F5EEE3"  # Subtle grid lines
DIVIDER_COLOR = "#E8D9C5"  # Bottom divider line


# ── ChartMogul ────────────────────────────────────────────────────────────────

def get_paying_customers() -> int:
    """
    Fetches paying customers from ChartMogul using the /v1/metrics/customer-count endpoint,
    filtered to only include customers with MRR > 0. This matches the Paid Subscribers
    figure shown in the ChartMogul UI.
    """
    today = date.today().isoformat()
    url = (
        f"https://api.chartmogul.com/v1/metrics/customer-count"
        f"?start-date={today}&end-date={today}&interval=day&filters=mrr~GT~0"
    )
    credentials = base64.b64encode(f"{CHARTMOGUL_API_KEY}:".encode()).decode()
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ChartMogul API error {e.code}: {e.read().decode()}") from e

    entries = data.get("entries", [])
    if not entries:
        raise ValueError(f"No ChartMogul data returned for {today}.")
    return entries[-1]["customers"]


# ── Image generation ──────────────────────────────────────────────────────────

def hex_to_rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def generate_image(customer_count: int) -> bytes:
    """Renders a dark-themed dashboard card image and returns PNG bytes."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise RuntimeError("Pillow is not installed. Run: pip install Pillow")

    W, H = IMAGE_WIDTH, IMAGE_HEIGHT
    today_str = date.today().strftime("%B %d, %Y")

    img = Image.new("RGB", (W, H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Subtle grid texture
    for x in range(0, W, 40):
        draw.line([(x, 0), (x, H)], fill=GRID_COLOR, width=1)
    for y in range(0, H, 40):
        draw.line([(0, y), (W, y)], fill=GRID_COLOR, width=1)

    # Accent bar on left edge
    draw.rectangle([0, 0, 5, H], fill=ACCENT_COLOR)

    # Font paths — falls back gracefully across OS
    font_paths = {
        "bold": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "C:/Windows/Fonts/arialbd.ttf",
        ],
        "regular": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "C:/Windows/Fonts/arial.ttf",
        ],
    }

    def load_font(style, size):
        for path in font_paths[style]:
            try:
                return ImageFont.truetype(path, size)
            except (IOError, OSError):
                continue
        return ImageFont.load_default()

    font_big   = load_font("bold",    180)
    font_label = load_font("regular",  32)
    font_date  = load_font("regular",  22)

    # "PAYING CUSTOMERS" label
    label = "PAYING CUSTOMERS"
    lw = draw.textlength(label, font=font_label)
    draw.text(((W - lw) / 2, 55), label, font=font_label, fill=ACCENT_COLOR)

    # Large number with glow layers
    num_str = f"{customer_count:,}"
    nw = draw.textlength(num_str, font=font_big)
    nx = (W - nw) / 2
    ny = 110

    for offset, color in [(6, "#0D1A3A"), (4, "#152444"), (2, "#1E3366")]:
        draw.text((nx + offset, ny + offset), num_str, font=font_big, fill=color)
        draw.text((nx - offset, ny - offset), num_str, font=font_big, fill=color)
    draw.text((nx, ny), num_str, font=font_big, fill=NUMBER_COLOR)

    # Date
    dw = draw.textlength(today_str, font=font_date)
    draw.text(((W - dw) / 2, 320), today_str, font=font_date, fill=ACCENT_COLOR)

    # Bottom divider
    draw.rectangle([60, 360, W - 60, 361], fill=DIVIDER_COLOR)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── Slack ─────────────────────────────────────────────────────────────────────

def upload_image_to_slack(image_bytes: bytes) -> None:
    """
    Uploads image to Slack using the files.getUploadURLExternal API
    (required since March 2025 — the old files.upload is deprecated).
    """
    filename = f"paying-customers-{date.today().isoformat()}.png"
    today_str = date.today().strftime("%B %d, %Y")

    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }

    # Step 1: Get upload URL
    step1_payload = json.dumps({
        "filename": filename,
        "length": len(image_bytes),
    }).encode()

    req = urllib.request.Request(
        "https://slack.com/api/files.getUploadURLExternal",
        data=step1_payload,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        resp1 = json.loads(r.read().decode())

    if not resp1.get("ok"):
        raise RuntimeError(f"Slack getUploadURLExternal failed: {resp1.get('error')}")

    upload_url = resp1["upload_url"]
    file_id    = resp1["file_id"]

    # Step 2: Upload the file bytes
    req2 = urllib.request.Request(upload_url, data=image_bytes, method="POST")
    req2.add_header("Content-Type", "image/png")
    with urllib.request.urlopen(req2) as r:
        _ = r.read()

    # Step 3: Complete upload and share to channel
    step3_payload = json.dumps({
        "files": [{"id": file_id, "title": f"Paying Customers — {today_str}"}],
        "channel_id": SLACK_CHANNEL_ID,
        "initial_comment": f"👥 Daily customer update for *{today_str}*",
    }).encode()

    req3 = urllib.request.Request(
        "https://slack.com/api/files.completeUploadExternal",
        data=step3_payload,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req3) as r:
        resp3 = json.loads(r.read().decode())

    if not resp3.get("ok"):
        raise RuntimeError(f"Slack completeUploadExternal failed: {resp3.get('error')}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    errors = []
    if not CHARTMOGUL_API_KEY: errors.append("CHARTMOGUL_API_KEY")
    if not SLACK_BOT_TOKEN:    errors.append("SLACK_BOT_TOKEN")
    if not SLACK_CHANNEL_ID:   errors.append("SLACK_CHANNEL_ID")
    if errors:
        print(f"ERROR: Missing environment variables: {', '.join(errors)}", file=sys.stderr)
        sys.exit(1)

    print("Fetching paying customer count from ChartMogul...")
    count = get_paying_customers()
    print(f"  → {count:,} paying customers")

    print("Generating image...")
    image_bytes = generate_image(count)
    print(f"  → Image generated ({len(image_bytes) / 1024:.1f} KB)")

    print("Uploading image to Slack...")
    upload_image_to_slack(image_bytes)
    print("  → Posted successfully!")


if __name__ == "__main__":
    main()
