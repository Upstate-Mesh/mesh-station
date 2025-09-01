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
from loguru import logger
import numpy

CONFIG_FILE = "config.yml"
HA_PATH = "/api/states/"
COMMANDS = {
    ".about": "I'm a sentient Meshtastic bot 🤖",
    ".help": "Available commands: .about .help .ping",
    ".ping": "pong!",
}


def load_config():
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)


def on_receive(packet, interface):
    try:
        decoded = packet.get("decoded", {})
        text = decoded.get("text")

        if not text:
            return

        from_id = packet.get("fromId")
        my_id = interface.myInfo.my_node_num

        # only reply to DMs
        if packet.get("channel", 0) != 0 or from_id == my_id:
            return

        cmd = text.strip().lower()
        if cmd not in COMMANDS:
            logger.debug(f"<- Unrecognized command from {from_id} ({cmd}), ignoring.")
            return

        logger.info(f"<- from {from_id}: {cmd}")

        reply_text = COMMANDS[cmd]
        logger.info(f"-> to {from_id}: {reply_text}")
        interface.sendText(reply_text, destinationId=from_id)
    except Exception as e:
        logger.error(f"Error decoding packet: {e}")


def beacon_worker(interface, job):
    logger.info(f"Beacon job started on channel {job['channel_index']}")

    while True:
        interface.sendText(job["text"], channelIndex=job["channel_index"])
        logger.info(f"-> Beacon: '{job['text']}' on channel {job['channel_index']}")
        time.sleep(job["interval"])


def weather_worker(interface, job):
    logger.info(f"Weather job started on channel {job['channel_index']}")

    while True:
        try:
            temp_data = get_ha_sensor_state(job["temp_entity_id"])
            temp = round(float(temp_data["state"]))
            humidity_data = get_ha_sensor_state(job["humidity_entity_id"])
            humidity = float(humidity_data["state"])
            heat_index = mpcalc.heat_index(temp * units.degF, humidity * units.percent)

            feels_like = temp
            magnitude = heat_index.m

            if not numpy.ma.is_masked(magnitude) and not math.isnan(float(magnitude)):
                feels_like = round(float(magnitude))

            msg = (
                f"Currently in {job['location_description']}, {temp}{temp_data['unit']}. "
                f"Feels like {feels_like}{temp_data['unit']}. "
                f"Humidity {round(humidity)}{humidity_data['unit']}."
            )
            interface.sendText(msg, channelIndex=job["channel_index"])
            logger.info(f"-> Weather: '{msg}' on channel {job['channel_index']}")
        except requests.exceptions.RequestException as e:
            logger.info(f"Weather job request failed: {e}")

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
    logger.info("Serial connected!")

    pub.subscribe(on_receive, "meshtastic.receive")
    start_jobs(interface, config)


JOB_DISPATCH = {
    "beacon": beacon_worker,
    "weather": weather_worker,
}


def start_jobs(interface, config):
    for job in config.get("outputs", []):
        job_type = job.get("type")

        if not job.get("active", True):
            logger.info(f"Job inactive: {job_type}, skipping")
            continue

        worker = JOB_DISPATCH.get(job_type)

        if worker:
            logger.info("Sleeping 1 second to space out jobs.")
            time.sleep(1)
            threading.Thread(target=worker, args=(interface, job), daemon=True).start()
        else:
            logger.warning(f"Unknown job type: {job_type}")


if __name__ == "__main__":
    logger.add("mesh_station.log", rotation="50 MB")
    load_dotenv()
    config = load_config()

    pub.subscribe(on_connection, "meshtastic.connection.established")

    logger.info(f"Connecting to Meshtastic device via '{config['serial_port']}'.")
    interface = meshtastic.serial_interface.SerialInterface(config["serial_port"])

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Closing connection, shutting down.")
        interface.close()
