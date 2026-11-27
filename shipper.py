#!/usr/bin/env python3
"""
rw-log-shipper  —  читает access.log Xray на ноде, парсит, шлёт на collector
Запускается на каждой ноде (RU / PL / DE) как systemd-сервис или cron

Поддерживает два формата:
  1. Стандартный Xray access log:
     2026/01/01 12:00:00 from 1.2.3.4:5678 accepted email: user@rw [tcp] >> google.com:443 [proxy]
  2. JSON-лог (если xray настроен с "logFormat": "json")

Env vars:
    LOG_NODE_NAME       — название ноды (ru / pl / de), обязателен
    LOG_COLLECTOR_URL   — URL коллектора, напр. http://<PANEL_IP>:9876/ingest
    LOG_COLLECTOR_TOKEN — токен авторизации
    LOG_ACCESS_FILE     — путь к access.log (default: /var/log/remnanode/access.log)
    LOG_BATCH_SIZE      — сколько строк слать за раз (default: 50)
    LOG_INTERVAL        — интервал опроса в сек (default: 5)
    LOG_STATE_FILE      — файл с позицией чтения (default: /var/lib/rw-logs/shipper.pos)
"""

import os
import re
import json
import time
import logging
import urllib.request
import urllib.error
from datetime import datetime

# ─── Config из env ─────────────────────────────────────────────────────────────
NODE_NAME       = os.environ.get("LOG_NODE_NAME", "unknown")
COLLECTOR_URL   = os.environ.get("LOG_COLLECTOR_URL", "http://127.0.0.1:9876/ingest")
COLLECTOR_TOKEN = os.environ.get("LOG_COLLECTOR_TOKEN", "changeme-secret-token")
ACCESS_FILE     = os.environ.get("LOG_ACCESS_FILE", "/var/log/remnanode/access.log")
BATCH_SIZE      = int(os.environ.get("LOG_BATCH_SIZE", "50"))
INTERVAL        = int(os.environ.get("LOG_INTERVAL", "5"))
STATE_FILE      = os.environ.get("LOG_STATE_FILE", "/var/lib/rw-logs/shipper.pos")

logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [shipper/{NODE_NAME}] %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)

# ─── Парсер строки Xray access log ────────────────────────────────────────────
# Пример:
# 2026/11/27 12:34:56 from 1.2.3.4:50123 accepted email: someuser@remnawave [tcp] >> example.com:443 [proxy]
PATTERN = re.compile(
    r"^(?P<date>\d{4}/\d{2}/\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2})"
    r"\s+from\s+(?P<src>[^\s]+)"
    r"\s+accepted"
    r"(?:\s+email:\s+(?P<email>\S+))?"
    r"\s+\[(?P<network>[^\]]+)\]"
    r"\s+>>\s+(?P<dest>[^\s]+)"
    r"\s+\[(?P<outbound>[^\]]+)\]"
)

def parse_line(line: str) -> dict | None:
    """Парсит строку access.log. Возвращает dict или None если не подходит."""
    line = line.strip()
    if not line:
        return None

    # Попытка JSON-формата
    if line.startswith("{"):
        try:
            obj = json.loads(line)
            if obj.get("type") != "access":
                return None
            dest = obj.get("to", "")
            host, _, port_s = dest.rpartition(":")
            return {
                "ts":        obj.get("time", ""),
                "node":      NODE_NAME,
                "email":     obj.get("email"),
                "src_ip":    obj.get("from", "").rsplit(":", 1)[0],
                "dest_host": host or dest,
                "dest_port": int(port_s) if port_s.isdigit() else None,
                "network":   obj.get("network"),
                "outbound":  obj.get("route"),
                "raw":       line,
            }
        except (json.JSONDecodeError, KeyError):
            return None

    # Стандартный текстовый формат
    m = PATTERN.match(line)
    if not m:
        return None

    dest_full = m.group("dest")
    host, _, port_s = dest_full.rpartition(":")

    # Убираем скобки из IP если вдруг IPv6 типа [::1]:443
    host = host.strip("[]") if host else dest_full

    src_full = m.group("src")
    src_ip, _, _ = src_full.rpartition(":")

    ts_str = f"{m.group('date')} {m.group('time')}"
    try:
        ts = datetime.strptime(ts_str, "%Y/%m/%d %H:%M:%S").isoformat()
    except ValueError:
        ts = ts_str

    return {
        "ts":        ts,
        "node":      NODE_NAME,
        "email":     m.group("email"),
        "src_ip":    src_ip,
        "dest_host": host,
        "dest_port": int(port_s) if port_s.isdigit() else None,
        "network":   m.group("network"),
        "outbound":  m.group("outbound"),
        "raw":       line,
    }

# ─── Позиция чтения файла ──────────────────────────────────────────────────────
def load_pos() -> int:
    try:
        return int(open(STATE_FILE).read().strip())
    except (FileNotFoundError, ValueError):
        return 0

def save_pos(pos: int):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        f.write(str(pos))

def get_file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except FileNotFoundError:
        return 0

# ─── Отправка батча ───────────────────────────────────────────────────────────
def ship_batch(records: list[dict]) -> bool:
    if not records:
        return True
    payload = json.dumps({"records": records}).encode()
    req = urllib.request.Request(
        COLLECTOR_URL,
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "X-Log-Token":   COLLECTOR_TOKEN,
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
            return True
    except urllib.error.URLError as e:
        log.warning("Failed to ship batch: %s", e)
        return False

# ─── Главный цикл ─────────────────────────────────────────────────────────────
def tail_and_ship():
    pos = load_pos()
    log.info("Starting shipper, file=%s pos=%d", ACCESS_FILE, pos)

    while True:
        size = get_file_size(ACCESS_FILE)

        # Файл ротировался (меньше нашей позиции) → читаем с начала
        if size < pos:
            log.info("File rotated, resetting pos to 0")
            pos = 0
            save_pos(0)

        if size > pos:
            try:
                with open(ACCESS_FILE, "r", errors="replace") as f:
                    f.seek(pos)
                    batch = []
                    while True:
                        line = f.readline()
                        if not line:
                            break
                        record = parse_line(line)
                        if record:
                            batch.append(record)
                        if len(batch) >= BATCH_SIZE:
                            if ship_batch(batch):
                                pos = f.tell()
                                save_pos(pos)
                                batch = []
                            else:
                                # не двигаем pos — попробуем позже
                                break
                    # остаток
                    if batch and ship_batch(batch):
                        pos = f.tell()
                        save_pos(pos)
            except FileNotFoundError:
                pass

        time.sleep(INTERVAL)

if __name__ == "__main__":
    tail_and_ship()
