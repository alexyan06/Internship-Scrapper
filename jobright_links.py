"""Resolve jobright.ai listing links to the original job posting URL.

The Airtable board only ever gives us a jobright.ai link. Logged out, the real
posting URL is not present anywhere on that page -- not in the server-rendered
JSON, not in the JSON-LD block, and not behind any public API route (they all
401). The "Apply on Employer Site" control is an inert div until a session
exists. So this module signs in once per run and reuses that session.

Three traps this code exists to avoid:

* The header has a "SIGN IN" button and the modal has "SIGN IN TO APPLY".
  Matching on "SIGN IN" hits the header one, which silently never submits.
* Every job page links the employer's *homepage* (from the JSON-LD
  hiringOrganization). Accepting "first external link" resolves to
  ``iherb.com`` instead of the posting, which looks like success and isn't.
* A fresh session gets onboarding promos that mask the page and swallow the
  apply click. CI starts cold every run, so this is the steady state, not a
  first-run quirk -- see ``_dismiss_overlays``.

Resolution is best-effort: anything unresolved falls back to the jobright link
rather than dropping the job or guessing.
"""
import json
import os
import re

from playwright.sync_api import sync_playwright, TimeoutError

# Any job page works as a place to open the auth modal from.
_LOGIN_ANCHOR = "https://jobright.ai/jobs/info/6a71c74445b6af1c30dbab9d"

_USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# Job pages render slowly; every wait needs real headroom.
_RENDER_MS = 12000
_WAIT_MS = 45000

# The authenticated payload states the destination outright in these keys, in
# preference order. Guessing from the page is only for when they are absent.
_AUTHORITATIVE_KEYS = ("originalUrl", "applyLink")

# Escaped as \" inside Next.js flight data, plain in ordinary JSON, hence \\?".
_KEY_VALUE_RE = r'\\?"{key}\\?"\s*:\s*\\?"(https?://[^"\\]+)\\?"'

# Fallback only: keys that merely *look* like they carry an outbound URL.
_URL_KEY_RE = re.compile(
    r'"[a-zA-Z]*(?:apply|original|external|source|redirect)[a-zA-Z]*(?:Link|Url|URL)"'
    r'\s*:\s*"(https?://[^"]+)"',
    re.I,
)

_LD_JSON_RE = re.compile(
    r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)

# Hosts that appear on a job page but are never the application URL.
#
# LinkedIn is deliberately absent: jobright routes a large share of postings to
# linkedin.com/jobs/view/..., so banning the host outright throws away the very
# links this module exists to find. Its non-job paths are excluded below.
_NOT_APPLY_HOSTS = (
    "jobright.ai", "w3.org", "schema.org", "google.com", "gstatic.com",
    "googletagmanager.com", "google-analytics.com", "googleapis.com",
    "accounts.google.com", "facebook.com", "cloudflare.com", "sentry.io",
    "bing.com", "youtube.com", "twitter.com", "x.com", "apple.com",
    "licdn.com", "crunchbase.com", "glassdoor.com", "marketscreener.com",
    "einpresswire.com", "acnnewswire.com", "gravatar.com",
)

# Recruiter profiles, company pages and people search all live on linkedin.com
# alongside the postings; only /jobs/ URLs are actual listings.
_LINKEDIN_JOB_PATH = "/jobs/view/"


def _host_of(url):
    if not url or not str(url).startswith("http"):
        return ""
    return re.sub(r"^https?://(www\.)?", "", str(url)).split("/")[0].lower()


def _is_reachable(url):
    """Minimal sanity check: a real off-jobright destination."""
    host = _host_of(url)
    return bool(host) and not (host == "jobright.ai" or host.endswith(".jobright.ai"))


def _is_apply_url(url, company_host=""):
    """True if `url` could plausibly be the employer's posting.

    Used when scraping the page for candidates, where the employer's *homepage*
    (JSON-LD hiringOrganization) is the most common false positive. Not applied
    to `_AUTHORITATIVE_KEYS`, which state the destination directly -- a company
    whose posting lives on its own root domain would be wrongly rejected here.
    """
    host = _host_of(url)
    if not host:
        return False
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return _LINKEDIN_JOB_PATH in str(url)
    if any(host == h or host.endswith("." + h) for h in _NOT_APPLY_HOSTS):
        return False
    if company_host and host == company_host:
        return False
    return True


def _company_homepage_host(html):
    """Employer homepage host from the JSON-LD block, so we can exclude it."""
    for block in _LD_JSON_RE.findall(html):
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, TypeError):
            continue
        org = data.get("hiringOrganization") or {}
        if isinstance(org, dict):
            host = _host_of(org.get("sameAs"))
            if host:
                return host
    return ""


# Framework close controls. Jobright's promos are antd modals whose contents
# rotate (the coach image is served as coach4.webp), so match the close control
# rather than any one modal's copy.
_CLOSE_SELECTORS = (
    ".ant-modal-close",
    ".ant-drawer-close",
    "[aria-label='Close']",
    "[aria-label='close']",
)

# What actually blocks the click: antd lays a full-page mask under each modal,
# and .ant-modal-wrap is the scroll container over it. Either one intercepts.
_MASK_SELECTORS = (".ant-modal-mask", ".ant-modal-wrap", ".ant-drawer-mask")

# Last resort for promos with no antd close control, e.g. the Orion
# resume-tailoring panel, which is dismissed by a plain "exit".
_DISMISS_LABELS = ("exit", "close", "got it", "skip", "maybe later", "no thanks")


def _page_is_clear(page):
    """True when nothing is masking the page."""
    for sel in _MASK_SELECTORS:
        loc = page.locator(sel)
        for i in range(loc.count()):
            try:
                if loc.nth(i).is_visible():
                    return False
            except Exception:
                pass
    return True


def _dismiss_overlays(page, rounds=6):
    """Close every modal stacked over the page, until nothing masks it.

    A fresh session always walks through onboarding promos -- an Orion resume
    prompt, then a coach image -- and CI never reuses a profile, so this runs on
    every job page rather than once. They stack: closing one reveals the next,
    hence the loop and the mask check instead of a fixed number of clicks.

    Note `force=True` is deliberately not used anywhere as a shortcut here. It
    skips Playwright's actionability check but still dispatches at the target's
    coordinates, so the mask swallows the click and the failure gets quieter,
    not fixed.
    """
    for _ in range(rounds):
        if _page_is_clear(page):
            return True

        clicked = False
        for sel in _CLOSE_SELECTORS:
            loc = page.locator(sel)
            for i in range(loc.count()):
                try:
                    if loc.nth(i).is_visible():
                        loc.nth(i).click(timeout=3000)
                        clicked = True
                        page.wait_for_timeout(1200)
                except Exception:
                    pass
        if clicked:
            continue

        # No close control matched. antd modals close on Escape by default.
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(800)
        except Exception:
            pass
        if _page_is_clear(page):
            return True

        for label in _DISMISS_LABELS:
            try:
                btn = page.get_by_role("button", name=label, exact=False)
                if btn.count() and btn.first.is_visible():
                    btn.first.click(timeout=3000)
                    clicked = True
                    page.wait_for_timeout(1200)
                    break
            except Exception:
                pass
        if not clicked:
            break

    return _page_is_clear(page)


def _sign_in(page, email, password):
    """Drive jobright's auth modal. Returns True only on a verified session."""
    page.goto(_LOGIN_ANCHOR, wait_until="domcontentloaded", timeout=60000)

    apply_btn = page.get_by_role("button", name="APPLY NOW", exact=False).first
    apply_btn.wait_for(state="visible", timeout=_WAIT_MS)
    apply_btn.click()

    # The modal opens in sign-up mode; flip it to sign-in.
    switch = page.get_by_role("button", name="Already a member", exact=False).first
    switch.wait_for(state="visible", timeout=_WAIT_MS)
    switch.click()

    password_field = page.locator("#basic_password")
    password_field.wait_for(state="visible", timeout=_WAIT_MS)

    page.fill("#basic_email", email)
    page.fill("#basic_password", password)

    # Must be the modal's submit, NOT the header "SIGN IN" button.
    page.get_by_role("button", name="SIGN IN TO APPLY", exact=False).first.click()

    # A real session removes the logged-out header controls AND the password
    # field. Poll on counts rather than waiting for one node to detach -- React
    # re-renders detach nodes while still logged out, which reads as success.
    for _ in range(12):
        page.wait_for_timeout(2500)
        logged_out = (page.get_by_role("button", name="JOIN NOW", exact=False).count()
                      or page.locator("#basic_password").count())
        if not logged_out:
            break
    else:
        print("Jobright sign-in failed: still logged out after submitting.")
        return False

    # A profile-less account is trapped in the onboarding wizard and cannot
    # open job pages at all, so say so plainly instead of failing per-job.
    if "/onboarding" in page.url:
        print("Jobright account is stuck in onboarding; complete your profile "
              "on jobright.ai first. Keeping jobright links.")
        return False

    # Sign-in is where the onboarding promos fire; clear them once here so the
    # session starts clean rather than carrying a modal into the first job page.
    _dismiss_overlays(page)

    print("Signed in to jobright.")
    return True


def _unescape(url):
    return url.replace("\\u002F", "/").replace("\\/", "/")


def _extract_from_markup(page, company_host):
    """Cheapest path: the URL is embedded in the authenticated page payload.

    Signed in, the payload carries `originalUrl`/`applyLink` outright, so read
    those before falling back to inference. Logged out they are absent, which
    is what makes the whole sign-in dance necessary.
    """
    html = page.content()

    for key in _AUTHORITATIVE_KEYS:
        for raw in re.findall(_KEY_VALUE_RE.format(key=key), html):
            url = _unescape(raw)
            if _is_reachable(url):
                return url

    # The visible "Original Job Post" link is the same destination, and exists
    # even if the key names change.
    try:
        link = page.locator("a", has_text=re.compile("Original Job Post", re.I))
        for i in range(min(link.count(), 3)):
            href = link.nth(i).get_attribute("href", timeout=2000)
            if href and _is_reachable(href):
                return href
    except Exception:
        pass

    for raw in _URL_KEY_RE.findall(html):
        url = _unescape(raw)
        if _is_apply_url(url, company_host):
            return url
    return None


def _extract_by_clicking(page, context, company_host):
    """Ground truth: click through and see where jobright sends us."""
    for label in ("Apply on Employer Site", "APPLY NOW"):
        # Re-check every attempt: promos can fire on a timer after the page
        # load, so one dismissal at navigation time is not enough. Cheap when
        # nothing is showing -- the mask check short-circuits.
        _dismiss_overlays(page)

        target = page.get_by_text(label, exact=False) if " on " in label \
            else page.get_by_role("button", name=label, exact=False)
        if target.count() == 0:
            continue

        existing = set(context.pages)
        try:
            target.first.click(timeout=10000)
        except Exception:
            continue

        page.wait_for_timeout(7000)

        for candidate in context.pages:
            if candidate in existing:
                continue
            try:
                candidate.wait_for_load_state("domcontentloaded", timeout=15000)
            except TimeoutError:
                pass
            url = candidate.url
            try:
                candidate.close()
            except Exception:
                pass
            if _is_apply_url(url, company_host):
                return url

        if _is_apply_url(page.url, company_host):
            return page.url

    return None


def resolve_apply_links(jobs):
    """Set job['original_link'] for each job, falling back to the jobright link.

    Only pass the small post-filter set -- each lookup is a full page load.
    """
    for job in jobs:
        job["original_link"] = job.get("apply_link", "N/A")

    targets = [j for j in jobs if str(j.get("apply_link", "")).startswith("http")]
    if not targets:
        return jobs

    email = os.environ.get("JOBRIGHT_EMAIL")
    password = os.environ.get("JOBRIGHT_PASSWORD")
    if not email or not password:
        print("JOBRIGHT_EMAIL / JOBRIGHT_PASSWORD not set. Keeping jobright links.")
        return jobs

    resolved = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=_USER_AGENT)
        page = context.new_page()

        try:
            if not _sign_in(page, email, password):
                browser.close()
                return jobs
        except Exception as e:
            print(f"Jobright sign-in failed ({type(e).__name__}). Keeping jobright links.")
            browser.close()
            return jobs

        for job in targets:
            try:
                page.goto(job["apply_link"], wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(_RENDER_MS)
                _dismiss_overlays(page)

                company_host = _company_homepage_host(page.content())
                url = (_extract_from_markup(page, company_host)
                       or _extract_by_clicking(page, context, company_host))

                if url:
                    job["original_link"] = url
                    resolved += 1
                    print(f"  resolved: {job['title'][:40]!r} -> {url[:80]}")
                else:
                    print(f"  unresolved, keeping jobright link: {job['title'][:40]!r}")
            except Exception as e:
                print(f"  lookup failed for {job['title'][:40]!r}: {type(e).__name__}")

        browser.close()

    print(f"Resolved {resolved}/{len(targets)} original posting links.")
    return jobs
