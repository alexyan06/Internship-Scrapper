import os
import smtplib
from email.message import EmailMessage
from playwright.sync_api import sync_playwright, TimeoutError


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


def filter_for_matches(internships):
    """Denylist-first split into matches / needs-review / dropped.

    A job is denied (dropped entirely) if its hire_time or title mentions
    any non-2027, non-Summer signal: "2026", a non-summer season (Fall,
    Spring, Winter), or a month outside April-July. Denial checks title
    too, since a job can't be Summer 2027 if its own title says "Fall".

    Anything not denied is a match if it explicitly confirms 2027 +
    Summer (or an April-July month); otherwise it's ambiguous (blank
    hire_time, bare "2027" with no season, no signal at all) and goes to
    needs-review instead of being silently dropped.
    """
    DENY_TERMS = (
        "2026",
        "fall", "spring", "winter",
        "january", "february", "march",
        "august", "september", "october", "november", "december",
    )
    GOOD_TERMS = ("summer", "april", "may", "june", "july")

    matches = []
    needs_review = []

    for job in internships:
        combined = f"{job['hire_time']} {job['title']}".lower()

        if any(term in combined for term in DENY_TERMS):
            continue

        if "2027" in combined and any(term in combined for term in GOOD_TERMS):
            matches.append(job)
        else:
            needs_review.append(job)

    return matches, needs_review


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
    html = f"<p><b>Title:</b> {job['title']}<br>"
    html += f"<b>Company:</b> {job['company']}<br>"
    html += f"<b>Location:</b> {job['location']}<br>"
    html += f"<b>Hire Time:</b> {job['hire_time']}<br>"
    html += f"<b>Grad Time:</b> {job['grad_time']}<br>"
    html += f"<b>Salary:</b> {job['salary']}<br>"
    html += f'<a href="{job["apply_link"]}"><b>Apply Here</b></a></p><hr>'
    return html


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
    my_matches, unspecified_jobs = filter_for_matches(new_jobs)

    print(f"\nMatches ({len(my_matches)}):")
    for job in my_matches:
        print(f"  {job['title']!r} | hire_time={job['hire_time']!r}")

    print(f"\nNeeds review ({len(unspecified_jobs)}):")
    for job in unspecified_jobs:
        print(f"  {job['title']!r} | hire_time={job['hire_time']!r}")

    send_email(my_matches, unspecified_jobs)
    print("\nScript finished.")
