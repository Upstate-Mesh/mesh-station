import threading
import time
from datetime import datetime

from croniter import croniter
from loguru import logger


class ScheduledWorker:
    def __init__(self, cron_expr, func, *args, **kwargs):
        self.cron_expr = cron_expr
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self._thread = None
        self._stop_event = threading.Event()

    def _runner(self):
        cron = croniter(self.cron_expr, datetime.now())

        while not self._stop_event.is_set():
            next_run = cron.get_next(datetime)
            sleep_seconds = (next_run - datetime.now()).total_seconds()

            if sleep_seconds > 0:
                end_time = time.time() + sleep_seconds

                while time.time() < end_time:
                    if self._stop_event.is_set():
                        return
                    time.sleep(min(1, end_time - time.time()))
            try:
                self.func(*self.args, **self.kwargs)
            except Exception as e:
                logger.error(f"Worker error in {self.func.__name__}: {e}")

    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._runner, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop_event.set()

        if self._thread:
            self._thread.join(timeout=2)
