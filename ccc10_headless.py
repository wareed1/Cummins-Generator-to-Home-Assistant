#!/usr/bin/env python3

"""
Get generator runtime hours and battery voltage from Cummins Connect Cloud.
Use Selenium to do all the heavy lifting with login redirects.
Put your username and password in a .env file in the same folder as this script.
The two expected environment variables are: MY_USERNAME and MY_PASSWORD.
The script runs headless.

Copyright (c) 2026 Wayne A. Reed
"""

import json
import logging
import re
import os
import sys

# uncomment time if you want to debug URLs
# import time
from pathlib import Path
from datetime import datetime
from datetime import timezone
import requests
from dateutil.parser import parse, ParserError
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class CumminsSelectors:  # pylint: disable=too-few-public-methods
    """Cummins Connect Cloud specific text"""

    # Sign In Page
    SIGN_IN_PAGE = "https://connectcloud.cummins.com"
    SIGN_IN_BUTTON = "SIGN IN"

    # Login Page
    USER_INPUT = 'input[name="username"]'
    PASSWORD_INPUT = 'input[name="password"]'
    LOGIN_BUTTON = "Login"
    LOGIN_SUBMIT = "button.slds-button_brand"

    # Dashboard Tabs/Pulldowns
    TAB_EVENTS = "Events"
    TAB_MAINTENANCE = "Maintenance"
    PULL_NOTIFICATIONS = "Notifications"
    PULL_GENERATOR_DATA = "Generator Data"

    # Data Labels
    LABEL_BATTERY = "Battery Voltage (V)"
    LABEL_RUNTIME = "Engine Runtime"
    LABEL_EXERCISE = "Genset exercise completed"
    LABEL_UNITS = "Hours"


# Get the directory where this script is located
script_dir = Path(__file__).parent.absolute()
# Explicitly load the .env file from the same directory
load_dotenv(dotenv_path=script_dir / ".env")

my_username = os.getenv("MY_USERNAME")
my_password = os.getenv("MY_PASSWORD")


GENERATOR_LOG_FILE = "generator_scraper.log"
# Setup logging to a file
logging.basicConfig(
    filename=GENERATOR_LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
# methods that follow are in alphabetical order except for main() that is last


def click_tab_smart(driver, tab_name):
    """Find the tab with a partial match"""
    script = f"""
    const findAndClickTab = (root, target) => {{
        const allNodes = root.querySelectorAll('*');
        for (const node of allNodes) {{
            // 1. Case-insensitive partial match (handles "Events (16)")
            if (node.textContent.trim().toLowerCase().includes(target.toLowerCase())) {{
                // Only click if it's a 'leaf' node or a button-like element
                if (node.children.length === 0 \
                    || node.tagName === 'BUTTON' \
                    || node.tagName === 'A') {{
                    // Trigger multiple click types for compatibility
                    node.click();
                    node.dispatchEvent(new MouseEvent('mousedown', {{bubbles: true}}));
                    node.dispatchEvent(new MouseEvent('mouseup', {{bubbles: true}}));
                    return true;
                }}
            }}
            // 2. Recursively search Shadow Roots
            if (node.shadowRoot) {{
                const found = findAndClickTab(node.shadowRoot, target);
                if (found) return true;
            }}
        }}
        return false;
    }};
    return findAndClickTab(document.body, "{tab_name}");
    """
    success = driver.execute_script(script)
    return success


def click_pulldown(driver, text_on_dashboard):
    """Find the pulldown associated with the text and click on it"""
    try:
        # 1. Wait up to 15 seconds for the element to actually exist
        # 2. Use an XPATH that looks for a <p> tag containing specific text
        #    regardless of its class name.
        element = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.XPATH, f"//p[contains(text(), '{text_on_dashboard}')]"))
        )
        element.click()
        return True
    except Exception as e:  # pylint: disable=broad-exception-caught
        logging.exception("Error on pulldown for %s: %s", text_on_dashboard, e)
        driver.save_screenshot("click_pulldown_debug_render.png")
        sys.exit(1)


def create_driver():
    """Set up the webdriver"""
    options = Options()
    options.add_argument("--headless=new")
    # add the User-Agent to look like a real browser
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        + "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    options.add_argument("--window-size=1920,1080")  # Prevents "mobile" layout shifts
    options.add_argument("--no-sandbox")  # Bypasses OS security model (needed for Pi)
    options.add_argument("--disable-dev-shm-usage")  # Uses /tmp instead of memory for crashes
    service = Service("/usr/lib/chromium-browser/chromedriver")
    driver = webdriver.Chrome(service=service, options=options)
    return driver


def do_login(driver):
    """Enter username and password and click Logon button"""
    username_field = find_deep_placeholder(driver, CumminsSelectors.USER_INPUT)
    if username_field:
        driver.execute_script("arguments[0].value = '';", username_field)
        username_field.send_keys(my_username)
    else:
        logging.error("Still couldn't find username - check if the page is fully loaded!")
        driver.save_screenshot("login_debug_render.png")
        sys.exit(1)
    password_field = find_deep_placeholder(driver, CumminsSelectors.PASSWORD_INPUT)
    if password_field:
        driver.execute_script("arguments[0].value = '';", password_field)
        password_field.send_keys(my_password)
    else:
        logging.error("Still couldn't find password - check if the page is fully loaded!")
        driver.save_screenshot("login_debug_render.png")
        sys.exit(1)
    trigger_event_script = """
    arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
    arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
    """
    driver.execute_script(trigger_event_script, username_field)
    driver.execute_script(trigger_event_script, password_field)
    login_button = find_deep_placeholder(driver, CumminsSelectors.LOGIN_SUBMIT)
    if login_button:
        logging.info("Attempting to click: %s", CumminsSelectors.LOGIN_BUTTON)
        login_button.click()
        logging.info("%s button clicked", CumminsSelectors.LOGIN_BUTTON)
    else:
        # If the class name is different, try searching by the text 'Log In'
        # using a slightly different selector or checking the HTML again
        logging.error("Button not found - check the class name in Inspect!")
        driver.save_screenshot("login_debug_render.png")
        sys.exit(1)
    # Allow time to load the redirect if you want to see the new URL.
    # time.sleep(10)
    # logging.info("New URL: %s", driver.current_url)


def find_battery_voltage_in_shadow_roots(driver):
    """Look through Shadow Roots for specific text that indicates the battery voltage"""
    script = """
    const findValueBelowLabel = (root, targetLabel) => {
        // 1. Find all elements that might be our label
        const allNodes = root.querySelectorAll('*');
        for (let i = 0; i < allNodes.length; i++) {
            if (allNodes[i].textContent.trim() === targetLabel) {
                // Label found! Now look at the next few elements in the DOM
                // for the one containing the actual voltage number.
                if (allNodes[i].textContent.trim().includes(targetLabel)) {
                    let parent = allNodes[i].parentElement;
                    // Search within the parent for the value to avoid index-hopping
                    const val = parent.innerText.match(/\\d+\\.\\d+/);
                    return val ? val[0] : null;
                }
            }
            // 2. Dig into Shadow Roots if this element has one
            if (allNodes[i].shadowRoot) {
                const found = findValueBelowLabel(allNodes[i].shadowRoot, targetLabel);
                if (found) return found;
            }
        }
        return null;
    };
    return findValueBelowLabel(document.body, arguments[0]) || "NOT_FOUND";
    """
    return driver.execute_script(script, CumminsSelectors.LABEL_BATTERY)


def find_deep_placeholder(driver, selector):
    """Find elements hidden deep inside nested Shadow DOMs"""
    script = """
    const findInShadows = (root, selector) => {
        const el = root.querySelector(selector);
        if (el) return el;
        const hosts = root.querySelectorAll('*');
        for (const host of hosts) {
            if (host.shadowRoot) {
                const found = findInShadows(host.shadowRoot, selector);
                if (found) return found;
            }
        }
        return null;
    };
    return findInShadows(document, arguments[0]);
    """
    return driver.execute_script(script, selector)


def find_genset_exercise_in_shadow_roots(driver):
    """Look through Shadow Roots for specific text that indicates the genset exercise date"""
    script = """
    const findDateByAnchor = (root, target) => {
        if (!root) return null;

        // 1. Search for the anchor label in the current root
        const allElements = root.querySelectorAll('p');
        for (const el of allElements) {
            if (el.textContent.trim().includes(target)) {
                const dateEl = el.nextElementSibling;
                return dateEl ? dateEl.textContent.trim() : null;
            }
        }

        // 2. Recursive Shadow DOM piercing
        // We use 'all' but must iterate through every element to check for shadowRoot
        const children = root.querySelectorAll('*');
        for (const node of children) {
            if (node.shadowRoot) {
                const found = findDateByAnchor(node.shadowRoot, target);
                if (found) return found; // Return immediately if found
            }
        }
        return null;
    };
    
    // arguments[0] is the CumminsSelectors.LABEL_EXERCISE passed below
    return findDateByAnchor(document.body, arguments[0]);
    """
    return driver.execute_script(script, CumminsSelectors.LABEL_EXERCISE)


def find_runtime_in_shadow_roots(driver):
    """Look through Shadow Roots for specific text that indicates the runtime hours"""
    script = """
    const findTextDeep = (root, target) => {
        // Search current level
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
        let node;
        while (node = walker.nextNode()) {
            if (node.textContent.includes(target)) return node.parentElement;
        }
        // Dig into Shadow Roots
        const allElements = root.querySelectorAll('*');
        for (const el of allElements) {
            if (el.shadowRoot) {
                const found = findTextDeep(el.shadowRoot, target);
                if (found) return found;
            }
        }
        return null;
    };
    const element = findTextDeep(document.body, arguments[0]);
    return element ? element.innerText : "NOT_FOUND";
    """
    return driver.execute_script(script, CumminsSelectors.LABEL_UNITS)


def generate_ha_payload(runtime, voltage, exercise_date):
    """
    Create the payload of sensor data for Home Assistant as a JSON string.
    Print it to standard output.
    Pipeline this output into another script to send it to Home Assistant via MQTT.
    """
    try:
        # 'fuzzy=True' ignores extra words like day names or ordinals automatically
        dt = parse(exercise_date, fuzzy=True).replace(tzinfo=timezone.utc)
        iso_date = dt.isoformat()
        logging.info("Successfully converted to ISO: %s", iso_date)
    except ParserError as e:
        logging.exception("Failed to convert last exercise date to ISO format: %s", e)
        sys.exit(1)
    # Build the dictionary
    data = {
        "generator": {
            "runtime_hours": float(runtime),
            "battery_voltage": float(voltage),
            "last_exercise_date": iso_date,
            "last_updated": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    }
    return data


def start_sign_in(driver, url):
    """
    Find the button to sign into Cloud Connect.
    Press it to move to the next step to authenticate to Cloud Connect.
    """
    try:
        driver.get(url)
        # 1. List clickable elements to find the sign in button
        logging.info("Searching for the '%s' element...", CumminsSelectors.SIGN_IN_BUTTON)
        elements = driver.find_elements(
            By.XPATH, "//*[contains(text(), " + f"'{CumminsSelectors.SIGN_IN_BUTTON}')]"
        )
        for idx, el in enumerate(elements):
            logging.info(
                "[%s] Tag: %s | Text: %s | Visible: %s",
                idx,
                el.tag_name,
                el.text,
                el.is_displayed(),
            )
        # 2. Try clicking the FIRST visible one using a different JS method
        if elements:
            target_span = elements[0]
            logging.info("Attempting to click: %s", target_span.text)
            parent_button = target_span.find_element(By.XPATH, "..")
            parent_button.click()
            logging.info("%s button clicked.", CumminsSelectors.SIGN_IN_BUTTON)
        # Allow time time to load the redirect if you want to see the new URL
        # time.sleep(10)
        # logging.info("New URL: %s", driver.current_url)
    except requests.exceptions.HTTPError as e:
        logging.exception("An HTTP error occurred: %s", e)
        driver.save_screenshot("sign_in_debug_render.png")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        logging.exception("A request error occurred during sign in: %s", e)
        driver.save_screenshot("sign_in_debug_render.png")
        sys.exit(1)


def scrape_battery(driver):
    """Get the generator's battery voltage from the Generator Data pulldown"""
    success = click_pulldown(driver, CumminsSelectors.PULL_GENERATOR_DATA)
    if not success:
        logging.error("Could not find the %s click-down!", CumminsSelectors.PULL_GENERATOR_DATA)
        driver.save_screenshot("battery_debug_render.png")
        sys.exit(1)
    battery_voltage_text = find_battery_voltage_in_shadow_roots(driver)
    logging.info("Battery voltage = %s", battery_voltage_text)
    return battery_voltage_text


def scrape_genset_exercise(driver):
    """Get the generator's last exercise completed date from the Notificatons pulldown"""
    success = click_pulldown(driver, CumminsSelectors.PULL_NOTIFICATIONS)
    if not success:
        logging.error("Could not find the %s click-down!", CumminsSelectors.PULL_NOTIFICATIONS)
        driver.save_screenshot("genset_debug_render.png")
        sys.exit(1)
    if click_tab_smart(driver, CumminsSelectors.TAB_EVENTS):
        logging.info("Success: %s tab clicked.", CumminsSelectors.TAB_EVENTS)
    else:
        logging.error("Failure: %s tab not found.", CumminsSelectors.TAB_EVENTS)
        driver.save_screenshot("genset_debug_render.png")
        sys.exit(1)
    genset_exercise_text = find_genset_exercise_in_shadow_roots(driver)
    if genset_exercise_text and re.search(r"\d{4}", genset_exercise_text):
        logging.info("Verified date: %s", genset_exercise_text)
    else:
        logging.error("Scraped text does not look like a date. Check UI.")
        driver.save_screenshot("genset_debug_render.png")
        sys.exit(1)
    return genset_exercise_text


def scrape_runtime(driver):
    """Get the generator's runtime hours from the Maintenance pulldown"""
    success = click_pulldown(driver, CumminsSelectors.TAB_MAINTENANCE)
    if not success:
        logging.error("Could not find the %s click-down!", CumminsSelectors.TAB_MAINTENANCE)
        driver.save_screenshot("runtime_debug_render.png")
        sys.exit(1)
    runtime_text = find_runtime_in_shadow_roots(driver)
    logging.info("Generator runtime = %s", runtime_text)
    # scraped results look like: "27.8 Hours"
    # break this raw text into value and units
    words_list = runtime_text.split()
    return words_list[0]


def main():
    """Use Selenium to get generator runtime hours and battery voltage"""
    driver = create_driver()
    try:
        start_sign_in(driver, CumminsSelectors.SIGN_IN_PAGE)
        do_login(driver)
        runtime_value = scrape_runtime(driver)
        battery_value = scrape_battery(driver)
        genset_value = scrape_genset_exercise(driver)
        if any(v is None for v in [runtime_value, battery_value, genset_value]):
            logging.error(
                "Data is not in expected format, inspect the logged values above this message."
            )
            sys.exit(1)
        payload = generate_ha_payload(runtime_value, battery_value, genset_value)
        json_payload = json.dumps(payload, separators=(",", ":"))
        # Print as a JSON string for the pipeline
        print(json_payload)
        # And print an audit log
        logging.info("Final JSON payload: %s", json_payload)
        logging.info("Scrape completed successfully and payload printed.")
    except Exception as e:  # pylint: disable=broad-exception-caught
        logging.exception("An unexpected error occurred: %s", e)
        sys.exit(1)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
