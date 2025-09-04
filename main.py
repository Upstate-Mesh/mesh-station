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


class Meshy:
    def __init__(self):
        logger.add("meshy.log", rotation="50 MB")
        load_dotenv()
        self.config = self.load_config()

    def start(self):
        logger.info(
            f"Connecting to Meshtastic device via '{self.config['serial_port']}'."
        )
        pub.subscribe(self.on_connection, "meshtastic.connection.established")
        interface = meshtastic.serial_interface.SerialInterface(
            self.config["serial_port"]
        )

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Closing connection, shutting down.")
            interface.close()

    def load_config(self):
        with open(CONFIG_FILE, "r") as f:
            return yaml.safe_load(f)

    def on_receive(self, packet, interface):
        if self.config["bot"]["active"] is False:
            return

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
            reply_text = self.handle_command(cmd)
            if not reply_text:
                logger.debug(
                    f"<- Unrecognized command from {from_id} ({cmd}), ignoring."
                )
                return

            logger.info(f"<- from {from_id}: {cmd}")
            logger.info(f"-> to {from_id}: {reply_text}")
            interface.sendText(reply_text, destinationId=from_id)
        except Exception as e:
            logger.error(f"Command error: {e}")

    def handle_command(self, cmd):
        action = self.config["bot"]["commands"][cmd]

        if action is None:
            return None

        if isinstance(action, str) and hasattr(self, action):
            method = getattr(self, action)
            reply_text = method()
        else:
            reply_text = action

        return reply_text

    def beacon_worker(self, interface, job):
        logger.info(f"Beacon job started on channel {job['channel_index']}")

        while True:
            interface.sendText(job["text"], channelIndex=job["channel_index"])
            logger.info(f"-> Beacon: '{job['text']}' on channel {job['channel_index']}")
            time.sleep(job["interval"])

    def weather_worker(self, interface, job):
        logger.info(f"Weather job started on channel {job['channel_index']}")

        while True:
            try:
                msg = self.get_weather_conditions(
                    job["temp_entity_id"],
                    job["humidity_entity_id"],
                    job["location_description"],
                )
                interface.sendText(msg, channelIndex=job["channel_index"])
                logger.info(f"-> Weather: '{msg}' on channel {job['channel_index']}")
            except requests.exceptions.RequestException as e:
                logger.info(f"Weather job request failed: {e}")

            time.sleep(job["interval"])

    def get_bot_weather_conditions(self):
        return self.get_weather_conditions(
            self.config["bot"]["temp_entity_id"],
            self.config["bot"]["humidity_entity_id"],
            self.config["bot"]["location_description"],
        )

    def get_weather_conditions(
        self, temp_entity_id, humidity_entity_id, location_description
    ):
        temp_data = self.get_ha_sensor_state(temp_entity_id)
        temp = round(float(temp_data["state"]))
        humidity_data = self.get_ha_sensor_state(humidity_entity_id)
        humidity = float(humidity_data["state"])
        heat_index = mpcalc.heat_index(temp * units.degF, humidity * units.percent)

        feels_like = temp
        magnitude = heat_index.m

        if not numpy.ma.is_masked(magnitude) and not math.isnan(float(magnitude)):
            feels_like = round(float(magnitude))

        return (
            f"Currently in {location_description}, {temp}{temp_data['unit']}. "
            f"Feels like {feels_like}{temp_data['unit']}. "
            f"Humidity {round(humidity)}{humidity_data['unit']}."
        )

    def get_ha_sensor_state(self, entity_id):
        ha_base = self.config["weather_home_assistant_base"]
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

    def on_connection(self, interface, topic=pub.AUTO_TOPIC):
        logger.info("Serial connected!")

        pub.subscribe(self.on_receive, "meshtastic.receive")
        self.start_jobs(interface)

    def start_jobs(self, interface):
        for job in self.config.get("interval_workers", []):
            job_type = job.get("type")

            if not job.get("active", True):
                logger.info(f"Job inactive: {job_type}, skipping")
                continue

            worker = getattr(self, job.get("dispatch"))

            if worker:
                logger.info("Sleeping 1 second to space out jobs.")
                time.sleep(1)
                threading.Thread(
                    target=worker, args=(interface, job), daemon=True
                ).start()
            else:
                logger.warning(f"Unknown job type: {job_type}")


if __name__ == "__main__":
    meshy = Meshy()
    meshy.start()
