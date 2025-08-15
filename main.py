import meshtastic.serial_interface
import time
from pubsub import pub
import os
import threading
import yaml
import requests
from dotenv import load_dotenv

CONFIG_FILE = "config.yml"
HA_PATH = "/api/states/"


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
                config["weather_temp_entity_id"],
                config["weather_humidity_entity_id"],
            ),
            daemon=True,
        ).start()


def ad(interface, channel_index, interval, text):
    print("[System] Ad enabled.")

    while True:
        print(f"[Sending] '{text}' on channel '{channel_index}'")
        # interface.sendText(text, channelIndex=channel_index)

        time.sleep(interval)


def beacon(interface, channel_index, interval, text):
    print("[System] Beacon enabled.")

    while True:
        print(f"[Sending] '{text}' on channel '{channel_index}'")
        # interface.sendText(text, channelIndex=channel_index)

        time.sleep(interval)


def get_ha_sensor_state(entity_id):
    ha_base = config["weather_home_assistant_base"]
    ha_token = os.getenv("HA_TOKEN")

    headers = {
        "Authorization": f"Bearer {ha_token}",
        "Content-Type": "application/json",
    }
    url = f"{ha_base}/api/states/{entity_id}"

    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()
    data = response.json()
    return {
        "state": data.get("state"),
        "unit": data["attributes"].get("unit_of_measurement"),
    }


def weather(interface, channel_index, interval, temp_entity_id, humidity_entity_id):
    print("[System] Weather enabled.")

    while True:
        try:
            temp_data = get_ha_sensor_state(temp_entity_id)
            temp = round(float(temp_data["state"]))
            humidity_data = get_ha_sensor_state(humidity_entity_id)
            humidity = round(float(humidity_data["state"]))

            weather = f"Currently in Albany, {temp}{temp_data['unit']}. Humidity {humidity}{humidity_data['unit']}."
            print(f"[Sending] '{weather}' on channel '{channel_index}'")
            interface.sendText(weather, channelIndex=channel_index)
        except requests.exceptions.RequestException as e:
            print(f"[System] Request failed: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    load_dotenv()
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
