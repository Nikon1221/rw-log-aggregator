#!/usr/bin/env python3
"""
rw-log-view  —  CLI-просмотр и запросы к базе логов
Запускается на vpn-panel

Usage:
    python3 viewer.py                         # топ сайтов за последний час
    python3 viewer.py --user someuser@rw      # история конкретного пользователя
    python3 viewer.py --domain google.com     # кто заходил на домен
    python3 viewer.py --hours 24              # за 24 часа
    python3 viewer.py --tail 50               # последние N соединений
    python3 viewer.py --stats                 # сводная статистика
    python3 viewer.py --serve                 # запустить веб-дашборд на :9877
"""

import argparse
import sqlite3
import os
from datetime import datetime, timedelta, timezone
from collections import defaultdict

DB_PATH = os.environ.get("LOG_DB", "/var/lib/rw-logs/logs.db")

# ─── Утилиты ──────────────────────────────────────────────────────────────────
def get_conn() -> sqlite3.Connection:
    if not os.path.exists(DB_PATH):
        print(f"[!] DB not found: {DB_PATH}")
        exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def since_iso(hours: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")

def fmt_row(r) -> str:
    ts       = r["ts"][:19] if r["ts"] else "?"
    email    = r["email"] or "anonymous"
    dest     = f"{r['dest_host']}:{r['dest_port']}" if r["dest_port"] else r["dest_host"] or "?"
    node     = r["node"] or "?"
    outbound = r["outbound"] or "?"
    return f"  {ts}  [{node:^4}]  {email:<30}  →  {dest:<40}  via {outbound}"

# ─── Команды ──────────────────────────────────────────────────────────────────
def cmd_tail(conn, n: int, hours: int):
    """Последние N соединений"""
    since = since_iso(hours)
    rows = conn.execute(
        "SELECT * FROM connections WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
        (since, n)
    ).fetchall()
    print(f"\n{'─'*100}")
    print(f"  {'Время':<19}  {'Нода':^4}  {'Пользователь':<30}  {'Назначение':<42}  Outbound")
    print(f"{'─'*100}")
    for r in reversed(rows):
        print(fmt_row(r))
    print(f"{'─'*100}")
    print(f"  Всего: {len(rows)} соединений за последние {hours}ч\n")

def cmd_user(conn, email: str, hours: int):
    """История конкретного пользователя"""
    since = since_iso(hours)
    rows = conn.execute(
        "SELECT * FROM connections WHERE email LIKE ? AND ts >= ? ORDER BY ts DESC LIMIT 200",
        (f"%{email}%", since)
    ).fetchall()
    if not rows:
        print(f"\n[!] Нет записей для пользователя '{email}' за последние {hours}ч")
        return
    # Уникальные домены
    domains = defaultdict(int)
    for r in rows:
        if r["dest_host"]:
            domains[r["dest_host"]] += 1
    print(f"\n{'─'*80}")
    print(f"  Пользователь: {rows[0]['email']}")
    print(f"  За последние {hours}ч: {len(rows)} соединений → {len(domains)} уникальных доменов")
    print(f"{'─'*80}")
    print(f"\n  Топ-20 доменов:")
    for domain, cnt in sorted(domains.items(), key=lambda x: -x[1])[:20]:
        bar = "█" * min(cnt, 40)
        print(f"  {cnt:>5}  {domain:<45}  {bar}")
    print(f"\n  Последние 20 соединений:")
    print(f"{'─'*100}")
    for r in rows[:20]:
        print(fmt_row(r))
    print()

def cmd_domain(conn, domain: str, hours: int):
    """Кто заходил на домен"""
    since = since_iso(hours)
    rows = conn.execute(
        "SELECT * FROM connections WHERE dest_host LIKE ? AND ts >= ? ORDER BY ts DESC LIMIT 500",
        (f"%{domain}%", since)
    ).fetchall()
    if not rows:
        print(f"\n[!] Нет записей для домена '{domain}' за последние {hours}ч")
        return
    users = defaultdict(int)
    for r in rows:
        users[r["email"] or "anonymous"] += 1
    print(f"\n{'─'*70}")
    print(f"  Домен: *{domain}*  |  {len(rows)} соединений за {hours}ч  |  {len(users)} пользователей")
    print(f"{'─'*70}")
    for email, cnt in sorted(users.items(), key=lambda x: -x[1]):
        bar = "█" * min(cnt, 40)
        print(f"  {cnt:>5}  {email:<40}  {bar}")
    print()

def cmd_stats(conn, hours: int):
    """Сводная статистика"""
    since = since_iso(hours)
    total = conn.execute("SELECT COUNT(*) FROM connections WHERE ts >= ?", (since,)).fetchone()[0]
    users = conn.execute(
        "SELECT COUNT(DISTINCT email) FROM connections WHERE ts >= ? AND email IS NOT NULL", (since,)
    ).fetchone()[0]
    domains = conn.execute(
        "SELECT COUNT(DISTINCT dest_host) FROM connections WHERE ts >= ?", (since,)
    ).fetchone()[0]
    print(f"\n{'═'*60}")
    print(f"  NikonVPN Log Stats — последние {hours}ч")
    print(f"{'═'*60}")
    print(f"  Соединений:          {total:>10}")
    print(f"  Уникальных юзеров:   {users:>10}")
    print(f"  Уникальных доменов:  {domains:>10}")

    print(f"\n  По нодам:")
    for row in conn.execute(
        "SELECT node, COUNT(*) as cnt FROM connections WHERE ts >= ? GROUP BY node ORDER BY cnt DESC",
        (since,)
    ):
        bar = "█" * min(row["cnt"] // max(total // 40, 1), 40)
        print(f"  {row['node']:>4}  {row['cnt']:>8}  {bar}")

    print(f"\n  Топ-20 доменов:")
    for row in conn.execute(
        "SELECT dest_host, COUNT(*) as cnt FROM connections WHERE ts >= ? AND dest_host IS NOT NULL "
        "GROUP BY dest_host ORDER BY cnt DESC LIMIT 20",
        (since,)
    ):
        bar = "█" * min(row["cnt"], 50)
        print(f"  {row['cnt']:>6}  {row['dest_host']:<45}  {bar}")

    print(f"\n  Топ-20 пользователей (по числу соединений):")
    for row in conn.execute(
        "SELECT email, COUNT(*) as cnt FROM connections WHERE ts >= ? AND email IS NOT NULL "
        "GROUP BY email ORDER BY cnt DESC LIMIT 20",
        (since,)
    ):
        bar = "█" * min(row["cnt"] // max(total // 40, 1), 40)
        print(f"  {row['cnt']:>6}  {row['email']:<45}  {bar}")

    print(f"\n  По outbound:")
    for row in conn.execute(
        "SELECT outbound, COUNT(*) as cnt FROM connections WHERE ts >= ? "
        "GROUP BY outbound ORDER BY cnt DESC",
        (since,)
    ):
        bar = "█" * min(row["cnt"] // max(total // 40, 1), 40)
        print(f"  {(row['outbound'] or 'null'):>12}  {row['cnt']:>8}  {bar}")

    print(f"{'═'*60}\n")

# ─── Веб-дашборд ──────────────────────────────────────────────────────────────
def cmd_serve(port: int = 9877):
    """Простой веб-дашборд"""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import json as json_mod

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_GET(self):
            conn = get_conn()
            hours = 24

            if self.path == "/api/stats":
                since = since_iso(hours)
                total = conn.execute("SELECT COUNT(*) FROM connections WHERE ts >= ?", (since,)).fetchone()[0]

                top_domains = [
                    {"domain": r["dest_host"], "count": r["cnt"]}
                    for r in conn.execute(
                        "SELECT dest_host, COUNT(*) as cnt FROM connections "
                        "WHERE ts >= ? AND dest_host IS NOT NULL "
                        "GROUP BY dest_host ORDER BY cnt DESC LIMIT 30", (since,)
                    )
                ]
                top_users = [
                    {"email": r["email"], "count": r["cnt"]}
                    for r in conn.execute(
                        "SELECT email, COUNT(*) as cnt FROM connections "
                        "WHERE ts >= ? AND email IS NOT NULL "
                        "GROUP BY email ORDER BY cnt DESC LIMIT 20", (since,)
                    )
                ]
                nodes = [
                    {"node": r["node"], "count": r["cnt"]}
                    for r in conn.execute(
                        "SELECT node, COUNT(*) as cnt FROM connections "
                        "WHERE ts >= ? GROUP BY node ORDER BY cnt DESC", (since,)
                    )
                ]
                body = json_mod.dumps({
                    "total": total, "hours": hours,
                    "top_domains": top_domains,
                    "top_users": top_users,
                    "nodes": nodes,
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            elif self.path == "/":
                html = build_html()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode())
            else:
                self.send_response(404)
                self.end_headers()
            conn.close()

    print(f"[dashboard] http://localhost:{port}")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

def build_html() -> str:
    return """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>NikonVPN Logs</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:monospace;background:#0d1117;color:#c9d1d9;padding:20px}
  h1{color:#58a6ff;margin-bottom:20px;font-size:1.4em}
  h2{color:#8b949e;margin:20px 0 10px;font-size:1em;text-transform:uppercase;letter-spacing:.1em}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
  .card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}
  .stat{font-size:2em;color:#58a6ff;font-weight:bold}
  .stat-label{color:#8b949e;font-size:.8em}
  table{width:100%;border-collapse:collapse;font-size:.85em}
  th{text-align:left;color:#8b949e;border-bottom:1px solid #30363d;padding:4px 8px}
  td{padding:4px 8px;border-bottom:1px solid #21262d}
  tr:hover td{background:#1c2128}
  .bar{display:inline-block;background:#1f6feb;height:10px;border-radius:2px;vertical-align:middle}
  .node-ru{color:#3fb950}.node-pl{color:#d29922}.node-de{color:#f85149}
  #refresh{position:fixed;top:20px;right:20px;background:#238636;color:#fff;
           border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:.9em}
</style>
</head>
<body>
<h1>🛡 NikonVPN · Log Dashboard</h1>
<button id="refresh" onclick="load()">↻ Обновить</button>

<div class="grid" id="counters"></div>

<div class="grid" style="margin-top:20px">
  <div class="card">
    <h2>Топ доменов (24ч)</h2>
    <table id="tDomains"><tr><th>#</th><th>Домен</th><th>Соед.</th><th></th></tr></table>
  </div>
  <div class="card">
    <h2>Топ пользователей (24ч)</h2>
    <table id="tUsers"><tr><th>#</th><th>Email</th><th>Соед.</th><th></th></tr></table>
  </div>
</div>

<script>
async function load() {
  const r = await fetch('/api/stats');
  const d = await r.json();

  // Счётчики
  const nodesHtml = d.nodes.map(n =>
    `<div class="card">
       <div class="stat node-${n.node}">${n.count}</div>
       <div class="stat-label">нода ${n.node.toUpperCase()}</div>
     </div>`
  ).join('');
  document.getElementById('counters').innerHTML =
    `<div class="card"><div class="stat">${d.total}</div><div class="stat-label">всего соединений (${d.hours}ч)</div></div>` + nodesHtml;

  // Домены
  const maxD = d.top_domains[0]?.count || 1;
  document.getElementById('tDomains').innerHTML =
    '<tr><th>#</th><th>Домен</th><th>Соед.</th><th></th></tr>' +
    d.top_domains.map((x,i) =>
      `<tr><td>${i+1}</td><td>${x.domain}</td><td>${x.count}</td>
       <td><span class="bar" style="width:${Math.round(x.count/maxD*120)}px"></span></td></tr>`
    ).join('');

  // Пользователи
  const maxU = d.top_users[0]?.count || 1;
  document.getElementById('tUsers').innerHTML =
    '<tr><th>#</th><th>Email</th><th>Соед.</th><th></th></tr>' +
    d.top_users.map((x,i) =>
      `<tr><td>${i+1}</td><td>${x.email}</td><td>${x.count}</td>
       <td><span class="bar" style="width:${Math.round(x.count/maxU*120)}px"></span></td></tr>`
    ).join('');
}
load();
setInterval(load, 30000);
</script>
</body>
</html>"""

# ─── Entry point ──────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="NikonVPN log viewer")
    p.add_argument("--db",     default=DB_PATH, help="путь к SQLite DB")
    p.add_argument("--hours",  type=int, default=24, help="за сколько часов смотреть")
    p.add_argument("--user",   help="фильтр по email пользователя")
    p.add_argument("--domain", help="фильтр по домену")
    p.add_argument("--tail",   type=int, default=0, help="последние N соединений")
    p.add_argument("--stats",  action="store_true", help="сводная статистика")
    p.add_argument("--serve",  action="store_true", help="веб-дашборд")
    p.add_argument("--port",   type=int, default=9877)
    args = p.parse_args()

    global DB_PATH
    DB_PATH = args.db

    if args.serve:
        cmd_serve(args.port)
        return

    conn = get_conn()
    if args.user:
        cmd_user(conn, args.user, args.hours)
    elif args.domain:
        cmd_domain(conn, args.domain, args.hours)
    elif args.tail:
        cmd_tail(conn, args.tail, args.hours)
    else:
        cmd_stats(conn, args.hours)
    conn.close()

if __name__ == "__main__":
    main()
