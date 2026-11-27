#!/usr/bin/env python3
"""
rw-log-collector  —  центральный сборщик логов Xray
Запускается на сервере панели управления
Принимает POST от log-shipper'ов со всех нод, пишет в SQLite

Usage:
    python3 collector.py [--port 9876] [--db /var/lib/rw-logs/logs.db]
"""

import argparse
import json
import sqlite3
import logging
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Lock
import os

# ─── Config ───────────────────────────────────────────────────────────────────
DEFAULT_HOST = os.environ.get("LOG_COLLECTOR_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.environ.get("LOG_COLLECTOR_PORT", "9876"))
DEFAULT_DB   = os.environ.get("LOG_DB", "/var/lib/rw-logs/logs.db")
API_TOKEN    = os.environ.get("LOG_COLLECTOR_TOKEN", "changeme-secret-token")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [collector] %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)

# ─── Database ─────────────────────────────────────────────────────────────────
DB_LOCK = Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,          -- ISO8601 timestamp из лога
    node        TEXT    NOT NULL,          -- ru / pl / de
    email       TEXT,                      -- идентификатор пользователя (email в VLESS)
    src_ip      TEXT,                      -- IP клиента
    dest_host   TEXT,                      -- куда подключился (домен или IP)
    dest_port   INTEGER,                   -- порт назначения
    network     TEXT,                      -- tcp / udp
    outbound    TEXT,                      -- тег outbound (proxy, direct, warp, ...)
    raw         TEXT                       -- сырая строка лога
);

CREATE INDEX IF NOT EXISTS idx_ts     ON connections(ts);
CREATE INDEX IF NOT EXISTS idx_email  ON connections(email);
CREATE INDEX IF NOT EXISTS idx_dest   ON connections(dest_host);
CREATE INDEX IF NOT EXISTS idx_node   ON connections(node);
"""

def init_db(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn

def insert_batch(conn: sqlite3.Connection, records: list[dict]):
    rows = []
    for r in records:
        rows.append((
            r.get("ts", ""),
            r.get("node", "unknown"),
            r.get("email"),
            r.get("src_ip"),
            r.get("dest_host"),
            r.get("dest_port"),
            r.get("network"),
            r.get("outbound"),
            r.get("raw"),
        ))
    with DB_LOCK:
        conn.executemany(
            "INSERT INTO connections (ts, node, email, src_ip, dest_host, dest_port, network, outbound, raw) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows
        )
        conn.commit()
    return len(rows)

# ─── HTTP Handler ──────────────────────────────────────────────────────────────
def make_handler(conn: sqlite3.Connection):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # заглушаем стандартные access-логи HTTP

        def do_POST(self):
            # Авторизация по токену
            token = self.headers.get("X-Log-Token", "")
            if token != API_TOKEN:
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b'{"error":"forbidden"}')
                return

            if self.path != "/ingest":
                self.send_response(404)
                self.end_headers()
                return

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)

            try:
                data = json.loads(body)
                records = data.get("records", [])
                if not isinstance(records, list):
                    raise ValueError("records must be a list")
                n = insert_batch(conn, records)
                log.info("Inserted %d records from node=%s", n, records[0].get("node", "?") if records else "?")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "inserted": n}).encode())
            except Exception as e:
                log.error("Failed to insert batch: %s", e)
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())

        def do_GET(self):
            # Healthcheck
            if self.path == "/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            else:
                self.send_response(404)
                self.end_headers()

    return Handler

# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db",   default=DEFAULT_DB)
    args = parser.parse_args()

    conn = init_db(args.db)
    log.info("DB initialized at %s", args.db)

    server = HTTPServer((args.host, args.port), make_handler(conn))
    log.info("Collector listening on %s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()

if __name__ == "__main__":
    main()
