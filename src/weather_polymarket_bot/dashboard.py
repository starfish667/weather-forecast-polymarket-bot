from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import secrets
import socket
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterable
from urllib.parse import parse_qs, urlparse

from weather_polymarket_bot.config import ZeroZeroConfig
from weather_polymarket_bot.news_monitor import NewsReviewRound, run_news_review_round


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_loopback_host(host: str) -> bool:
    return host in {"127.0.0.1", "::1", "localhost"}


def preferred_lan_address(addresses: Iterable[str]) -> str | None:
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if (
            parsed.version == 4
            and parsed.is_private
            and not parsed.is_loopback
            and not address.startswith("198.18.")
            and not address.endswith(".1")
        ):
            return address
    return None


def local_network_address() -> str:
    try:
        preferred = preferred_lan_address(socket.gethostbyname_ex(socket.gethostname())[2])
        if preferred is not None:
            return preferred
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.connect(("8.8.8.8", 80))
            return str(connection.getsockname()[0])
    except OSError:
        return "127.0.0.1"


class DashboardMonitor:
    def __init__(
        self,
        *,
        config: ZeroZeroConfig,
        feeds: Iterable[str],
        interval_seconds: int,
    ) -> None:
        self.config = config
        self.feeds = tuple(feeds)
        self.interval_seconds = interval_seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._seen_headlines: set[str] = set()
        self._running = False
        self._status = "idle"
        self._last_completed_at: str | None = None
        self._last_error: str | None = None
        self._result: NewsReviewRound | None = None

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="news-dashboard-monitor").start()

    def stop(self) -> None:
        self._stop.set()

    def trigger(self) -> bool:
        with self._lock:
            if self._running:
                return False
        threading.Thread(target=self._run_once, daemon=True, name="news-dashboard-refresh").start()
        return True

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            result = self._result
            review = result.review if result else None
            return {
                "status": self._status,
                "running": self._running,
                "last_completed_at": self._last_completed_at,
                "last_error": self._last_error,
                "interval_seconds": self.interval_seconds,
                "headline_count": result.headline_count if result else 0,
                "market_count": result.market_count if result else 0,
                "feed_errors": list(result.feed_errors) if result else [],
                "review": (
                    {
                        "summary": review.summary,
                        "event_slugs": list(review.event_slugs),
                        "evidence": list(review.evidence),
                        "uncertainties": list(review.uncertainties),
                    }
                    if review
                    else None
                ),
            }

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._run_once()
            self._stop.wait(self.interval_seconds)

    def _run_once(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._status = "researching"
            self._last_error = None
        try:
            result = asyncio.run(
                run_news_review_round(
                    config=self.config,
                    feeds=self.feeds,
                    seen_headlines=self._seen_headlines,
                )
            )
        except Exception as error:
            with self._lock:
                self._status = "error"
                self._last_error = str(error)
        else:
            with self._lock:
                self._status = "ready"
                self._result = result
                self._last_completed_at = utc_timestamp()
        finally:
            with self._lock:
                self._running = False


DASHBOARD_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Signal Desk</title>
<style>
:root { color-scheme: light; --ink:#17211d; --muted:#61706a; --line:#d7ddd6; --canvas:#f5f7f2; --panel:#ffffff; --teal:#14665a; --blue:#246fa8; --orange:#c45b2d; --red:#a93b3b; }
* { box-sizing:border-box; }
body { margin:0; background:var(--canvas); color:var(--ink); font:14px/1.45 Inter,Segoe UI,Arial,sans-serif; }
button { font:inherit; }
.shell { width:min(1180px, calc(100% - 28px)); margin:0 auto; padding:18px 0 34px; }
.top { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:8px 0 18px; border-bottom:1px solid var(--line); }
.brand { font-size:18px; font-weight:700; letter-spacing:0; }
.meta { display:flex; align-items:center; gap:10px; color:var(--muted); font-size:12px; }
.dot { width:9px; height:9px; border-radius:50%; background:var(--orange); }
.dot.ready { background:var(--teal); }.dot.error { background:var(--red); }.dot.researching { background:var(--blue); }
.refresh { width:34px; height:34px; border:1px solid var(--line); border-radius:6px; background:var(--panel); color:var(--ink); cursor:pointer; font-size:18px; line-height:1; }
.refresh:disabled { color:var(--muted); cursor:wait; }
.metrics { display:grid; grid-template-columns:repeat(3, minmax(0,1fr)); gap:1px; margin:18px 0; border:1px solid var(--line); background:var(--line); }
.metric { min-height:76px; padding:14px; background:var(--panel); }
.metric strong { display:block; font-size:25px; line-height:1.1; font-weight:650; }
.metric span { color:var(--muted); font-size:12px; }
.grid { display:grid; grid-template-columns:minmax(0,1.7fr) minmax(280px,0.9fr); gap:18px; }
.panel { border:1px solid var(--line); border-radius:7px; background:var(--panel); }
.panel-head { display:flex; justify-content:space-between; gap:12px; align-items:baseline; padding:14px 16px; border-bottom:1px solid var(--line); }
.panel h2 { margin:0; font-size:14px; font-weight:700; }.panel-head time { color:var(--muted); font-size:12px; }
.body { padding:16px; }.summary { margin:0; font-size:16px; line-height:1.55; }.empty { color:var(--muted); margin:0; }
.list { margin:16px 0 0; padding:0; list-style:none; }.list li { padding:10px 0; border-top:1px solid var(--line); overflow-wrap:anywhere; }.list li:first-child { border-top:0; }
.slug { color:var(--blue); font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:12px; }
.label { display:block; margin-bottom:6px; color:var(--muted); font-size:11px; font-weight:700; text-transform:uppercase; }
.status-line { display:grid; gap:9px; }.status-line div { display:flex; justify-content:space-between; gap:12px; padding-bottom:9px; border-bottom:1px solid var(--line); }.status-line div:last-child { border-bottom:0; padding-bottom:0; }.status-line span { color:var(--muted); }.error { color:var(--red); overflow-wrap:anywhere; }.footer { color:var(--muted); font-size:12px; padding-top:14px; }
@media (max-width:760px) { .shell { width:min(100% - 20px, 680px); padding-top:10px; }.top { padding-bottom:12px; }.metrics { grid-template-columns:1fr; }.grid { grid-template-columns:1fr; }.metric { min-height:64px; }.summary { font-size:15px; } }
</style>
</head>
<body>
<main class="shell">
  <header class="top"><div><div class="brand">Signal Desk</div><div class="meta"><i id="dot" class="dot"></i><span id="status">连接中</span></div></div><button id="refresh" class="refresh" title="立即研究" aria-label="立即研究">&#10227;</button></header>
  <section class="metrics"><div class="metric"><strong id="headlineCount">0</strong><span>新新闻</span></div><div class="metric"><strong id="marketCount">0</strong><span>活跃事件</span></div><div class="metric"><strong id="interval">--</strong><span>轮询间隔</span></div></section>
  <section class="grid">
    <article class="panel"><div class="panel-head"><h2>研究队列</h2><time id="updated">等待首轮结果</time></div><div class="body"><p id="summary" class="empty">监控器正在准备研究上下文。</p><ul id="events" class="list"></ul></div></article>
    <aside class="panel"><div class="panel-head"><h2>监控状态</h2></div><div class="body"><div id="statusRows" class="status-line"></div><div id="errors" class="error"></div></div></aside>
  </section>
  <section class="grid" style="margin-top:18px"><article class="panel"><div class="panel-head"><h2>证据</h2></div><div class="body"><ul id="evidence" class="list"></ul></div></article><article class="panel"><div class="panel-head"><h2>不确定性</h2></div><div class="body"><ul id="uncertainties" class="list"></ul></div></article></section>
  <div class="footer">Public headlines · active market context · research queue</div>
</main>
<script>
const token = new URLSearchParams(location.search).get('token') || '';
const headers = token ? {'X-Dashboard-Token': token} : {};
const byId = id => document.getElementById(id);
const list = (id, values, kind) => { const target=byId(id); target.replaceChildren(); if (!values || !values.length) { const item=document.createElement('li'); item.className='empty'; item.textContent=kind; target.append(item); return; } values.forEach(value => { const item=document.createElement('li'); item.textContent=value; target.append(item); }); };
function render(data) { const status=data.status || 'idle'; byId('status').textContent=status==='ready' ? '已就绪' : status==='researching' ? '研究中' : status==='error' ? '需要处理' : '等待中'; byId('dot').className='dot '+status; byId('headlineCount').textContent=data.headline_count ?? 0; byId('marketCount').textContent=data.market_count ?? 0; byId('interval').textContent=data.interval_seconds ? Math.round(data.interval_seconds/60)+' 分钟' : '--'; byId('updated').textContent=data.last_completed_at ? new Date(data.last_completed_at).toLocaleString() : '等待首轮结果'; const review=data.review; byId('summary').className=review ? 'summary' : 'empty'; byId('summary').textContent=review ? review.summary : '本轮没有需要人工复核的事件。'; const events=byId('events'); events.replaceChildren(); (review?.event_slugs || []).forEach(slug=>{ const item=document.createElement('li'); const code=document.createElement('span'); code.className='slug'; code.textContent=slug; item.append(code); events.append(item); }); list('evidence', review?.evidence, '暂无证据条目'); list('uncertainties', review?.uncertainties, '暂无不确定性条目'); const rows=byId('statusRows'); rows.replaceChildren(); [['状态',status],['上次完成',data.last_completed_at ? new Date(data.last_completed_at).toLocaleTimeString() : '--'],['新闻源错误',(data.feed_errors || []).length]].forEach(([label,value])=>{ const row=document.createElement('div'); const left=document.createElement('span'); left.textContent=label; const right=document.createElement('strong'); right.textContent=value; row.append(left,right); rows.append(row); }); byId('errors').textContent=[data.last_error,...(data.feed_errors || [])].filter(Boolean).join(' | '); byId('refresh').disabled=Boolean(data.running); }
async function load() { try { const response=await fetch('/api/status',{headers}); if (!response.ok) throw new Error('连接失败'); render(await response.json()); } catch(error) { byId('status').textContent=error.message; byId('dot').className='dot error'; } }
byId('refresh').addEventListener('click', async () => { byId('refresh').disabled=true; await fetch('/api/run',{method:'POST',headers}); setTimeout(load,500); });
load(); setInterval(load,10000);
</script>
</body>
</html>
"""


class DashboardRequestHandler(BaseHTTPRequestHandler):
    monitor: DashboardMonitor
    access_token: str | None

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _authorized(self) -> bool:
        if self.access_token is None:
            return True
        query = parse_qs(urlparse(self.path).query)
        token = self.headers.get("X-Dashboard-Token") or query.get("token", [""])[0]
        return secrets.compare_digest(token, self.access_token)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not self._authorized():
            self._send(403, b"Forbidden", "text/plain; charset=utf-8")
            return
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, DASHBOARD_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/status":
            body = json.dumps(self.monitor.snapshot()).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        self._send(404, b"Not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(403, b"Forbidden", "text/plain; charset=utf-8")
            return
        if urlparse(self.path).path != "/api/run":
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        started = self.monitor.trigger()
        body = json.dumps({"started": started}).encode("utf-8")
        self._send(202, body, "application/json; charset=utf-8")


def serve_dashboard(
    *,
    config: ZeroZeroConfig,
    feeds: Iterable[str],
    host: str,
    port: int,
    interval_seconds: int,
) -> int:
    if not 1 <= port <= 65535:
        raise RuntimeError("--port must be between 1 and 65535")
    if interval_seconds < 30:
        raise RuntimeError("--interval-seconds must be at least 30")
    remote_access = not is_loopback_host(host)
    token = None
    if remote_access:
        token = os.getenv("DASHBOARD_TOKEN") or secrets.token_urlsafe(24)
    monitor = DashboardMonitor(config=config, feeds=feeds, interval_seconds=interval_seconds)
    handler = type(
        "ConfiguredDashboardRequestHandler",
        (DashboardRequestHandler,),
        {"monitor": monitor, "access_token": token},
    )
    server = ThreadingHTTPServer((host, port), handler)
    monitor.start()
    public_host = local_network_address() if host in {"0.0.0.0", "::"} else host
    url = f"http://{public_host}:{port}/"
    if token:
        url += f"?token={token}"
    print(f"Signal Desk: {url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        monitor.stop()
        server.server_close()
