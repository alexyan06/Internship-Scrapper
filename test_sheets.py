"""Standalone check for the Google Sheets export. Touches nothing else.

    python test_sheets.py

Reads GOOGLE_SHEET_ID and GOOGLE_SERVICE_ACCOUNT_JSON from .env or the
environment, writes one clearly-labelled test row, then tells you to delete it.
Does not scrape, email, or touch seen_jobs.txt.
"""
import os
import sys

from test_jobright import load_dotenv

load_dotenv()

# Convenience: point GOOGLE_SERVICE_ACCOUNT_JSON at a file and we'll read it,
# since pasting multi-line JSON into .env is awkward.
_creds = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
if _creds and not _creds.lstrip().startswith("{") and os.path.exists(_creds):
    with open(_creds) as f:
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = f.read()
elif not _creds and os.path.exists("service_account.json"):
    with open("service_account.json") as f:
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = f.read()
    print("Using service_account.json from the project root.")

import sheets_export  # noqa: E402 -- needs env first

TEST_ROW = {
    "title": "TEST ROW - safe to delete",
    "company": "test",
    "location": "test",
    "date": "test",
    "hire_time": "Summer 2027",
    "grad_time": "test",
    "salary": "test",
    "apply_link": "https://jobright.ai/test",
    "original_link": "https://example.com/test",
}


def main():
    missing = [k for k in ("GOOGLE_SHEET_ID", "GOOGLE_SERVICE_ACCOUNT_JSON")
               if not os.environ.get(k)]
    if missing:
        print(f"Missing {', '.join(missing)}. See SETUP.md.")
        return 1

    # Check imports up front. Otherwise a missing library surfaces as the
    # generic "could not open the sheet" advice below, which points at Google
    # Cloud setup that is in fact fine.
    try:
        import gspread  # noqa: F401
        import google.auth  # noqa: F401
    except ImportError:
        print("gspread / google-auth are not installed for this Python.")
        print(f"  interpreter: {sys.executable}")
        print(f"  fix: {sys.executable} -m pip install gspread google-auth")
        print("\nRun the exact interpreter above -- a bare 'pip install' can")
        print("land in a different environment than the one running this.")
        return 1

    worksheet = sheets_export._open_worksheet()
    if worksheet is None:
        print("\nCould not open the sheet. Most likely causes:")
        print("  - the sheet is not shared with the service account's")
        print("    ...iam.gserviceaccount.com address as Editor")
        print("  - the Google Sheets API is not enabled on the project")
        print("  - GOOGLE_SHEET_ID is the full URL instead of just the ID")
        return 1

    before = len(worksheet.get_all_values())
    sheets_export.append_matches([TEST_ROW])
    after = len(worksheet.get_all_values())

    if after > before:
        print(f"\nWrote to the sheet ({before} -> {after} rows).")
        print("Delete the row labelled 'TEST ROW - safe to delete' and you're set.")
        return 0

    print("\nOpened the sheet but no row appeared -- check the log above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
