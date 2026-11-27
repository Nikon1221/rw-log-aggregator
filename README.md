# rw-log-aggregator

Лёгкая система сбора и агрегации access-логов Xray со всех нод Remnawave в одно место. Всё работает в **Docker Compose** на стандартной библиотеке Python (без внешних зависимостей).

```
[Node 1]  rw-log-shipper ──┐
[Node 2]  rw-log-shipper ──┼──► rw-log-collector (Panel:9876) ──► SQLite (logs.db) ──► rw-log-viewer (:9877)
[Node N]  rw-log-shipper ──┘
```

## Компоненты

| Файл | Назначение |
|------|------------|
| `Dockerfile` | Минимальный образ на `python:3.12-alpine` |
| `docker-compose.panel.yml` | Стек для сервера панели: `collector` + `viewer` |
| `docker-compose.node.yml` | Стек для нод: `shipper` |
| `collector.py` | HTTP-сервер, принимает батчи от нод и пишет в SQLite |
| `shipper.py` | Тейлит `access.log` Xray, парсит строки и шлёт батчами |
| `viewer.py` | CLI-утилита для поиска и веб-дашборд |

---

## Шаг 1 — Подготовка нод и включение access-лога Xray

1. На **каждой ноде** убедись, что в `docker-compose.yml` контейнера `remnanode` примонтирована папка для логов:
   ```yaml
   volumes:
     - '/var/log/remnanode:/var/log/remnanode'
   ```
   Если добавлял строку впервые — примени изменения:
   ```bash
   docker compose up -d
   ```

2. В панели Remnawave открой профиль Xray для нод и добавь секцию `log`:
   ```json
   "log": {
     "access": "/var/log/remnanode/access.log",
     "error":  "/var/log/remnanode/error.log",
     "loglevel": "warning"
   }
   ```
   После сохранения Remnawave перезапустит Xray, и в `/var/log/remnanode/access.log` на хосте начнут появляться записи.

---

## Шаг 2 — Развёртывание на сервере панели (Collector + Viewer)

```bash
git clone https://github.com/Nikon1221/rw-log-aggregator.git /opt/rw-log-aggregator
cd /opt/rw-log-aggregator
cp .env.example .env
```

1. Сгенерируй случайный токен и впиши его в `.env`:
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   nano .env   # заполни LOG_COLLECTOR_TOKEN=<ТВОЙ_ТОКЕН>
   ```

2. Разреши порт `9876` в файрволе **только** для IP-адресов твоих нод:
   ```bash
   sudo ufw allow from <NODE_1_IP> to any port 9876
   sudo ufw allow from <NODE_2_IP> to any port 9876
   ```

3. Запусти контейнеры:
   ```bash
   docker compose -f docker-compose.panel.yml up -d --build
   ```

4. Проверь, что коллектор отвечает:
   ```bash
   curl http://127.0.0.1:9876/health
   # ожидаемый ответ: {"ok":true}
   ```

---

## Шаг 3 — Развёртывание на каждой ноде (Shipper)

Выполни на каждой ноде:

```bash
git clone https://github.com/Nikon1221/rw-log-aggregator.git /opt/rw-log-aggregator
cd /opt/rw-log-aggregator
cp .env.example .env
nano .env
```

Заполни `.env`:
```env
LOG_COLLECTOR_TOKEN=<ТОТ_ЖЕ_ТОКЕН_ЧТО_НА_ПАНЕЛИ>
LOG_NODE_NAME=node-1               # уникальное имя ноды (напр. ru, pl, de)
LOG_COLLECTOR_URL=http://<PANEL_IP>:9876/ingest
HOST_LOG_DIR=/var/log/remnanode
```

Запусти контейнер:
```bash
docker compose -f docker-compose.node.yml up -d --build
docker compose -f docker-compose.node.yml logs -f
```

---

## Шаг 4 — Просмотр логов и веб-дашборд

### Веб-дашборд
По соображениям безопасности `viewer` слушает только `127.0.0.1:9877` на сервере панели. Чтобы открыть его в браузере, пробрось порт через SSH-туннель со своего ПК:

```bash
ssh -N -L 9877:127.0.0.1:9877 -p <SSH_PORT> <USER>@<PANEL_IP>
```
После этого открой: `http://localhost:9877`

### CLI прямо внутри контейнера
```bash
# Сводная статистика за 24 часа
docker exec rw-log-viewer python3 /app/viewer.py --stats

# Последние 50 соединений
docker exec rw-log-viewer python3 /app/viewer.py --tail 50

# История конкретного пользователя (за последние 6 часов)
docker exec rw-log-viewer python3 /app/viewer.py --user <EMAIL_OR_ID> --hours 6

# Кто заходил на конкретный домен
docker exec rw-log-viewer python3 /app/viewer.py --domain google.com
```

---

## Обновление

```bash
cd /opt/rw-log-aggregator
git pull

# На панели:
docker compose -f docker-compose.panel.yml up -d --build

# На нодах:
docker compose -f docker-compose.node.yml up -d --build
```

---

## Полное удаление (после завершения теста)

```bash
# На каждой ноде:
cd /opt/rw-log-aggregator && docker compose -f docker-compose.node.yml down -v
rm -rf /opt/rw-log-aggregator

# На сервере панели:
cd /opt/rw-log-aggregator && docker compose -f docker-compose.panel.yml down -v
rm -rf /opt/rw-log-aggregator
```
И в профиле Xray в Remnawave удали (или верни на `"none"`) пути `access` и `error` в секции `log`.
