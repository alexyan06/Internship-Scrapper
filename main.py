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


def scrape_internship(seen_jobs):
    AIRTABLE_URL = "https://airtable.com/app17F0kkWQZhC6HB/shrOTtndhc6HSgnYb/tblp8wxvfYam5sD04?"
    CONSECUTIVE_SEEN_LIMIT = 10

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
        consecutive_seen_count = 0
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
                hire_time = right_row.locator('div[data-columnindex="6"]').inner_text()
                grad_time = right_row.locator('div[data-columnindex="7"]').inner_text()

                scroll_container.evaluate("node => node.scrollLeft = node.scrollWidth")
                page.wait_for_timeout(250)

                salary = right_row.locator('div[data-columnindex="10"]').inner_text()
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
                print(f"Successfully scraped: {job_title}")

                # --- EARLY EXIT CHECK ---
                job_id = f"{job_data['title']}-{job_data['company']}"
                if job_id in seen_jobs:
                    consecutive_seen_count += 1
                    if consecutive_seen_count >= CONSECUTIVE_SEEN_LIMIT:
                        print(f"Last {CONSECUTIVE_SEEN_LIMIT} scraped jobs were already seen. Stopping early.")
                        early_exit = True
                        break
                else:
                    consecutive_seen_count = 0

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
    my_matches = []

    WANTED_GRAD_TIMES = [
        "2027-December",
        "2028",
        "2028-Spring",
        "2028-Summer",
        "N/A",
        "2027-Winter",
    ]

    WANTED_HIRE_TIMES = [
        "2026-Summer",
        "Summer",
        "2026-May",
        "2026-June",
        "2026-July",
        "2026-Fall",
        "Fall 2026",
        "Fall",
        "2026-Spring",
        "Spring 2026",
        "Spring",
        "N/A",
        "2025-Summer",
        "2025",
        "2024-Summer",
        "2024",
    ]

    for job in internships:
        grad_time_match = job["grad_time"] in WANTED_GRAD_TIMES
        hire_time_match = job["hire_time"] in WANTED_HIRE_TIMES

        if grad_time_match and hire_time_match:
            my_matches.append(job)
    return my_matches


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


def send_email(new_jobs):
    if not new_jobs:
        print("No new jobs to notify about")
        return

    sender_email = os.environ.get("MY_EMAIL_ADDRESS")
    sender_password = os.environ.get("MY_EMAIL_APP_PASSWORD")

    if not sender_email or not sender_password:
        print("Email credentials not set. Skipping email.")
        return

    subject = f"Found {len(new_jobs)} new internships that match your description!"

    html_body = "<html><body>"
    html_body += "<h2>Here are the new internships that match your criteria:</h2>"
    for job in new_jobs:
        html_body += f"<p><b>Title:</b> {job['title']}<br>"
        html_body += f"<b>Company:</b> {job['company']}<br>"
        html_body += f"<b>Location:</b> {job['location']}<br>"
        html_body += f"<b>Hire Time:</b> {job['hire_time']}<br>"
        html_body += f"<b>Grad Time:</b> {job['grad_time']}<br>"
        html_body += f"<b>Salary:</b> {job['salary']}<br>"
        html_body += f'<a href="{job["apply_link"]}"><b>Apply Here</b></a></p><hr>'
    html_body += "</body></html>"

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender_email
    msg['To'] = sender_email

    msg.set_content("Please enable HTML to view this email.")
    msg.add_alternative(html_body, subtype='html')

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(sender_email, sender_password)
        smtp.send_message(msg)
    print(f"Successfully sent email with {len(new_jobs)} new jobs!")


# --- MAIN EXECUTION BLOCK ---
if __name__ == "__main__":
    seen_jobs = load_seen_jobs()
    all_internships = scrape_internship(seen_jobs)
    my_matches = filter_for_matches(all_internships)
    new_matching_jobs = get_new_jobs(my_matches, seen_jobs)
    send_email(new_matching_jobs)
    print("\nScript finished.")
