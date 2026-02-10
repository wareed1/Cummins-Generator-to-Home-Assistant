#!/usr/bin/env python3

"""
Send a JSON string via MQTT to Home Assistant.
Read from stdin: the JSON string comes from another script in a pipeline.

Copyright (c) 2026 Wayne A. Reed
"""

import argparse
import ipaddress
import json
import logging
import os
from pathlib import Path
import sys
import time
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# Get the directory where this script is located
script_dir = Path(__file__).parent.absolute()
# Explicitly load the .env file from the same directory
load_dotenv(dotenv_path=script_dir / ".env")

GENERATOR_LOG_FILE = "generator_scraper.log"
# Setup logging to a file
logging.basicConfig(
    filename=GENERATOR_LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

ha_username = os.getenv("HA_USERNAME")
ha_password = os.getenv("HA_PASSWORD")


def on_connect(client, userdata, flags, rc, properties):  # pylint: disable=unused-argument
    """Callback for when the client successfully connects."""
    if rc == 0:
        logging.info("Connected successfully!")
    else:
        logging.error("Connection failed with code %s", rc)


def on_publish(client, userdata, mid, reason_code, properties):  # pylint: disable=unused-argument
    """Callback for when a message is published."""
    logging.info("Message Published (mid=%s)", mid)


def is_valid_ip(ip_string):
    """Return the IP address if the given string is a valid IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(ip_string)
        return ip_string
    except ValueError as exc:
        # The string is not a valid IP address
        raise argparse.ArgumentTypeError(f"Invalid format: expected xxx.xxx.xxx.xxx, {exc}")


def collect_arguments():
    """Get and validate command line arguments"""
    parser = argparse.ArgumentParser(description="MQTT Pipeline Script for Cummins Generator Data")
    # Mandatory Broker IP Address
    parser.add_argument(
        "broker", type=is_valid_ip, help="The IP address of the MQTT broker (xxx.xxx.xxx.xxx)"
    )
    # Mandatory Topic
    parser.add_argument("topic", type=str, help="The MQTT topic string (e.g., 'home/generator')")
    # Optional Port
    parser.add_argument(
        "-p", "--port", type=int, default=1883, help="MQTT broker port (default: 1883)"
    )
    args = parser.parse_args()
    return args


def main():
    """
    Get command line arguments, establish communication with the broker,
    get a JSON string from stdio, and forward the string to the specified topic
    """
    args = collect_arguments()
    raw_input = sys.stdin.read().strip()
    if not raw_input:
        # Fixed: logging.error doesn't take 'file'
        logging.error("No data received from pipe.")
        sys.stderr.write("Error: No data received from pipe.\n")
        sys.exit(1)

    client = None
    try:
        payload = json.loads(raw_input)
        payload_str = json.dumps(payload)
        logging.info("Payload: %s", payload_str)

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.username_pw_set(ha_username, ha_password)
        client.on_connect = on_connect
        client.on_publish = on_publish

        logging.info("Connecting to broker: %s", args.broker)
        client.connect(args.broker, args.port, 60)
        client.loop_start()

        # Wait for connection (on_connect callback)
        time.sleep(1)

        logging.info("Publishing to %s", args.topic)
        result = client.publish(args.topic, payload_str, qos=1, retain=True)

        # Wait for the message to actually be acknowledged (QoS 1)
        result.wait_for_publish()
        logging.info("Publish confirmed by broker.")

    except json.JSONDecodeError:
        logging.error("Failed to decode JSON: %s", raw_input)
        sys.exit(1)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logging.exception("An error occurred: %s", e)
        sys.exit(1)
    finally:
        if client and client.is_connected():
            logging.info("Cleaning up MQTT connection...")
            client.loop_stop()
            client.disconnect()
            logging.info("Done")


if __name__ == "__main__":
    main()
