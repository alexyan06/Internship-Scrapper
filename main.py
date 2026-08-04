import os
import re
import smtplib
from email.message import EmailMessage
from playwright.sync_api import sync_playwright, TimeoutError

import sheets_export
from jobright_links import resolve_apply_links


def load_seen_jobs():
    """Load the set of already-seen job IDs from disk."""
    SEEN_JOBS_FILE = "seen_jobs.txt"
    if not os.path.exists(SEEN_JOBS_FILE):
        open(SEEN_JOBS_FILE, 'w').close()
    with open(SEEN_JOBS_FILE, 'r') as f:
        return set(line.strip() for line in f)


def scrape_internship():
    AIRTABLE_URL = "https://airtable.com/app17F0kkWQZhC6HB/shrOTtndhc6HSgnYb/tblp8wxvfYam5sD04?"
    MAX_ROWS_TO_SCRAPE = 200

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(AIRTABLE_URL, wait_until="domcontentloaded", timeout=60000)
        page.locator("div.dataRow").first.wait_for(timeout=60000)
        print("Page and initial rows loaded.")

        left_pane = page.locator("div.dataLeftPane")
        right_pane = page.locator("div.dataRightPane")
        scroll_container = page.locator("div.antiscroll-inner").first

        internships = []
        processed_row_ids = set()
        early_exit = False

        while True:
            left_rows = left_pane.locator("div.dataRow").all()
            new_rows_found = False

            for left_row in left_rows:
                row_id = left_row.get_attribute("data-rowid")
                if not row_id or row_id in processed_row_ids:
                    continue

                processed_row_ids.add(row_id)
                new_rows_found = True

                job_title = left_row.locator('div[data-columnindex="0"]').inner_text()
                right_row = right_pane.locator(f'div.dataRow[data-rowid="{row_id}"]')

                scroll_container.evaluate("node => node.scrollLeft = 0")
                page.wait_for_timeout(250)

                apply_link = "N/A"
                try:
                    link_locator = right_row.locator('div[data-columnindex="2"] a')
                    apply_link = link_locator.get_attribute('href', timeout=1000) or "N/A"
                except TimeoutError:
                    print(f"No apply button found for {job_title}")

                date = right_row.locator('div[data-columnindex="1"]').inner_text()
                location = right_row.locator('div[data-columnindex="4"]').inner_text()
                company = right_row.locator('div[data-columnindex="5"]').inner_text()
                salary = right_row.locator('div[data-columnindex="6"]').inner_text()
                hire_time = right_row.locator('div[data-columnindex="7"]').inner_text()
                grad_time = right_row.locator('div[data-columnindex="8"]').inner_text()

                scroll_container.evaluate("node => node.scrollLeft = node.scrollWidth")
                page.wait_for_timeout(250)

                qualifications = right_row.locator('div[data-columnindex="11"]').inner_text()

                job_data = {
                    "title": job_title.strip() or "N/A",
                    "apply_link": apply_link,
                    "date": date.strip() or "N/A",
                    "location": location.strip() or "N/A",
                    "company": company.strip() or "N/A",
                    "hire_time": hire_time.strip() or "N/A",
                    "grad_time": grad_time.strip() or "N/A",
                    "salary": salary.strip() or "N/A",
                    "qualifications": qualifications.strip() or "N/A",
                }
                internships.append(job_data)
                print(f"Successfully scraped: {job_title} | hire_time={job_data['hire_time']!r}")

                if len(internships) >= MAX_ROWS_TO_SCRAPE:
                    print(f"Reached scrape cap of {MAX_ROWS_TO_SCRAPE} jobs. Stopping.")
                    early_exit = True
                    break

            if early_exit:
                break

            if not new_rows_found:
                print("Reached the end of the list.")
                break

            scroll_container.evaluate("node => node.scrollTop += node.clientHeight")
            page.wait_for_timeout(1500)

        browser.close()
        return internships


# Word boundaries are required, not stylistic: "ms" appears inside Systems,
# Platforms and Algorithms, and "graduate" inside "Undergraduate". Substring
# matching -- the style DENY_TERMS below uses -- would be badly wrong here.
BACHELORS_RE = re.compile(
    r"\b(?:b\.?s\.?|b\.?a\.?|bachelor'?s?|undergrad(?:uate)?)\b",
    re.IGNORECASE,
)
HIGHER_DEGREE_RE = re.compile(
    r"\b(?:ph\.?\s?d\.?|m\.?s\.?|m\.?eng\.?|m\.?b\.?a\.?|master'?s?|"
    r"doctora(?:l|te)|post[-\s]?doc(?:toral)?|grad(?:uate)?\s+students?)\b",
    re.IGNORECASE,
)


def is_bachelors_eligible(job):
    """True unless the title names a higher degree with no bachelors signal.

    A bachelors marker anywhere in the title wins outright -- "(BS/MS)" is
    open to undergrads. Silence about degree also means keep; only an
    unaccompanied MS/PhD/Master's signal drops the job.
    """
    title = job.get("title", "")
    if BACHELORS_RE.search(title):
        return True
    return not HIGHER_DEGREE_RE.search(title)


# Word-bounded like the degree patterns above, and for the same reason: "may"
# sits inside Maynard, "march" inside Marchetti, "fall" inside Fallback.
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_SUMMER_RE = re.compile(r"\b(?:summer|april|may|june|july)\b", re.IGNORECASE)
_OFF_SEASON_RE = re.compile(
    r"\b(?:fall|autumn|spring|winter|january|february|march|"
    r"august|september|october|november|december)\b",
    re.IGNORECASE,
)


def classify_timing(job):
    """'match' | 'review' | 'drop' for a job's Summer-2027 fit.

    Reads hire_time and title together, since either may carry the timing.

    The ordering matters. A summer signal *outranks* a co-listed off-season
    one, because "Spring/Summer 2027" is a role you can take in summer --
    the previous denylist-first rule dropped those outright. An off-season
    signal only decides the outcome when no summer option accompanies it,
    which still drops "Fall 2026 or Spring 2027" correctly.

    A bare "Summer" with no year counts as a match. An explicitly wrong year
    is already dropped before that point, so the only thing reaching it is an
    unqualified summer posting, which at this stage means 2027.
    """
    text = f"{job.get('hire_time', '')} {job.get('title', '')}"

    years = set(_YEAR_RE.findall(text))
    wants_2027 = "2027" in years
    other_year_only = bool(years - {"2027"}) and not wants_2027

    summer = bool(_SUMMER_RE.search(text))
    off_season = bool(_OFF_SEASON_RE.search(text))

    if summer and wants_2027:
        return "match"
    if other_year_only:
        return "drop"
    if off_season and not summer:
        return "drop"
    if wants_2027:
        # 2027 with no conflicting season. Internships default to summer.
        return "match"
    if summer:
        # Summer with no year stated. 2026 summer hiring is over by the time
        # this runs, and an explicit wrong year would have been caught above,
        # so an unqualified "Summer" is taken as 2027.
        return "match"
    return "review"


def filter_for_matches(internships):
    """Split into matches / needs-review / degree-dropped.

    Timing decides the split (see classify_timing); "drop" jobs are discarded
    entirely, "review" ones are surfaced in the email rather than silently
    binned. Survivors then pass a degree gate (see is_bachelors_eligible).

    Degree rejects are returned as a third list rather than dropped silently,
    so the run log can show what that filter cost us -- seen_jobs.txt is
    written before filtering, so a bad drop is permanent.
    """
    matches = []
    needs_review = []
    degree_dropped = []

    for job in internships:
        verdict = classify_timing(job)
        if verdict == "drop":
            continue

        # Degree gate applies to both buckets, so it sits before the split.
        if not is_bachelors_eligible(job):
            degree_dropped.append(job)
            continue

        (matches if verdict == "match" else needs_review).append(job)

    return matches, needs_review, degree_dropped


def get_new_jobs(jobs, seen_jobs):
    SEEN_JOBS_FILE = "seen_jobs.txt"
    new_jobs = []
    for job in jobs:
        job_id = f"{job.get('title')}-{job.get('company')}"
        if job_id not in seen_jobs:
            new_jobs.append(job)
    with open(SEEN_JOBS_FILE, 'a') as f:
        for job in new_jobs:
            job_id = f"{job.get('title')}-{job.get('company')}"
            f.write(job_id + "\n")
    return new_jobs


def _render_job_html(job):
    jobright_link = job.get("apply_link", "N/A")
    apply_link = job.get("original_link") or jobright_link

    html = f"<p><b>Title:</b> {job['title']}<br>"
    html += f"<b>Company:</b> {job['company']}<br>"
    html += f"<b>Location:</b> {job['location']}<br>"
    html += f"<b>Hire Time:</b> {job['hire_time']}<br>"
    html += f"<b>Grad Time:</b> {job['grad_time']}<br>"
    html += f"<b>Salary:</b> {job['salary']}<br>"
    html += f'<a href="{apply_link}"><b>Apply Here</b></a>'

    # Only worth showing both when we actually got past jobright.
    if apply_link != jobright_link and jobright_link.startswith("http"):
        html += f' &nbsp;|&nbsp; <a href="{jobright_link}">(Jobright listing)</a>'

    return html + "</p><hr>"


def send_email(matches, unspecified_jobs):
    if not matches and not unspecified_jobs:
        print("No new jobs to notify about")
        return

    sender_email = os.environ.get("MY_EMAIL_ADDRESS")
    sender_password = os.environ.get("MY_EMAIL_APP_PASSWORD")

    if not sender_email or not sender_password:
        print("Email credentials not set. Skipping email.")
        return

    subject = f"Found {len(matches)} new internships that match your description!"
    if unspecified_jobs:
        subject += f" ({len(unspecified_jobs)} more need review)"

    html_body = "<html><body>"
    html_body += "<h2>Here are the new internships that match your criteria:</h2>"
    for job in matches:
        html_body += _render_job_html(job)

    if unspecified_jobs:
        html_body += "<h2>Needs Review (no hire time / season info found)</h2>"
        for job in unspecified_jobs:
            html_body += _render_job_html(job)

    html_body += "</body></html>"

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender_email
    msg['To'] = sender_email

    msg.set_content("Please enable HTML to view this email.")
    msg.add_alternative(html_body, subtype='html')

    total = len(matches) + len(unspecified_jobs)
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
        print(f"Successfully sent email with {total} new jobs!")
    except smtplib.SMTPException as e:
        print(f"Failed to send email: {e}")


# --- MAIN EXECUTION BLOCK ---
if __name__ == "__main__":
    seen_jobs = load_seen_jobs()
    all_internships = scrape_internship()
    new_jobs = get_new_jobs(all_internships, seen_jobs)
    my_matches, unspecified_jobs, degree_dropped = filter_for_matches(new_jobs)

    print(f"\nMatches ({len(my_matches)}):")
    for job in my_matches:
        print(f"  {job['title']!r} | hire_time={job['hire_time']!r}")

    print(f"\nNeeds review ({len(unspecified_jobs)}):")
    for job in unspecified_jobs:
        print(f"  {job['title']!r} | hire_time={job['hire_time']!r}")

    print(f"\nDegree-filtered out ({len(degree_dropped)}):")
    for job in degree_dropped:
        print(f"  {job['title']!r}")

    # Only the jobs we're about to report on -- each lookup is a page load.
    if my_matches or unspecified_jobs:
        print("\nResolving original posting links...")
        resolve_apply_links(my_matches + unspecified_jobs)

    # Inconclusive jobs go to the sheet too, flagged "maybe". Anything with a
    # contrary season/year signal was dropped by classify_timing and is not in
    # either list, so nothing known-wrong can reach the sheet this way.
    sheets_export.append_matches(my_matches, unspecified_jobs)
    send_email(my_matches, unspecified_jobs)
    print("\nScript finished.")
