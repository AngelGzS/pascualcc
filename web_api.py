"""Lightweight web API for paper trading status.

Runs alongside the executor, serves JSON status at /api/status
and a simple HTML dashboard at /.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from aiohttp import web

logger = logging.getLogger(__name__)

STATE_DIR = Path("data/paper")


def _load_all_states() -> list[dict]:
    """Load all ORB state files."""
    states = []
    if not STATE_DIR.exists():
        return states
    for f in STATE_DIR.glob("orb_*_state.json"):
        try:
            state = json.loads(f.read_text())
            n = len(state.get("trades", []))
            wins = sum(1 for t in state.get("trades", []) if t.get("pnl_usd", 0) > 0)
            pnl = state.get("capital", 0) - state.get("initial_capital", 0)
            state["pnl_usd"] = pnl
            state["pnl_pct"] = pnl / state["initial_capital"] * 100 if state.get("initial_capital") else 0
            state["trades_count"] = n
            state["win_rate"] = wins / n * 100 if n > 0 else 0
            states.append(state)
        except Exception as e:
            logger.warning("Failed to load %s: %s", f, e)
    return states


async def handle_status(request: web.Request) -> web.Response:
    states = _load_all_states()
    return web.json_response(states)


async def handle_dashboard(request: web.Request) -> web.Response:
    html = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>PascualCC Paper Trading</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'SF Mono',Consolas,monospace;background:#0d1117;color:#c9d1d9;padding:20px}
h1{color:#58a6ff;margin-bottom:20px;font-size:1.4em}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:16px}
.card h2{color:#58a6ff;font-size:1.1em;margin-bottom:12px}
.row{display:flex;gap:16px;flex-wrap:wrap}
.metric{flex:1;min-width:120px}
.metric .label{color:#8b949e;font-size:0.75em;text-transform:uppercase}
.metric .value{font-size:1.3em;font-weight:bold;margin-top:2px}
.green{color:#3fb950}.red{color:#f85149}.yellow{color:#d29922}
table{width:100%;border-collapse:collapse;margin-top:8px;font-size:0.85em}
th{text-align:left;color:#8b949e;padding:6px 8px;border-bottom:1px solid #30363d}
td{padding:6px 8px;border-bottom:1px solid #21262d}
.pos{background:#0d2818;border-left:3px solid #3fb950;padding:12px;border-radius:4px;margin-top:8px}
.neg{background:#2d1215;border-left:3px solid #f85149}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.75em;font-weight:bold}
.badge-long{background:#0d2818;color:#3fb950}.badge-short{background:#2d1215;color:#f85149}
.or-info{background:#1c1d21;padding:8px 12px;border-radius:4px;margin-top:8px;font-size:0.9em}
footer{margin-top:20px;color:#484f58;font-size:0.75em;text-align:center}
</style>
</head><body>
<h1>PascualCC Paper Trading Dashboard</h1>
<div id="app">Loading...</div>
<footer>Auto-refreshes every 30s</footer>
<script>
async function refresh(){
  try{
    const r=await fetch('/api/status');
    const data=await r.json();
    if(!data.length){document.getElementById('app').innerHTML='<p>No active strategies.</p>';return}
    let html='';
    for(const s of data){
      const pnlClass=s.pnl_usd>=0?'green':'red';
      const wrClass=s.win_rate>=50?'green':s.win_rate>=40?'yellow':'red';
      html+=`<div class="card">
        <h2>${s.pair} | ${s.strategy||'orb'} | RR=${s.rr_target||'?'}</h2>
        <div class="row">
          <div class="metric"><div class="label">Capital</div><div class="value">$${s.capital?.toFixed(2)}</div></div>
          <div class="metric"><div class="label">PnL</div><div class="value ${pnlClass}">$${s.pnl_usd>=0?'+':''}${s.pnl_usd?.toFixed(2)} (${s.pnl_pct>=0?'+':''}${s.pnl_pct?.toFixed(1)}%)</div></div>
          <div class="metric"><div class="label">Trades</div><div class="value">${s.trades_count}</div></div>
          <div class="metric"><div class="label">Win Rate</div><div class="value ${wrClass}">${s.win_rate?.toFixed(0)}%</div></div>
          <div class="metric"><div class="label">Candles</div><div class="value">${s.candles_processed}</div></div>
        </div>`;
      if(s.or_formed){
        html+=`<div class="or-info">OR: H=${s.or_high?.toFixed(2)} L=${s.or_low?.toFixed(2)} | Traded: ${s.today_traded?'Yes':'No'}</div>`;
      }
      if(s.open_position){
        const p=s.open_position;
        const cls=p.direction==='long'?'pos':'pos neg';
        html+=`<div class="${cls}">OPEN ${p.direction.toUpperCase()} @ ${p.entry_price?.toFixed(2)} | SL=${p.stop_loss?.toFixed(2)} TP=${p.take_profit?.toFixed(2)}</div>`;
      }
      const trades=s.trades||[];
      if(trades.length){
        const recent=trades.slice(-10).reverse();
        html+=`<table><tr><th>Date</th><th>Dir</th><th>Entry</th><th>Exit</th><th>Reason</th><th>PnL</th><th>R</th></tr>`;
        for(const t of recent){
          const d=new Date(t.entry_time);
          const ds=d.toISOString().slice(5,16).replace('T',' ');
          const pc=t.pnl_usd>=0?'green':'red';
          const badge=t.direction==='long'?'badge-long':'badge-short';
          html+=`<tr><td>${ds}</td><td><span class="badge ${badge}">${t.direction.toUpperCase()}</span></td><td>${t.entry_price?.toFixed(2)}</td><td>${t.exit_price?.toFixed(2)}</td><td>${t.exit_reason}</td><td class="${pc}">$${t.pnl_usd>=0?'+':''}${t.pnl_usd?.toFixed(2)}</td><td class="${pc}">${t.r_multiple>=0?'+':''}${t.r_multiple?.toFixed(1)}R</td></tr>`;
        }
        html+=`</table>`;
      }
      html+=`</div>`;
    }
    document.getElementById('app').innerHTML=html;
  }catch(e){document.getElementById('app').innerHTML='<p>Error: '+e.message+'</p>'}
}
refresh();
setInterval(refresh,30000);
</script>
</body></html>"""
    return web.Response(text=html, content_type="text/html")


async def start_web(port: int = 8080) -> None:
    app = web.Application()
    app.router.add_get("/", handle_dashboard)
    app.router.add_get("/api/status", handle_status)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Web dashboard running on http://0.0.0.0:%d", port)
