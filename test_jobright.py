"""Standalone check for jobright link resolution. Touches nothing else.

    python test_jobright.py                    # default sample job
    python test_jobright.py <jobright_url> ... # specific jobs

Reads credentials from .env (gitignored) or the environment. Does not scrape
Airtable, send email, write the sheet, or touch seen_jobs.txt.
"""
import os
import sys


def load_dotenv(path=".env"):
    """Minimal .env loader so this runs without extra dependencies."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()

from jobright_links import resolve_apply_links  # noqa: E402 -- needs env first

DEFAULT_JOB = "https://jobright.ai/jobs/info/6a71c74445b6af1c30dbab9d"


def main():
    urls = sys.argv[1:] or [DEFAULT_JOB]

    missing = [k for k in ("JOBRIGHT_EMAIL", "JOBRIGHT_PASSWORD") if not os.environ.get(k)]
    if missing:
        print(f"Missing {', '.join(missing)}.")
        print("Put them in .env (gitignored) or export them, then re-run.")
        return 1

    print(f"Signing in as {os.environ['JOBRIGHT_EMAIL']}")
    print(f"Testing {len(urls)} job page(s)...\n")

    jobs = [{"title": f"test-{i + 1}", "company": "test", "apply_link": u}
            for i, u in enumerate(urls)]
    resolve_apply_links(jobs)

    print("\n" + "=" * 70)
    resolved = 0
    for job in jobs:
        original, jobright = job["original_link"], job["apply_link"]
        if original != jobright:
            resolved += 1
            print(f"RESOLVED  {jobright[:55]}\n       -> {original}")
        else:
            print(f"UNRESOLVED (kept jobright link)  {jobright[:55]}")
    print("=" * 70)

    if resolved == len(jobs):
        print(f"\nAll {resolved}/{len(jobs)} resolved. Link resolution works.")
        return 0

    print(f"\n{resolved}/{len(jobs)} resolved.")
    if resolved == 0:
        print("\nNothing resolved. The scraper still works -- emails just keep the")
        print("jobright links. Check the log above for which stage failed:")
        print("  'sign-in failed'      -> wrong credentials, or Google-only login")
        print("  'stuck in onboarding' -> finish your profile on jobright.ai")
        print("  'unresolved'          -> signed in fine, but the URL wasn't on the")
        print("                           page; send me this output and I'll adjust")
    return 1


if __name__ == "__main__":
    sys.exit(main())
