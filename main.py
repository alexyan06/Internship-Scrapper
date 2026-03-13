def scrape_internship():
    seen_jobs = set()
    # Load seen jobs from the file
    try:
        with open('seen_jobs.txt', 'r') as f:
            seen_jobs = set([line.strip() for line in f])
    except FileNotFoundError:
        pass  # If the file does not exist, we start with an empty seen_jobs set

    consecutive_seen = 0  # Track consecutive seen jobs
    jobs_to_scrape = get_jobs()  # Assume this function retrieves jobs from the website

    for job in jobs_to_scrape:
        if job in seen_jobs:
            consecutive_seen += 1  # Increment count if the job has been seen
            if consecutive_seen >= 10:
                print('10 consecutive jobs found in seen_jobs. Stopping scrape.\n')
                break  # Stop scraping early
        else:
            consecutive_seen = 0  # Reset count if we find a new job
            # Process the job...
            process_job(job)  # Assume this function processes and saves the job
            seen_jobs.add(job)  # Add new job to seen jobs

    # Save the seen jobs back to the file
    with open('seen_jobs.txt', 'w') as f:
        for job in seen_jobs:
            f.write(job + '\n')

