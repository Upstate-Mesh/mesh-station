import sqlite3
from datetime import datetime

from loguru import logger


class NodeDB:
    def __init__(self, db_file="nodes.db"):
        self.db_file = db_file
        self._init_db()

    def _get_conn(self):
        return sqlite3.connect(self.db_file)

    def _init_db(self):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                short_name TEXT,
                long_name TEXT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        conn.commit()
        conn.close()

    def upsert_node(self, node_id, short_name, long_name):
        last4 = node_id[-4:] if len(node_id) > 4 else node_id

        conn = sqlite3.connect(self.db_file)
        c = conn.cursor()

        c.execute("SELECT short_name, long_name FROM nodes WHERE id = ?", (last4,))
        row = c.fetchone()

        if row is None:
            c.execute(
                """
                INSERT INTO nodes (id, short_name, long_name, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?)
            """,
                (last4, short_name, long_name, datetime.utcnow(), datetime.utcnow()),
            )
            logger.info(f"New node seen: {long_name}/{short_name} ({last4})")
        else:
            old_short, old_long = row
            new_short = short_name if short_name else old_short
            new_long = long_name if long_name else old_long

            if new_short != old_short or new_long != old_long:
                c.execute(
                    """
                    UPDATE nodes
                    SET short_name = ?, long_name = ?, last_seen = ?
                    WHERE id = ?
                """,
                    (new_short, new_long, datetime.utcnow(), last4),
                )
                logger.info(
                    f"Updated node already seen: {new_long}/{new_short} ({last4})"
                )
            else:
                c.execute(
                    """
                    UPDATE nodes
                    SET last_seen = ?
                    WHERE id = ?
                """,
                    (datetime.utcnow(), last4),
                )

        conn.commit()
        conn.close()

    def get_seen_nodes(self):
        conn = self._get_conn()
        c = conn.cursor()

        c.execute("SELECT id, short_name, long_name FROM nodes ORDER BY last_seen DESC")
        rows = c.fetchall()
        return [
            {
                "id": row[0],
                "short_name": row[1],
                "long_name": row[2],
            }
            for row in rows
        ]

        conn.close()
