"""Append confirmed Summer 2027 internships to a shareable Google Sheet.

Uses a service account so it works unattended in CI (no OAuth consent screen).
The sheet itself is shared read-only with whoever needs to follow along, so the
link stays stable while rows accumulate.
"""
import json
import os
from datetime import datetime

HEADER = [
    "Added", "Date Posted", "Title", "Company", "Location",
    "Hire Time", "Grad Time", "Salary", "Apply Link", "Jobright Link",
]


def _open_worksheet():
    """Return the target worksheet, or None if Sheets isn't configured."""
    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")

    if not creds_json or not sheet_id:
        print("Google Sheets not configured (missing credentials or sheet ID). Skipping.")
        return None

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("gspread/google-auth not installed. Skipping sheet update.")
        return None

    try:
        info = json.loads(creds_json)
    except json.JSONDecodeError as e:
        print(f"GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON: {e}")
        return None

    try:
        creds = Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        worksheet = gspread.authorize(creds).open_by_key(sheet_id).sheet1
    except Exception as e:
        print(f"Could not open Google Sheet: {type(e).__name__}: {e}")
        return None

    return worksheet


def append_matches(matches):
    """Append confirmed Summer 2027 matches. Never raises -- the email still matters."""
    if not matches:
        print("No confirmed matches to add to the sheet.")
        return

    worksheet = _open_worksheet()
    if worksheet is None:
        return

    today = datetime.now().strftime("%Y-%m-%d")
    rows = [[
        today,
        job.get("date", "N/A"),
        job.get("title", "N/A"),
        job.get("company", "N/A"),
        job.get("location", "N/A"),
        job.get("hire_time", "N/A"),
        job.get("grad_time", "N/A"),
        job.get("salary", "N/A"),
        job.get("original_link", job.get("apply_link", "N/A")),
        job.get("apply_link", "N/A"),
    ] for job in matches]

    # Write at an explicit range rather than appending.
    #
    # append() resolves its insert position server-side from the last populated
    # row, and that lookup lags a recent write -- so appending a header and
    # then appending the first data row puts both on row 1, destroying the
    # header. Computing the range here means one write and no guessing.
    #
    # len(get_all_values()) is not a safe row count either: it counts trailing
    # blanks and can read stale straight after another write. Position is taken
    # from the last row with actual content instead.
    try:
        existing = worksheet.get_all_values()
        last_filled = max((i + 1 for i, row in enumerate(existing)
                           if any(cell.strip() for cell in row)), default=0)

        if last_filled == 0:
            payload, start_row = [HEADER] + rows, 1
        else:
            payload, start_row = rows, last_filled + 1
            # Restore the header only into a genuinely empty A1, so a sheet
            # that starts with real data never gets a row overwritten.
            first_row = existing[0] if existing else []
            if not any(cell.strip() for cell in first_row):
                worksheet.update(values=[HEADER], range_name="A1",
                                 value_input_option="USER_ENTERED")

        worksheet.update(values=payload, range_name=f"A{start_row}",
                         value_input_option="USER_ENTERED")
        print(f"Added {len(rows)} confirmed matches to the Google Sheet.")
    except Exception as e:
        print(f"Failed to write rows to sheet: {type(e).__name__}: {e}")
