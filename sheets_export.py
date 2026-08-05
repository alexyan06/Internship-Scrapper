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


TITLE_COL = HEADER.index("Title")
COMPANY_COL = HEADER.index("Company")


def _sheet_identity(row):
    """(title, company) for an existing sheet row, matching main.job_id."""
    if len(row) <= COMPANY_COL:
        return None
    title, company = row[TITLE_COL].strip(), row[COMPANY_COL].strip()
    return (title, company) if title else None


def _drop_already_present(jobs, existing):
    """Filter out jobs whose title+company is already a row in the sheet.

    A backstop rather than the primary defence -- get_new_jobs should have
    excluded these already. It exists because that check depends on
    seen_jobs.txt surviving the run, and when a commit of it failed the next
    run happily re-added every job. The sheet itself is the one record that
    cannot get out of sync with the sheet.
    """
    seen = {ident for ident in (_sheet_identity(r) for r in existing) if ident}
    kept, skipped = [], 0
    for job in jobs:
        ident = (str(job.get("title", "")).strip(), str(job.get("company", "")).strip())
        if ident in seen:
            skipped += 1
            continue
        seen.add(ident)  # also collapses duplicates within this batch
        kept.append(job)
    return kept, skipped


def _row(job, today):
    return [
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
    ]


def append_matches(matches, maybes=()):
    """Append confirmed Summer 2027 matches, plus inconclusive ones.

    `maybes` are jobs that named no season and no year at all. Anything
    carrying an explicit contrary signal -- a non-2027 year, or an off-season
    with no summer option -- was already dropped upstream and never reaches
    here, so a maybe row is genuinely unknown rather than known-wrong. They are
    written exactly like matches, showing whatever hire time the board gave.

    Never raises -- the email still matters.
    """
    maybes = list(maybes)
    if not matches and not maybes:
        print("No confirmed matches to add to the sheet.")
        return

    worksheet = _open_worksheet()
    if worksheet is None:
        return

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

        matches, skipped_m = _drop_already_present(matches, existing)
        maybes, skipped_b = _drop_already_present(maybes, existing + [
            _row(j, "") for j in matches])
        skipped = skipped_m + skipped_b
        if skipped:
            print(f"Skipped {skipped} job(s) already in the sheet.")

        today = datetime.now().strftime("%Y-%m-%d")
        rows = [_row(job, today) for job in matches + maybes]
        if not rows:
            print("Nothing new to add to the sheet.")
            return

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
        summary = f"Added {len(rows)} rows to the Google Sheet"
        if maybes:
            summary += f" ({len(matches)} confirmed, {len(maybes)} inconclusive)"
        print(summary + ".")
    except Exception as e:
        print(f"Failed to write rows to sheet: {type(e).__name__}: {e}")
