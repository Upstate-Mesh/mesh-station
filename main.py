import math
import os
import threading
import time

import meshtastic.serial_interface
import metpy.calc as mpcalc
import numpy
import requests
import yaml
from dotenv import load_dotenv
from loguru import logger
from metpy.units import units
from pubsub import pub

from db import NodeDB
from scheduled_worker import ScheduledWorker

CONFIG_FILE = "config.yml"
HA_PATH = "/api/states/"


class Meshy:
    def __init__(self):
        logger.add("meshy.log", rotation="50 MB")
        load_dotenv()
        self.config = self.load_config()

        if self.config["save_node_db"]:
            self.db = NodeDB()

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

            for worker_job in self.worker_jobs:
                worker_job.stop()
            interface.close()

    def load_config(self):
        with open(CONFIG_FILE, "r") as f:
            return yaml.safe_load(f)

    def on_receive(self, packet, interface):
        if self.db is not None:
            self.observe_node(packet, interface)

        if self.config["bot"]["active"] is False:
            return

        my_id = interface.myInfo.my_node_num
        my_id_encoded = f"!{my_id:08x}"

        try:
            decoded = packet.get("decoded", {})
            from_id = packet.get("fromId")
            to_id = packet.get("toId")

            # only reply to DMs
            if to_id != my_id_encoded or from_id == my_id_encoded:
                return

            text = decoded.get("text", "")

            if not text:
                return

            cmd = text.strip().lower()
            reply_text = self.handle_command(cmd)
            if reply_text is None:
                logger.debug(
                    f"<- Unrecognized command from {from_id} ({cmd}), ignoring."
                )
                return

            logger.info(f"<- from {from_id}: {cmd}")
            logger.info(f"-> to {from_id}: {reply_text}")
            interface.sendText(reply_text, destinationId=from_id)
        except Exception as e:
            logger.error(f"Command error: {e}")

    def observe_node(self, packet, interface):
        try:
            from_id = packet.get("fromId", "unknown")
            portnum = packet.get("decoded", {}).get("portnum")

            if portnum != "NODEINFO_APP":
                return

            node = interface.nodes.get(from_id)
            if node and "user" in node and "longName" in node["user"]:
                short_name = node["user"]["shortName"]
                long_name = node["user"]["longName"]
                self.db.upsert_node(from_id, short_name, long_name)
        except Exception as e:
            logger.error(f"Error decoding packet: {e}")

    def handle_command(self, cmd):
        commands = self.config.get("bot", {}).get("commands", {})
        action = commands.get(cmd)

        if action is None:
            return None

        if isinstance(action, str) and hasattr(self, action):
            method = getattr(self, action)
            reply_text = method()
        else:
            reply_text = action

        return reply_text

    def get_beacon_worker(self, interface, job):
        interface.sendText(job["text"], channelIndex=job["channel_index"])
        logger.info(f"-> Beacon: '{job['text']}' on channel {job['channel_index']}")

    def get_weather_conditions_worker(self, interface, job):
        try:
            msg = self.get_conditions(
                job["temp_entity_id"],
                job["humidity_entity_id"],
                job["location_description"],
            )
            interface.sendText(msg, channelIndex=job["channel_index"])
            logger.info(f"-> Weather: '{msg}' on channel {job['channel_index']}")
        except requests.exceptions.RequestException as e:
            logger.info(f"Weather job request failed: {e}")

    def get_seen_nodes(self):
        if self.db is None:
            return "Command inactive."

        seen_nodes = self.db.get_seen_nodes()

        if len(seen_nodes) == 0:
            return "No nodes seen."

        # TODO make this useful
        n = seen_nodes[0]
        return f"Most recently seen node:\n{n['long_name']} / {n['short_name']} / {n['id']}"

    def get_weather_forecast(self):
        weather_config = self.config.get("weather").get("forecast")
        url = weather_config.get("url")
        user_agent = weather_config.get("user_agent")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": user_agent,
        }

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        periods = data.get("properties", {}).get("periods", [])
        if len(periods) == 0:
            return "Forecast unavailable."

        period = periods[0]
        name = period.get("name")
        detailed_forecast = period.get("detailedForecast")
        return f"{name}: {detailed_forecast}"

    def get_weather_conditions(self):
        return self.get_conditions(
            self.config["bot"]["temp_entity_id"],
            self.config["bot"]["humidity_entity_id"],
            self.config["bot"]["location_description"],
        )

    def get_conditions(self, temp_entity_id, humidity_entity_id, location_description):
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
        ha_base = self.config["home_assistant_base"]
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
        self.worker_jobs = []

        for job in self.config.get("workers", []):
            job_type = job.get("type")

            if not job.get("active", True):
                logger.info(f"Job inactive: {job_type}, skipping")
                continue

            worker = getattr(self, job.get("dispatch"))

            if worker:
                cron = job.get("cron")
                threaded_worker = ScheduledWorker(cron, worker, interface, job)
                threaded_worker.start()
                self.worker_jobs.append(threaded_worker)
                logger.info(f"{job_type} job started in thread with schedule: {cron}")
            else:
                logger.warning(f"Unknown job type: {job_type}")


if __name__ == "__main__":
    meshy = Meshy()
    meshy.start()
