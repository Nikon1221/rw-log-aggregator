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
def _build_where(qs: dict) -> tuple[str, list]:
    hours = int(qs.get("hours", ["24"])[0] or 24)
    clauses = ["ts >= ?"]
    params: list = [since_iso(hours)]

    node = (qs.get("node", [""])[0] or "").strip()
    if node:
        clauses.append("node = ?")
        params.append(node)

    user = (qs.get("user", [""])[0] or "").strip()
    if user:
        clauses.append("email LIKE ?")
        params.append(f"%{user}%")

    domain = (qs.get("domain", [""])[0] or "").strip()
    if domain:
        clauses.append("dest_host LIKE ?")
        params.append(f"%{domain}%")

    outbound = (qs.get("outbound", [""])[0] or "").strip()
    if outbound:
        clauses.append("outbound = ?")
        params.append(outbound)

    return " AND ".join(clauses), params


def cmd_serve(port: int = 9877):
    """Веб-дашборд с фильтрами и лентой последних подключений"""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from urllib.parse import urlparse, parse_qs
    import json as json_mod

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _json(self, data: dict):
            body = json_mod.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            conn = get_conn()

            try:
                hours = int(qs.get("hours", ["24"])[0] or 24)
            except ValueError:
                hours = 24

            if parsed.path == "/api/stats":
                where, params = _build_where(qs)
                since = since_iso(hours)

                total = conn.execute(
                    f"SELECT COUNT(*) FROM connections WHERE {where}", params
                ).fetchone()[0]

                top_domains = [
                    {"domain": r["dest_host"], "count": r["cnt"]}
                    for r in conn.execute(
                        f"SELECT dest_host, COUNT(*) as cnt FROM connections "
                        f"WHERE {where} AND dest_host IS NOT NULL "
                        f"GROUP BY dest_host ORDER BY cnt DESC LIMIT 30", params
                    )
                ]
                top_users = [
                    {"email": r["email"], "count": r["cnt"]}
                    for r in conn.execute(
                        f"SELECT email, COUNT(*) as cnt FROM connections "
                        f"WHERE {where} AND email IS NOT NULL "
                        f"GROUP BY email ORDER BY cnt DESC LIMIT 20", params
                    )
                ]
                nodes = [
                    {"node": r["node"], "count": r["cnt"]}
                    for r in conn.execute(
                        "SELECT node, COUNT(*) as cnt FROM connections "
                        "WHERE ts >= ? GROUP BY node ORDER BY cnt DESC", (since,)
                    )
                ]
                users_list = [
                    r["email"] for r in conn.execute(
                        "SELECT DISTINCT email FROM connections "
                        "WHERE ts >= ? AND email IS NOT NULL ORDER BY email", (since,)
                    )
                ]
                outbounds_list = [
                    r["outbound"] for r in conn.execute(
                        "SELECT DISTINCT outbound FROM connections "
                        "WHERE ts >= ? AND outbound IS NOT NULL ORDER BY outbound", (since,)
                    )
                ]
                self._json({
                    "total": total, "hours": hours,
                    "top_domains": top_domains,
                    "top_users": top_users,
                    "nodes": nodes,
                    "users_list": users_list,
                    "outbounds_list": outbounds_list,
                })

            elif parsed.path == "/api/connections":
                where, params = _build_where(qs)
                try:
                    limit = min(max(int(qs.get("limit", ["100"])[0] or 100), 1), 500)
                except ValueError:
                    limit = 100

                rows = [
                    {
                        "id": r["id"],
                        "ts": r["ts"],
                        "node": r["node"],
                        "email": r["email"],
                        "src_ip": r["src_ip"],
                        "dest_host": r["dest_host"],
                        "dest_port": r["dest_port"],
                        "network": r["network"],
                        "outbound": r["outbound"],
                    }
                    for r in conn.execute(
                        f"SELECT id, ts, node, email, src_ip, dest_host, dest_port, network, outbound "
                        f"FROM connections WHERE {where} ORDER BY ts DESC, id DESC LIMIT ?",
                        params + [limit]
                    )
                ]
                self._json({"connections": rows, "count": len(rows)})

            elif parsed.path == "/":
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
<title>Xray Log Dashboard</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;background:#0d1117;color:#c9d1d9;padding:20px}
  .header{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:12px}
  h1{color:#58a6ff;font-size:1.3em}
  h2{color:#8b949e;margin-bottom:10px;font-size:.9em;text-transform:uppercase;letter-spacing:.08em}
  .controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;margin-bottom:16px}
  .controls label{font-size:.78em;color:#8b949e;display:flex;flex-direction:column;gap:4px}
  .controls input,.controls select{background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:6px 10px;font-family:inherit;font-size:.85em;min-width:130px}
  .controls input:focus,.controls select:focus{outline:none;border-color:#58a6ff}
  .btn{background:#238636;color:#fff;border:none;padding:7px 14px;border-radius:6px;cursor:pointer;font-size:.85em;font-family:inherit}
  .btn:hover{background:#2ea043}
  .btn-sec{background:#21262d;color:#c9d1d9;border:1px solid #30363d}
  .btn-sec:hover{background:#30363d}
  .counters{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
  @media(max-width:900px){.grid{grid-template-columns:1fr}}
  .card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;overflow-x:auto}
  .stat{font-size:1.8em;color:#58a6ff;font-weight:bold}
  .stat-label{color:#8b949e;font-size:.78em;margin-top:2px}
  table{width:100%;border-collapse:collapse;font-size:.82em}
  th{text-align:left;color:#8b949e;border-bottom:1px solid #30363d;padding:6px 8px;white-space:nowrap}
  td{padding:5px 8px;border-bottom:1px solid #21262d;white-space:nowrap}
  tr:hover td{background:#1c2128}
  .bar{display:inline-block;background:#1f6feb;height:9px;border-radius:2px;vertical-align:middle}
  .badge{display:inline-block;padding:2px 7px;border-radius:4px;font-size:.8em;font-weight:600;cursor:pointer}
  .node-ru{background:#132f1e;color:#3fb950}.node-pl{background:#2e2412;color:#d29922}.node-de{background:#32171a;color:#f85149}
  .node-other{background:#1c2536;color:#58a6ff}
  .out-DIRECT{color:#3fb950}.out-BLOCK{color:#f85149}
  .clickable{cursor:pointer;color:#79c0ff}.clickable:hover{text-decoration:underline}
  .chk{display:flex;align-items:center;gap:6px;font-size:.82em;color:#8b949e;cursor:pointer}
</style>
</head>
<body>
<div class="header">
  <h1>🛡 Xray · Log Dashboard</h1>
  <div style="display:flex;gap:12px;align-items:center">
    <label class="chk"><input type="checkbox" id="autoRefresh" checked> Авто (10с)</label>
    <button class="btn" onclick="loadAll()">↻ Обновить</button>
  </div>
</div>

<div class="controls">
  <label>Период
    <select id="fHours" onchange="loadAll()">
      <option value="1">1 час</option>
      <option value="6">6 часов</option>
      <option value="24" selected>24 часа</option>
      <option value="72">3 дня</option>
      <option value="168">7 дней</option>
    </select>
  </label>
  <label>Нода
    <select id="fNode" onchange="loadAll()">
      <option value="">Все ноды</option>
    </select>
  </label>
  <label>Пользователь
    <input id="fUser" list="usersList" placeholder="email / ID..." onchange="loadAll()" onkeydown="if(event.key==='Enter')loadAll()">
    <datalist id="usersList"></datalist>
  </label>
  <label>Домен / IP
    <input id="fDomain" placeholder="google.com..." onkeydown="if(event.key==='Enter')loadAll()">
  </label>
  <label>Outbound
    <select id="fOutbound" onchange="loadAll()">
      <option value="">Все</option>
    </select>
  </label>
  <label>Строк
    <select id="fLimit" onchange="loadAll()">
      <option value="50">50</option>
      <option value="100" selected>100</option>
      <option value="250">250</option>
      <option value="500">500</option>
    </select>
  </label>
  <div style="display:flex;gap:6px;align-self:flex-end">
    <button class="btn" onclick="loadAll()">Применить</button>
    <button class="btn btn-sec" onclick="resetFilters()">Сброс</button>
  </div>
</div>

<div class="counters" id="counters"></div>

<div class="card" style="margin-bottom:16px">
  <h2>Последние подключения <span id="connCount" style="color:#58a6ff"></span></h2>
  <table id="tConns">
    <tr><th>Время (UTC)</th><th>Нода</th><th>Юзер</th><th>Назначение</th><th>Протокол</th><th>Outbound</th><th>IP клиента</th></tr>
  </table>
</div>

<div class="grid">
  <div class="card">
    <h2>Топ доменов</h2>
    <table id="tDomains"><tr><th>#</th><th>Домен</th><th>Соед.</th><th></th></tr></table>
  </div>
  <div class="card">
    <h2>Топ пользователей</h2>
    <table id="tUsers"><tr><th>#</th><th>Email</th><th>Соед.</th><th></th></tr></table>
  </div>
</div>

<script>
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nodeCls = n => ['ru','pl','de'].includes((n||'').toLowerCase()) ? 'node-' + n.toLowerCase() : 'node-other';

function qs() {
  const p = new URLSearchParams();
  p.set('hours', document.getElementById('fHours').value);
  const node = document.getElementById('fNode').value;
  const user = document.getElementById('fUser').value.trim();
  const domain = document.getElementById('fDomain').value.trim();
  const outbound = document.getElementById('fOutbound').value;
  const limit = document.getElementById('fLimit').value;
  if (node) p.set('node', node);
  if (user) p.set('user', user);
  if (domain) p.set('domain', domain);
  if (outbound) p.set('outbound', outbound);
  p.set('limit', limit);
  return p.toString();
}

function setFilter(id, val) {
  document.getElementById(id).value = val;
  loadAll();
}

function resetFilters() {
  document.getElementById('fHours').value = '24';
  document.getElementById('fNode').value = '';
  document.getElementById('fUser').value = '';
  document.getElementById('fDomain').value = '';
  document.getElementById('fOutbound').value = '';
  document.getElementById('fLimit').value = '100';
  loadAll();
}

function syncSelect(id, items, defaultLabel) {
  const sel = document.getElementById(id);
  const cur = sel.value;
  sel.innerHTML = `<option value="">${defaultLabel}</option>` +
    items.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
  if (items.includes(cur)) sel.value = cur;
}

async function loadAll() {
  const query = qs();
  const [rStats, rConns] = await Promise.all([
    fetch('/api/stats?' + query),
    fetch('/api/connections?' + query)
  ]);
  const d = await rStats.json();
  const c = await rConns.json();

  syncSelect('fNode', d.nodes.map(x => x.node), 'Все ноды');
  syncSelect('fOutbound', d.outbounds_list || [], 'Все');
  document.getElementById('usersList').innerHTML =
    (d.users_list || []).map(u => `<option value="${esc(u)}">`).join('');

  const nodesHtml = d.nodes.map(n =>
    `<div class="card" style="cursor:pointer" onclick="setFilter('fNode','${esc(n.node)}')">
       <div class="stat"><span class="badge ${nodeCls(n.node)}" style="font-size:.55em">${esc(n.node.toUpperCase())}</span> ${n.count}</div>
       <div class="stat-label">нода ${esc(n.node)} (${d.hours}ч)</div>
     </div>`
  ).join('');
  document.getElementById('counters').innerHTML =
    `<div class="card" style="cursor:pointer" onclick="setFilter('fNode','')"><div class="stat">${d.total}</div><div class="stat-label">по фильтру (${d.hours}ч)</div></div>` + nodesHtml;

  document.getElementById('connCount').textContent = `(${c.count})`;
  document.getElementById('tConns').innerHTML =
    '<tr><th>Время (UTC)</th><th>Нода</th><th>Юзер</th><th>Назначение</th><th>Протокол</th><th>Outbound</th><th>IP клиента</th></tr>' +
    c.connections.map(x => {
      const ts = esc((x.ts || '').replace('T', ' ').slice(0, 19));
      const dest = esc(x.dest_host || '?') + (x.dest_port ? ':' + esc(x.dest_port) : '');
      const u = x.email ? `<span class="clickable" onclick="setFilter('fUser','${esc(x.email)}')">${esc(x.email)}</span>` : '<span style="color:#8b949e">anon</span>';
      const dom = x.dest_host ? `<span class="clickable" onclick="setFilter('fDomain','${esc(x.dest_host)}')">${dest}</span>` : dest;
      const out = esc(x.outbound || '-');
      return `<tr>
        <td>${ts}</td>
        <td><span class="badge ${nodeCls(x.node)}" onclick="setFilter('fNode','${esc(x.node)}')">${esc(x.node)}</span></td>
        <td>${u}</td>
        <td>${dom}</td>
        <td>${esc(x.network || '-')}</td>
        <td class="out-${out}">${out}</td>
        <td style="color:#8b949e">${esc(x.src_ip || '-')}</td>
      </tr>`;
    }).join('');

  const maxD = d.top_domains[0]?.count || 1;
  document.getElementById('tDomains').innerHTML =
    '<tr><th>#</th><th>Домен</th><th>Соед.</th><th></th></tr>' +
    d.top_domains.map((x,i) =>
      `<tr><td>${i+1}</td><td><span class="clickable" onclick="setFilter('fDomain','${esc(x.domain)}')">${esc(x.domain)}</span></td><td>${x.count}</td>
       <td><span class="bar" style="width:${Math.round(x.count/maxD*120)}px"></span></td></tr>`
    ).join('');

  const maxU = d.top_users[0]?.count || 1;
  document.getElementById('tUsers').innerHTML =
    '<tr><th>#</th><th>Email</th><th>Соед.</th><th></th></tr>' +
    d.top_users.map((x,i) =>
      `<tr><td>${i+1}</td><td><span class="clickable" onclick="setFilter('fUser','${esc(x.email)}')">${esc(x.email)}</span></td><td>${x.count}</td>
       <td><span class="bar" style="width:${Math.round(x.count/maxU*120)}px"></span></td></tr>`
    ).join('');
}

loadAll();
setInterval(() => {
  if (document.getElementById('autoRefresh').checked) loadAll();
}, 10000);
</script>
</body>
</html>"""

# ─── Entry point ──────────────────────────────────────────────────────────────
def main():
    global DB_PATH
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
