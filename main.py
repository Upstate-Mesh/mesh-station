import meshtastic.serial_interface
import time
from pubsub import pub
import threading
import yaml
import requests

API_URL = "https://weatherjawn.com/api/current?zone=ALB&units=us"
CONFIG_FILE = "config.yml"


def load_config():
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)


def on_receive(packet, interface):
    try:
        decoded = packet.get("decoded", {})
        fromId = packet.get("fromId", "unknown")
        toId = packet.get("toId", "unknown")
        portnum = decoded.get("portnum")

        if portnum == "TEXT_MESSAGE_APP":
            text = decoded.get("text", "")
            print(f"[Received from {fromId} to {toId}] {text}")
    except Exception as e:
        print(f"[System] Error decoding packet: {e}")


def on_connection(interface, topic=pub.AUTO_TOPIC):
    print("[System] Serial connected!")

    if config["ad_enabled"] is True:
        threading.Thread(
            target=ad,
            args=(
                interface,
                config["ad_channel_index"],
                config["ad_interval_seconds"],
                config["ad_text"],
            ),
            daemon=True,
        ).start()

    if config["beacon_enabled"] is True:
        threading.Thread(
            target=beacon,
            args=(
                interface,
                config["beacon_channel_index"],
                config["beacon_interval_seconds"],
                config["beacon_text"],
            ),
            daemon=True,
        ).start()

    if config["weather_enabled"] is True:
        threading.Thread(
            target=weather,
            args=(
                interface,
                config["weather_channel_index"],
                config["weather_interval_seconds"],
            ),
            daemon=True,
        ).start()


def ad(interface, channel_index, interval, text):
    print("[System] Ad enabled.")

    while True:
        print(f"[Sending] '{text}' on channel '{channel_index}'")
        interface.sendText(text, channelIndex=channel_index)

        time.sleep(interval)


def beacon(interface, channel_index, interval, text):
    print("[System] Beacon enabled.")

    while True:
        print(f"[Sending] '{text}' on channel '{channel_index}'")
        interface.sendText(text, channelIndex=channel_index)

        time.sleep(interval)


def weather(interface, channel_index, interval):
    print("[System] Weather enabled.")

    while True:
        try:
            response = requests.get(API_URL, timeout=10)
            response.raise_for_status()

            data = response.json()

            current_conditions = data.get("currentConditions", {})
            conditions = current_conditions.get("conditions", "unknown")
            temp = current_conditions.get("temp", "unknown")
            feels_like = current_conditions.get("feelslike", "unknown")
            weather = (
                f"Currently in Albany, {conditions}. {temp}F. Feels like {feels_like}F."
            )

            print(f"[Sending] '{weather}' on channel '{channel_index}'")
            interface.sendText(weather, channelIndex=channel_index)
        except requests.exceptions.RequestException as e:
            print(f"[System] Request failed: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    config = load_config()

    pub.subscribe(on_receive, "meshtastic.receive")
    pub.subscribe(on_connection, "meshtastic.connection.established")

    print(f"[System] Connecting to Meshtastic device via '{config['serial_port']}'.")
    interface = meshtastic.serial_interface.SerialInterface(config["serial_port"])

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[System] Closing connection.")
        interface.close()
