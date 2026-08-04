# Setup

The scraper runs on GitHub Actions three times a day and needs six secrets.
Everything is optional in the sense that a missing secret degrades gracefully:
no jobright credentials means the email keeps the jobright links, no Google
credentials means the sheet is skipped. The email still sends either way.

## GitHub secrets

Repo → Settings → Secrets and variables → Actions → *New repository secret*.

| Secret | Purpose |
|---|---|
| `MY_EMAIL_ADDRESS` | Gmail address the digest is sent from and to |
| `MY_EMAIL_APP_PASSWORD` | Gmail **app password**, not your account password |
| `JOBRIGHT_EMAIL` | Your jobright.ai login |
| `JOBRIGHT_PASSWORD` | Your jobright.ai password |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The full service-account JSON key, pasted whole |
| `GOOGLE_SHEET_ID` | The sheet ID from its URL |

## Google Sheet

1. Create a sheet. The ID is the long string in the URL:
   `docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`
2. In [Google Cloud Console](https://console.cloud.google.com/): create a
   project → enable the **Google Sheets API** → *Credentials* → *Create
   credentials* → *Service account*.
3. On the service account → *Keys* → *Add key* → *Create new key* → **JSON**.
   Paste the entire file contents into the `GOOGLE_SERVICE_ACCOUNT_JSON` secret.
4. Copy the service account's email (ends `@…iam.gserviceaccount.com`) and
   **share the sheet with it as Editor**. This step is the one that is easy to
   miss — without it every write fails with a permission error.
5. To share with a friend: *Share* → *Anyone with the link* → **Viewer**. The
   scraper appends rows, so the link stays valid as the sheet grows.

Only confirmed Summer 2027 matches go to the sheet. Needs-review jobs stay in
the email so the shared sheet doesn't fill with maybes.

## Local testing

Put credentials in a `.env` file in the project root — it is gitignored:

```
JOBRIGHT_EMAIL=you@example.com
JOBRIGHT_PASSWORD=...
GOOGLE_SHEET_ID=...
```

To check jobright link resolution on its own — it reads `.env` itself, and
touches nothing else (no Airtable scrape, no email, no sheet, no
`seen_jobs.txt`):

```bash
python test_jobright.py                      # one sample job
python test_jobright.py <jobright_url> ...   # specific jobs
```

It prints the original URL beside each jobright link, and on failure names the
stage that broke. Verified against LinkedIn, iCIMS, Workday and company career
sites.

For a full run:

```bash
set -a && source .env && set +a
python main.py
```

Note that `main.py` appends to `seen_jobs.txt` **before** filtering, so a local
run marks jobs as seen and they will not appear in the next real run. Work on a
scratch copy of `seen_jobs.txt` if that matters.

## Schedule

`.github/workflows/run-scraper.yml` runs at 13:00, 19:00 and 01:00 UTC — 8am,
2pm and 8pm Eastern during standard time. GitHub cron only speaks UTC, so
these drift an hour during daylight saving; adjust the three `cron:` lines if
you want them pinned to local clock time year round.
