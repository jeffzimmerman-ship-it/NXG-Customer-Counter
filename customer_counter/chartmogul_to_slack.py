#!/usr/bin/env python3
"""
ChartMogul → Slack Daily Visual Reporter
Generates a bold image of the paying customer count and uploads it to Slack.
Includes a breakdown of "New" vs "BME Classic" paying customers.

Required environment variables:
  CHARTMOGUL_API_KEY   - Your ChartMogul API key (Settings > API Keys)
  SLACK_BOT_TOKEN      - Slack Bot token (xoxb-...) with files:write + channels:join scope
  SLACK_CHANNEL_ID     - The Slack channel ID to post to (e.g. C012AB3CD)
                         (not required in test mode)

Install dependency:
  pip install Pillow

Test mode (saves image locally, does not post to Slack):
  python3 chartmogul_to_slack.py --test
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
IMAGE_HEIGHT = 560
ACCENT_COLOR  = "#751165"  # Left bar + label + date text
NUMBER_COLOR  = "#3C247F"  # Large customer count
BG_COLOR      = "#FEF9F2"  # Background
GRID_COLOR    = "#F5EEE3"  # Subtle grid lines
DIVIDER_COLOR = "#E8DDD0"  # Horizontal divider between total and breakdown


# ── ChartMogul ────────────────────────────────────────────────────────────────

def _chartmogul_get(url: str) -> dict:
    """Makes an authenticated GET request to the ChartMogul API and returns parsed JSON."""
    credentials = base64.b64encode(f"{CHARTMOGUL_API_KEY}:".encode()).decode()
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ChartMogul API error {e.code}: {e.read().decode()}") from e


def get_paying_customers() -> int:
    """
    Fetches total paying customers from ChartMogul using the /v1/metrics/customer-count
    endpoint, filtered to only include customers with MRR > 0. This matches the Paid
    Subscribers figure shown in the ChartMogul UI.
    """
    today = date.today().isoformat()
    url = (
        f"https://api.chartmogul.com/v1/metrics/customer-count"
        f"?start-date={today}&end-date={today}&interval=day&filters=mrr~GT~0"
    )
    data = _chartmogul_get(url)
    entries = data.get("entries", [])
    if not entries:
        raise ValueError(f"No ChartMogul data returned for {today}.")
    return entries[-1]["customers"]


def get_bme_classic_customers() -> int:
    """
    Fetches the count of paying BME Classic customers — those with MRR > 0
    and bme_classic custom attribute set to TRUE.
    """
    today = date.today().isoformat()
    url = (
        f"https://api.chartmogul.com/v1/metrics/customer-count"
        f"?start-date={today}&end-date={today}&interval=day"
        f"&filters=mrr~GT~0~AND~custom.bme_classic~EQ~TRUE"
    )
    data = _chartmogul_get(url)
    entries = data.get("entries", [])
    if not entries:
        return 0
    return entries[-1]["customers"]


# ── Image generation ──────────────────────────────────────────────────────────

def hex_to_rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def download_poppins() -> str:
    """Downloads Poppins-Regular.ttf from Google Fonts to a temp file if not already cached."""
    import tempfile, os
    cache_path = os.path.join(tempfile.gettempdir(), "Poppins-Regular.ttf")
    if not os.path.exists(cache_path):
        url = "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Regular.ttf"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as r:
            data = r.read()
        with open(cache_path, "wb") as f:
            f.write(data)
    return cache_path


def generate_image(total_count: int, bme_count: int, new_count: int) -> bytes:
    """Renders a styled dashboard card image and returns PNG bytes."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise RuntimeError("Pillow is not installed. Run: pip install Pillow")

    import os

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

    # Load Song Myung (numbers) — expected in the same directory as this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    song_myung_path = os.path.join(script_dir, "SongMyung-Regular.ttf")

    # Load Poppins (labels + date) — downloaded at runtime from Google Fonts
    poppins_path = download_poppins()

    def load_font(path, size, fallback_paths=None):
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            for fp in (fallback_paths or []):
                try:
                    return ImageFont.truetype(fp, size)
                except (IOError, OSError):
                    continue
        return ImageFont.load_default()

    system_fallbacks = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]

    font_big        = load_font(song_myung_path, 180, system_fallbacks)
    font_label      = load_font(poppins_path,     32,  system_fallbacks)
    font_date       = load_font(poppins_path,     22,  system_fallbacks)
    font_sub_num    = load_font(song_myung_path,  80,  system_fallbacks)  # ~44% of 180
    font_sub_label  = load_font(poppins_path,     22,  system_fallbacks)

    # ── "PAYING CUSTOMERS" label ──
    label = "PAYING CUSTOMERS"
    lw = draw.textlength(label, font=font_label)
    draw.text(((W - lw) / 2, 55), label, font=font_label, fill=ACCENT_COLOR)

    # ── Large total number ──
    num_str = f"{total_count:,}"
    nw = draw.textlength(num_str, font=font_big)
    nx = (W - nw) / 2
    ny = 110
    draw.text((nx, ny), num_str, font=font_big, fill=NUMBER_COLOR)

    # ── Horizontal divider ──
    divider_y = 320
    draw.line([(40, divider_y), (W - 40, divider_y)], fill=DIVIDER_COLOR, width=1)

    # ── Breakdown section ──
    # Two columns: "New" on the left, "BME Classic" on the right
    col_left_x  = W // 4        # Center of left column
    col_right_x = (W * 3) // 4  # Center of right column
    label_y     = 340
    number_y    = 368

    # Left column — New customers
    new_label = "New"
    nlw = draw.textlength(new_label, font=font_sub_label)
    draw.text((col_left_x - nlw / 2, label_y), new_label, font=font_sub_label, fill=ACCENT_COLOR)

    new_str = f"{new_count:,}"
    nnw = draw.textlength(new_str, font=font_sub_num)
    draw.text((col_left_x - nnw / 2, number_y), new_str, font=font_sub_num, fill=NUMBER_COLOR)

    # Right column — BME Classic customers
    bme_label = "BME Classic"
    blw = draw.textlength(bme_label, font=font_sub_label)
    draw.text((col_right_x - blw / 2, label_y), bme_label, font=font_sub_label, fill=ACCENT_COLOR)

    bme_str = f"{bme_count:,}"
    bnw = draw.textlength(bme_str, font=font_sub_num)
    draw.text((col_right_x - bnw / 2, number_y), bme_str, font=font_sub_num, fill=NUMBER_COLOR)

    # Vertical divider between the two columns
    draw.line([(W // 2, divider_y + 10), (W // 2, number_y + 90)], fill=DIVIDER_COLOR, width=1)

    # ── Date ──
    dw = draw.textlength(today_str, font=font_date)
    draw.text(((W - dw) / 2, 490), today_str, font=font_date, fill=ACCENT_COLOR)

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

    # Step 1: Get upload URL (must be sent as form-encoded data, not JSON)
    import urllib.parse
    step1_payload = urllib.parse.urlencode({
        "filename": filename,
        "length": len(image_bytes),
    }).encode()

    form_headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    req = urllib.request.Request(
        "https://slack.com/api/files.getUploadURLExternal",
        data=step1_payload,
        headers=form_headers,
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
    test_mode = "--test" in sys.argv

    # In test mode, only the ChartMogul API key is required
    errors = []
    if not CHARTMOGUL_API_KEY: errors.append("CHARTMOGUL_API_KEY")
    if not test_mode:
        if not SLACK_BOT_TOKEN:  errors.append("SLACK_BOT_TOKEN")
        if not SLACK_CHANNEL_ID: errors.append("SLACK_CHANNEL_ID")
    if errors:
        print(f"ERROR: Missing environment variables: {', '.join(errors)}", file=sys.stderr)
        sys.exit(1)

    if test_mode:
        print("⚠️  Running in TEST MODE — image will be saved locally, not posted to Slack.")

    print("Fetching total paying customer count from ChartMogul...")
    total_count = get_paying_customers()
    print(f"  → {total_count:,} total paying customers")

    print("Fetching BME Classic paying customer count from ChartMogul...")
    bme_count = get_bme_classic_customers()
    print(f"  → {bme_count:,} BME Classic paying customers")

    new_count = total_count - bme_count
    print(f"  → {new_count:,} New paying customers (calculated)")

    print("Generating image...")
    image_bytes = generate_image(total_count, bme_count, new_count)
    print(f"  → Image generated ({len(image_bytes) / 1024:.1f} KB)")

    if test_mode:
        # Save image locally instead of posting to Slack
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_path = os.path.join(script_dir, f"test-output-{date.today().isoformat()}.png")
        with open(output_path, "wb") as f:
            f.write(image_bytes)
        print(f"  → Image saved to: {output_path}")
    else:
        print("Uploading image to Slack...")
        upload_image_to_slack(image_bytes)
        print("  → Posted successfully!")


if __name__ == "__main__":
    main()
