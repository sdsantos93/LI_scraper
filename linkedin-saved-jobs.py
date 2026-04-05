# %%
# ---- Imports ----
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from bs4 import BeautifulSoup

import os
import time
import re
import math
import random

import pandas as pd  # for CSV export
from notion_client import Client  # for Notion integration


# %%
# ---- Functions ----


def create_stealth_browser():
    options = Options()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    b = webdriver.Chrome(options=options)
    b.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return b


def human_type(element, text):
    for char in text:
        element.send_keys(char)
        time.sleep(random.uniform(0.05, 0.15))


# Log into LinkedIn with stealth settings and login verification
# Requires LI_USER and LI_PASS env vars
def login_to_linkedin():
    browser.get("https://www.linkedin.com/login")
    wait = WebDriverWait(browser, 30)

    username_field = wait.until(EC.element_to_be_clickable((By.ID, "username")))
    human_type(username_field, os.environ["LI_USER"])

    password_field = wait.until(EC.element_to_be_clickable((By.ID, "password")))
    human_type(password_field, os.environ["LI_PASS"])

    wait.until(
        EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))
    ).click()
    print("Login submitted, waiting for verification...")

    # Wait for either successful login (redirect to /feed) or 2FA prompt
    for _ in range(120):  # up to 120 seconds
        current_url = browser.current_url
        # Check if login succeeded — LinkedIn redirects to /feed after login
        if "/feed" in current_url:
            print("Login verified - redirected to feed")
            return
        # Check for 2FA pin input
        pin_fields = browser.find_elements(By.XPATH, "//input[@name='pin']")
        if pin_fields:
            print("\n2FA detected! Please enter your verification code in the browser.")
            print("Waiting up to 120 seconds for you to complete verification...")
            for _ in range(120):
                if "/feed" in browser.current_url:
                    print("Login verified after 2FA")
                    return
                time.sleep(1)
            print("ERROR: 2FA verification timed out")
            break
        time.sleep(1)

    # If we get here, login failed — save debug info
    save_debug_info("login_failed")
    raise Exception(
        "Login verification failed. Check login_failed.html and login_failed.png"
    )


def save_debug_info(prefix):
    html_path = prefix + ".html"
    png_path = prefix + ".png"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(browser.page_source)
    try:
        browser.save_screenshot(png_path)
    except Exception:
        pass
    print("  Debug info saved: " + html_path + ", " + png_path)


# Get the appropriate saved job URL
def get_saved_jobs_url(job_type="saved"):
    url_dict = {
        "saved": "https://www.linkedin.com/my-items/saved-jobs/?cardType=SAVED",
        "applied": "https://www.linkedin.com/my-items/saved-jobs/?cardType=APPLIED",
        "progress": "https://www.linkedin.com/my-items/saved-jobs/?cardType=IN_PROGRESS",
        "archived": "https://www.linkedin.com/my-items/saved-jobs/?cardType=ARCHIVED",
    }
    assert job_type.lower() in url_dict.keys(), "not a recognized job type!"

    return url_dict[job_type.lower()]


# Collect the results on a page
# Args:
# - get_ext_link (bool): flag to get external application link
# - wait_time (float): how long to wait, in seconds, for dropdown menu to appear when getting external apply link (anecdotally should be >= 0.6)
# Returns: two lists of the same length
# - results: list of saved job content
# - apply_cont: list of dropdown elements (contains external application link)
def collect_results(get_ext_link=True, wait_time=0.65):
    html = browser.page_source
    soup = BeautifulSoup(html, "html.parser")
    results = soup.find_all("div", attrs={"class": "mb1"})
    if len(results) == 0:
        save_debug_info("debug_no_results")
        raise Exception(
            "No results detected! Check debug_no_results.html and debug_no_results.png"
        )
    print("  Found " + str(len(results)) + " results")

    # only get external links if indicated, otherwise return a list of Nones
    if get_ext_link:
        # find all dropdowns
        dds = browser.find_elements(
            By.CLASS_NAME, "entity-result__actions-overflow-menu-dropdown"
        )
        assert len(dds) > 0, (
            "Expected to find dropdown elements in browser, but did not!"
        )
        apply_cont = [get_apply_content_from_dropdown(dd, wait_time) for dd in dds]
    else:
        apply_cont = [None] * len(results)
    return results, apply_cont


# Parse the collected results
# Returns: list of lists, each containing job title, link to posting, external application link (or None), employer, location
def parse_results():
    inside_res = []
    any_apply_content = any(saved_ext)

    for res, apply_cont in zip(saved, saved_ext):
        # job title
        job = res.find("div", attrs={"class": "t-roman"})
        title = job.get_text().replace(", Verified", "").replace(", Vérifié", "").strip()
        title = re.sub(r"\s+", " ", title)  # collapse extra whitespace/newlines
        link = job.find("a").get("href")
        li_link = re.split(r"[\\?]", link)[0]
        # company, location
        employer, location = [
            r.get_text().strip() for r in res.find_all("div", attrs={"class": "t-14"})
        ]

        ext_link = None
        # Only update the link if the text is 'Apply' (e.g., could be Easy Apply)
        if any_apply_content and apply_cont is not None:
            dd_apply = apply_cont.find("a")
            assert dd_apply is not None, "Expected to find a link in dropdown!"

            if dd_apply.get_text().strip() == "Apply":
                ext_link = dd_apply.get("href")
        inside_res.append([title, li_link, ext_link, employer, location])
    return inside_res


# Determines whether there is a next page
# Returns: bool
def next_page():
    time.sleep(1)
    test = browser.find_element(By.XPATH, "//button[@aria-label='Next']")
    if test.is_enabled():
        try:
            test.click()
            return True
        except Exception:
            return False
    else:
        print("No more pages")
        return False


# Click and return the dropdown component for a saved job
# Args:
# - dd (WebElement): dropdown element for a saved job, detected by Selenium
# - wait_time (float): how long to wait, in seconds, for dropdown menu to appear when getting external apply link (anecdotally should be >= 0.6)
# Returns: the content of a the dropdown
def get_apply_content_from_dropdown(dd, wait_time=0.6):
    # Click it to reveal the dropdown
    dd.click()
    time.sleep(wait_time)
    # Find the apply link/text within the dropdown
    dd_soup = BeautifulSoup(browser.page_source, "html.parser")
    dd_result = dd_soup.find("div", attrs={"class": "artdeco-dropdown__content-inner"})
    assert dd_result is not None, "Expected to find a dropdown, but content not found!"
    # Click again to hide dropdown in browser
    dd.click()
    return dd_result


# Fetch full job descriptions by navigating to each job's URL
# Called after parse_results() so we have the URLs
# Args:
# - parsed_results: list of [title, url, url2, employer, location] lists
# - wait_time (float): seconds to wait for description to load per job
# Returns: list of description strings (or None for each job)
def fetch_descriptions(parsed_results, wait_time=5):
    descriptions = []
    debug_saved = False
    selectors = [
        "#job-details",
        ".jobs-description__content",
        ".jobs-description",
        "div.show-more-less-html__markup",
        "div.description__text--rich",
    ]
    show_more_selectors = [
        ".jobs-description__footer-button",
        "button[aria-label='Show more']",
        "button[aria-label='Voir plus']",
    ]

    print("\nFetching descriptions for " + str(len(parsed_results)) + " jobs...")

    for idx, job in enumerate(parsed_results):
        title = job[0]
        url = job[1]
        print("  Job " + str(idx + 1) + "/" + str(len(parsed_results)) + ": " + title[:50])

        try:
            browser.get(url)
            time.sleep(random.uniform(2.0, 3.5))

            # Try clicking "Show more" button to expand description
            for sm_sel in show_more_selectors:
                try:
                    show_more = browser.find_element(By.CSS_SELECTOR, sm_sel)
                    browser.execute_script("arguments[0].click();", show_more)
                    time.sleep(0.5)
                    break
                except Exception:
                    pass

            # Try each selector to find the description
            desc = None
            elapsed = 0
            interval = 0.5
            while elapsed < wait_time:
                page_soup = BeautifulSoup(browser.page_source, "html.parser")
                for sel in selectors:
                    el = page_soup.select_one(sel)
                    if el and len(el.get_text(strip=True)) > 50:
                        desc = el.get_text(separator="\n", strip=True)
                        break
                if desc:
                    break
                time.sleep(interval)
                elapsed += interval

            if desc:
                # Check for expired/removed jobs
                expired_phrases = [
                    "no longer accepting applications",
                    "no longer available",
                    "this job has expired",
                    "n'accepte plus de candidatures",
                    "n'est plus disponible",
                ]
                if any(phrase in desc.lower() for phrase in expired_phrases):
                    desc = "[EXPIRED]"
                    print("    -> expired")
                else:
                    print("    -> captured (" + str(len(desc)) + " chars)")
            else:
                print("    -> no description found")
                if not debug_saved:
                    save_debug_info("debug_job_desc")
                    debug_saved = True

            descriptions.append(desc)

        except Exception as e:
            print("    -> error: " + str(e))
            descriptions.append(None)

    return descriptions


# Function to create an entry in a Notion database
def create_entry(title, url, url2, employer, location):
    new_page = {
        "Name": {"title": [{"text": {"content": title}}]},
        "Status": {"type": "status", "status": {"name": "Not started"}},
        "Company": {
            "type": "rich_text",
            "rich_text": [
                {
                    "type": "text",
                    "text": {"content": employer},
                },
            ],
        },
        "URL": {"type": "url", "url": url},
        "URL 2": {"type": "url", "url": url2},
        "Location": {
            "type": "rich_text",
            "rich_text": [
                {
                    "type": "text",
                    "text": {"content": location},
                },
            ],
        },
    }
    notion.pages.create(
        parent={"database_id": os.environ["NOTION_DATABASE_ID"]}, properties=new_page
    )
    print("  Added to DB")


# Function to check if an entry in a Notion database
# Only checks for matching job title and employer
def entry_exists(title, company):
    results = notion.databases.query(
        database_id=os.environ["NOTION_DATABASE_ID"],
        filter={
            "and": [
                {"property": "Name", "rich_text": {"equals": title}},
                {"property": "Company", "rich_text": {"equals": company}},
            ]
        },
    ).get("results")
    return len(results) > 0


# %%
# ---- Params ----
# What type of saved job?
# One of: 'saved', 'progress', 'applied', 'archived' (case insensitive)
saved_job_type = "saved"

# How many pages (max) to check?
# Keep as -1 if all
num_pages = 1 #-1

# Whether to retrieve external application links, and how long to wait to retrieve them
retrieve_ext_links = True
ext_link_wait_time = 0.65  # seconds

# Whether to retrieve full job descriptions (navigates to each job URL)
retrieve_descriptions = True
desc_wait_time = 5  # seconds to wait for description to load per job

# Export type
# One of: 'csv', 'notion' (case insensitive)
export_to = "csv"

# [Notion export]
# How many consecutive entries should already exist in the Notion database before we stop checking?
# Set to a very high number if you want to add all new and don't mind waiting
notion_exist_thresh = 10

# [CSV export]
# File name/path for CSV file
csv_filename = "saved_jobs.csv"

# %%
# ---- Run script ----

# Open a Chrome browser to LinkedIn saved jobs, then log in
# Requires LI_USER and LI_PASS environment variables
browser = create_stealth_browser()
login_to_linkedin()
browser.get(get_saved_jobs_url(saved_job_type))

# Iterate through each page and collect results
# This is the only time we should be clicking through the browser
# Parsing will happen after
# Initiate the list of saved jobs and page counter
saved = []
saved_ext = []
next_page_exists = True
num_pages = num_pages if num_pages > 0 else math.inf
i = 1

# Override get_ext_link if job type is not saved
if saved_job_type.lower() != "saved":
    if retrieve_ext_links:
        print("job type is not 'saved', will not retrieve external links")
    retrieve_ext_links = False

# This could take a bit longer to run if you're getting the external links
# How much longer? approximately: number of saved jobs * wait_time (below)
while next_page_exists and i <= num_pages:
    print("Page " + str(i))
    time.sleep(
        5
    )  # Wait for page to fully load before parsing
    results, apply_cont = collect_results(
        get_ext_link=retrieve_ext_links, wait_time=ext_link_wait_time
    )
    assert len(results) > 0, "No saved jobs detected! (expected at least 1)"
    saved.extend(results)
    saved_ext.extend(apply_cont)
    try:
        next_page_exists = next_page()
    except Exception:
        break
    i += 1

assert len(saved) > 0, "No results saved, expected more than one saved job!"
print("\nTotal collected jobs: " + str(len(saved)))

# Parse results (title, url, url2, employer, location)
parsed_results = parse_results()
print("\nParsed results: " + str(len(parsed_results)))
assert len(parsed_results) == len(saved), (
    "Number of parsed results not equal to number saved!"
)

# Fetch descriptions by navigating to each job URL (browser still open)
if retrieve_descriptions:
    descriptions = fetch_descriptions(parsed_results, wait_time=desc_wait_time)
    # Add description to each result
    for i, desc in enumerate(descriptions):
        parsed_results[i].append(desc)
else:
    for job in parsed_results:
        job.append(None)

# Close browser
browser.close()

# Export
assert export_to.lower() in ["csv", "notion"], "export type not recognized!"

if export_to.lower() == "csv":
    # Create date frame from results
    colnames = ["title", "url", "url2", "employer", "location", "description"]
    df = pd.DataFrame(parsed_results, columns=colnames)
    assert len(df) > 0, "Issue converting parsed results to df!"
    # Drop columns where all values are None (url2, if no external links)
    df.dropna(how="all", axis=1, inplace=True)
    # Save results to CSV file
    df.to_csv(csv_filename, index=False)
elif export_to.lower() == "notion":
    # Copy new results into Notion database
    notion = Client(auth=os.environ["NOTION_TOKEN"])
    # Iterate through results, keeping track of how many consecutive existing entries there are
    # This way we can stop if we're just getting enough results that already exist
    exist_count = 0
    print("\nChecking for entries in Notion database\n")
    for job in parsed_results:
        title, url, url2, employer, location, _description = job
        print(title + " at " + employer)

        if entry_exists(title, employer):
            exist_count += 1
            print("  Already exists")
        else:
            exist_count = 0
            create_entry(title, url, url2, employer, location)

        # Stop iterating once we hit threshold
        if exist_count >= notion_exist_thresh:
            print(
                "  Stopping because "
                + str(notion_exist_thresh)
                + " consecutive entries already exist"
            )
            break

# Print final message
print("\nDone!")
