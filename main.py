import meshtastic.serial_interface
import time
from pubsub import pub
import os
import threading
import yaml
import requests
from dotenv import load_dotenv
import metpy.calc as mpcalc
from metpy.units import units
import math

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


def beacon_worker(interface, job):
    print(f"[System] Beacon job started on channel {job['channel_index']}")

    while True:
        interface.sendText(job["text"], channelIndex=job["channel_index"])
        print(f"[Sending] Beacon: '{job['text']}' on channel {job['channel_index']}")
        time.sleep(job["interval"])


def weather_worker(interface, job):
    print(f"[System] Weather job started on channel {job['channel_index']}")

    while True:
        try:
            temp_data = get_ha_sensor_state(job["temp_entity_id"])
            temp = round(float(temp_data["state"]))
            humidity_data = get_ha_sensor_state(job["humidity_entity_id"])
            humidity = float(humidity_data["state"])
            heat_index = mpcalc.heat_index(temp * units.degF, humidity * units.percent)

            feels_like = temp
            if not math.isnan(float(heat_index.m)):
                feels_like = round(float(heat_index.m))

            msg = (
                f"Currently in {job['location_description']}, {temp}{temp_data['unit']}. "
                f"Feels like {feels_like}{temp_data['unit']}. "
                f"Humidity {round(humidity)}{humidity_data['unit']}."
            )
            interface.sendText(msg, channelIndex=job["channel_index"])
            print(f"[Sending] Weather: '{msg}' on channel {job['channel_index']}")
        except requests.exceptions.RequestException as e:
            print(f"[System] Weather job request failed: {e}")

        time.sleep(job["interval"])


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


def on_connection(interface, topic=pub.AUTO_TOPIC):
    print("[System] Serial connected!")
    start_jobs(interface, config)


JOB_DISPATCH = {
    "beacon": beacon_worker,
    "weather": weather_worker,
}


def start_jobs(interface, config):
    for job in config.get("outputs", []):
        if not job.get("active", True):
            print(f"[System] Job inactive: {job_type}, skipping")
            continue

        job_type = job.get("type")
        worker = JOB_DISPATCH.get(job_type)

        if worker:
            threading.Thread(target=worker, args=(interface, job), daemon=True).start()
        else:
            print(f"[System] Unknown job type: {job_type}")


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
