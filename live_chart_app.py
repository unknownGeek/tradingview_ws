#!/usr/bin/env python3
"""
Jarvix - TradingView WS -> Flask + SSE live candles

Features:
- TradingView WebSocket client with reconnect/backoff.
- Flask app served in a daemon thread.
- SSE endpoint (/events) that streams new candle messages to browsers.
- Live front-end (/) using Chart.js + chartjs-chart-financial to show candlesticks and auto-update.
- Logging to stdout (Render logs).
"""

import os
import sys
import time as time_module
import random
import string
import json
import logging
import threading
import queue
import signal
from collections import deque
from datetime import datetime, timezone, timedelta

import pytz
import requests
import websocket
from flask import Flask, Response, render_template_string, stream_with_context

# -------------------- Logging --------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("jarvix")

# -------------------- Config & Globals --------------------
TV_WS_URL = "wss://data.tradingview.com/socket.io/websocket"
DEFAULT_FLASK_PORT = int(os.environ.get("PORT", 5000))

timeframe_minute = 1
max_candle_window_len = 100  # keep more candles for viz
candle_window = deque(maxlen=max_candle_window_len)
current_candle = None
current_interval = None

# Protect shared candle state between threads
data_lock = threading.Lock()

IST = pytz.timezone("Asia/Kolkata")
tradingview_symbol = os.environ.get("SYMBOL", "CRYPTO:BTCUSD")
# tradingview_symbol = os.environ.get("SYMBOL", "OANDA:XAUUSD")

# SSE client state
client_queues = []  # list of queue.Queue instances (one per connected client)
client_lock = threading.Lock()

# graceful stop event
_stop_event = threading.Event()

app = Flask(__name__)


# -------------------- Utilities --------------------
def generate_session(prefix="qs"):
    return prefix + "_" + ''.join(
        random.choices(string.ascii_lowercase + string.digits, k=12))


def construct_message(func, params):
    return json.dumps({"m": func, "p": params})


def get_chart_timeframe_interval(timestamp):
    dt = datetime.fromtimestamp(timestamp)
    floored = dt.replace(minute=dt.minute - dt.minute % timeframe_minute,
                         second=0,
                         microsecond=0)
    return int(floored.timestamp())


def push_to_clients(payload: dict):
    """Enqueue payload (dict) to all connected SSE clients (non-blocking)."""
    payload_text = json.dumps(payload)
    with client_lock:
        to_remove = []
        for q in list(client_queues):
            try:
                q.put_nowait(payload_text)
            except queue.Full:
                # drop message for slow client
                logger.debug("Client queue full; dropping message for one client")
            except Exception:
                logger.exception("error pushing to client queue")
                to_remove.append(q)
        # cleanup any queues that errored
        for q in to_remove:
            try:
                client_queues.remove(q)
            except ValueError:
                pass


def format_candle_for_client(c):
    """Return a serializable representation of a candle for client JS."""
    # Provide timestamp in milliseconds for JS Date()
    # Cap volume to a reasonable value for frontend display
    try:
        vol = float(c.get("volume", 0))
        if vol > 1e10 or vol < 0:
            vol = 0
    except Exception:
        vol = 0
    return {
        "ts": int(c["timestamp"]) * 1000,
        "open": float(c["open"]),
        "high": float(c["high"]),
        "low": float(c["low"]),
        "close": float(c["close"]),
        "volume": vol,
        # optional: human timestamp in IST
        "ts_ist": datetime.fromtimestamp(c["timestamp"], timezone.utc).astimezone(IST).strftime("%Y-%m-%dT%H:%M:%S")
    }


# -------------------- TradingView WS client --------------------
class TradingViewWS:
    def __init__(self, symbol=tradingview_symbol, url=TV_WS_URL):
        self.symbol = symbol
        self.session = generate_session("cs")
        self.quote_session = generate_session("qs")
        self.url = url
        self.ws = None
        self.connected = False

    def on_open(self, ws):
        logger.info("[+] WebSocket opened")
        try:
            # send initialization messages
            self.send("set_auth_token", ["unauthorized_user_token"])
            self.send("chart_create_session", [self.session, ""])
            self.send("quote_create_session", [self.quote_session])
            self.send("quote_set_fields", [
                self.quote_session, "lp", "ch", "chp", "ask", "bid", "volume",
                "open", "high", "low"
            ])
            self.send("quote_add_symbols", [self.quote_session, self.symbol])
            self.send("quote_fast_symbols", [self.quote_session, self.symbol])
            self.send("resolve_symbol", [self.session, "symbol_1", self.symbol])
            # create_series may require different args; keep as in your original
            self.send("create_series", [self.session, "s1", "symbol_1", "1", 300])
            self.connected = True
        except Exception:
            logger.exception("exception in on_open")

    def on_message(self, ws, message):
        # handle messages and heartbeats
        try:
            # quick truncate for logging
            display = message if len(message) < 1000 else message[:1000] + "...(truncated)"
            logger.debug(f"received message: {display}")

            # heartbeat handling same style as original
            if isinstance(message, str) and message.startswith("~m~") and "~m~~h~" in message:
                # quick echo/back logic
                try:
                    heartbeat_msg = message.split("~m~")[2]  # e.g., ~h~7
                    ws.send(message)
                    logger.info(f"[♥] Heartbeat responded with: {display}")
                    return
                except Exception:
                    logger.debug("failed to handle heartbeat echo")

            # parse ~m~ chunks
            while isinstance(message, str) and message.startswith("~m~"):
                message = message[3:]
                len_str, _, message = message.partition("~m~")
                try:
                    length = int(len_str)
                except Exception:
                    logger.debug("failed to parse length from wrapper")
                    break
                chunk = message[:length]
                message = message[length:]
                if chunk.startswith("~h~"):
                    # heartbeat chunk
                    try:
                        ws.send(f"~m~{len(chunk)}~m~{chunk}")
                        logger.info(f"[♥] Heartbeat responded with chunk")
                    except Exception:
                        logger.debug("failed to send heartbeat chunk reply")
                    continue
                # JSON chunk
                try:
                    data = json.loads(chunk)
                except Exception:
                    logger.debug("chunk is not JSON; skipping")
                    continue
                self.handle_data(data)

        except Exception:
            logger.exception("exception in on_message")

    def handle_data(self, data):
        """Process a parsed TradingView message and update candles; broadcast updates to clients."""
        global current_candle, current_interval

        try:
            if data.get("m") != "qsd":
                return

            payload = data.get("p", [])[1]
            if not payload or payload.get("n") != self.symbol:
                return

            values = payload.get("v", {})
            price = values.get("lp")
            volume = values.get("volume", 0)

            if price is None:
                logger.debug(f"price is None in {data}")
                return

            # Use current time as fallback if timestamp not available
            timestamp = time_module.time()
            interval = get_chart_timeframe_interval(timestamp)

            # Mutate shared state under data_lock
            with data_lock:
                if current_interval is None or interval != current_interval:
                    if current_candle:
                        candle_window.append(current_candle)
                        # broadcast the finalized candle to clients
                        push_to_clients({"type": "candle_final", "candle": format_candle_for_client(current_candle)})
                    # start new candle
                    current_candle = {
                        'timestamp': interval,
                        'open': price,
                        'high': price,
                        'low': price,
                        'close': price,
                        'volume': volume if volume else 0.0
                    }
                    current_interval = interval
                else:
                    # update in-progress candle
                    current_candle['high'] = max(current_candle['high'], price)
                    current_candle['low'] = min(current_candle['low'], price)
                    current_candle['close'] = price
                    if volume:
                        current_candle['volume'] = volume

                # Prepare a copy for broadcasting (keep broadcast out of lock if heavy; small copy ok)
                candle_copy = dict(current_candle)

            # broadcast the live (in-progress) candle update to clients
            push_to_clients({"type": "candle_update", "candle": format_candle_for_client(candle_copy)})
            # also optionally broadcast a brief ticker with last price
            push_to_clients({"type": "ticker", "price": float(price), "ts": int(time_module.time() * 1000)})
            logger.debug("pushed update to clients")
        except Exception:
            logger.exception("exception in handle_data")

    def on_error(self, ws, error):
        logger.error(f"[!] WebSocket error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        logger.warning(f"[x] WebSocket closed: {close_status_code} {close_msg}")
        self.connected = False

    def send(self, func, params):
        try:
            msg = construct_message(func, params)
            final_msg = "~m~{}~m~{}".format(len(msg), msg)
            if self.ws:
                self.ws.send(final_msg)
        except Exception:
            logger.exception("exception in send")


# -------------------- Flask routes and SSE --------------------
@app.route("/")
def index():
    # Enhanced, modern, interactive UI for live candles
    html = r"""
    <!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Jarvix Live Candles</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #181c20;
      --panel: #23272e;
      --accent: #2e8bff;
      --up: #1ecb81;
      --down: #ff4b5c;
      --text: #f7f9fc;
      --muted: #b0b8c1;
      --shadow: 0 8px 32px rgba(0,0,0,0.18);
      --chart-bg: linear-gradient(120deg, #23272e 70%, #2e8bff22 100%);
    }
    body {
      font-family: 'Inter', Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      margin:0; padding:0;
      min-height: 100vh;
      box-sizing: border-box;
    }
    #container {
      width: 90vw;
      max-width: 1800px;
      margin: 2vw auto 0 auto;
      background: var(--panel);
      border-radius: 18px;
      box-shadow: var(--shadow);
      padding: 2vw 2vw 1vw 2vw;
      display: flex;
      flex-direction: column;
      align-items: stretch;
      min-height: 92vh;
    }
    h1 {
      margin:0 0 10px 0;
      font-size: 2.2rem;
      font-weight:700;
      letter-spacing:0.01em;
      background: linear-gradient(90deg, var(--accent) 10%, var(--up) 90%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }
    #meta {
      font-size: 1.1rem;
      color: var(--muted);
      margin-bottom: 18px;
      display:flex;
      gap:2vw;
      flex-wrap:wrap;
    }
    #chart-wrap {
      position:relative;
      background: var(--chart-bg);
      border-radius: 16px;
      box-shadow: 0 4px 32px #0003;
      padding: 1vw 1vw 0.5vw 1vw;
      margin-bottom: 2vw;
      min-height: 60vh;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: box-shadow 0.2s;
    }
    #chart-wrap:hover {
      box-shadow: 0 8px 48px #1ecb8133, 0 2px 12px #0002;
    }
    canvas {
      width:100% !important;
      height:70vh !important;
      min-height: 500px !important;
      max-height: 80vh !important;
      display:block;
      background:transparent;
      border-radius: 12px;
      box-shadow: 0 2px 16px #0002;
    }
    #price-ticker {
      font-size:3.2rem;
      font-weight:700;
      margin: 0 0 18px 0;
      color: var(--up);
      transition: color 0.2s;
      letter-spacing:0.01em;
      text-shadow: 0 2px 8px #1ecb8133;
      padding: 0.2em 0.8em;
      border-radius: 8px;
      background: rgba(30,203,129,0.07);
      display: inline-block;
      align-self: flex-start;
    }
    #price-ticker.down {
      color: var(--down);
      background: rgba(255,75,92,0.07);
      text-shadow: 0 2px 8px #ff4b5c33;
    }
    #ohlcv-panel {
      display:flex;
      gap:2vw;
      font-size:1.25rem;
      margin-bottom:1vw;
      background: rgba(46,139,255,0.07);
      border-radius: 8px;
      padding: 0.7em 1.2em;
      box-shadow: 0 1px 8px #2e8bff11;
      align-items: center;
      flex-wrap: wrap;
    }
    #ohlcv-panel span {
      min-width: 90px;
      display:inline-block;
      font-weight: 500;
    }
    #last {
      color: var(--muted);
      font-size:1.05rem;
    }
    #theme-toggle {
      position:absolute;
      top:2vw;
      right:2vw;
      background:var(--bg);
      color:var(--muted);
      border:none;
      border-radius:8px;
      padding:10px 22px;
      cursor:pointer;
      font-size:1.3rem;
      transition:background 0.2s, color 0.2s;
      box-shadow: 0 2px 8px #0002;
    }
    #theme-toggle:hover {
      background:var(--accent);
      color:#fff;
    }
    #price-line-label {
      position:absolute;
      right:0;
      top:0;
      z-index:10;
      font-size:1.3rem;
      font-weight:700;
      color:var(--up);
      background:rgba(24,28,32,0.97);
      padding:4px 18px;
      border-radius:10px 0 0 10px;
      pointer-events:none;
      display:none;
      box-shadow: 0 2px 8px #1ecb8133;
      border: 1.5px solid var(--up);
    }
    @media (max-width: 900px) {
      #container { width: 98vw; padding: 2vw 1vw; }
      #chart-wrap { padding: 1vw 0.5vw; }
      #ohlcv-panel { gap: 1vw; font-size: 1.1rem; }
      canvas { height: 45vh !important; min-height: 220px !important; }
    }
    @media (max-width: 700px) {
      #container { width: 100vw; padding: 1vw 0.5vw; }
      #chart-wrap { padding: 0.5vw 0.2vw; }
      canvas { height: 32vh !important; min-height: 120px !important; }
      #ohlcv-panel { flex-direction:column; gap:4px; font-size:1rem; }
      #price-ticker { font-size:2rem; }
    }
  </style>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-chart-financial@0.2.1/dist/chartjs-chart-financial.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@1.4.0/dist/chartjs-plugin-annotation.min.js"></script>
</head>
<body>
  <div id="container">
    <button id="theme-toggle" title="Toggle light/dark">🌙</button>
    <h1>📈 <span id="sym">SYMBOL</span> <span style="font-size:1rem;font-weight:400;color:var(--muted);">Live Candles</span></h1>
    <div id="meta">
      <span>Timeframe: <b id="tf">1</b> min</span>
      <span>Last updated: <span id="last">-</span></span>
    </div>
    <div id="ohlcv-panel">
      <span>O: <b id="open">-</b></span>
      <span>H: <b id="high">-</b></span>
      <span>L: <b id="low">-</b></span>
      <span>C: <b id="close">-</b></span>
      <span>V: <b id="vol">-</b></span>
      <span>IST: <b id="ts_ist">-</b></span>
    </div>
    <div id="price-ticker">-</div>
    <div id="chart-wrap">
      <canvas id="chart"></canvas>
      <div id="price-line-label" style="position:absolute;right:0;top:0;z-index:10;font-size:1.1rem;font-weight:600;color:var(--up);background:rgba(24,28,32,0.92);padding:2px 10px;border-radius:6px 0 0 6px;pointer-events:none;display:none;"></div>
    </div>
  </div>
<script>
(function(){
  // --- CONFIG ---
  const timeframe = {{ timeframe }};
  const symbol = "{{ symbol }}";
  document.getElementById('sym').textContent = symbol;
  document.getElementById('tf').textContent = timeframe;
  const maxPoints = 500;
  // --- Chart setup ---
  const dataset = {
    label: symbol,
    data: [],
    barThickness: 10,
    maxBarThickness: 20,
    borderColor: 'rgba(0,0,0,0.85)',
    borderWidth: 1,
    upColor: '#1ecb81',
    downColor: '#ff4b5c',
    color: '#f7f9fc',
  };
  const ctx = document.getElementById('chart').getContext('2d');
  const cfg = {
    type: 'candlestick',
    data: { datasets: [ dataset ] },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        tooltip: {
          enabled: true,
          callbacks: {
            label: function(ctx) {
              const d = ctx.raw;
              return `O:${d.o} H:${d.h} L:${d.l} C:${d.c}`;
            }
          }
        }
      },
      scales: {
        x: {
          type: 'time',
          time: { unit: 'minute', tooltipFormat: 'yyyy-MM-dd HH:mm' },
          ticks: { source: 'auto', autoSkip: true, maxRotation: 0, major: { enabled: true } },
          grid: { drawTicks: true },
          offset: true,
        },
        y: {
          position: 'right',
          grid: { drawTicks: true },
          beginAtZero: false,
          min: undefined,
          max: undefined,
          // Let Chart.js autoscale based on data
        }
      },
      elements: {
        candlestick: {
          barThickness: 1,
        }
      },
      animation: false,
      maintainAspectRatio: false
    }
  };
  const chart = new Chart(ctx, cfg);
  const idxByTs = new Map();
  // --- Price Line Annotation Setup ---
  let priceLineLabel = document.getElementById('price-line-label');
  // Set up annotation config ONCE
  chart.options.plugins.annotation = {
    annotations: {
      priceLine: {
        type: 'line',
        yMin: null,
        yMax: null,
        borderColor: '#1ecb81', // green
        borderWidth: 2,
        borderDash: [6,6], // dotted
        label: { display: false },
        z: 99,
        // Remove xMin/xMax: undefined means infinite horizontal line
      }
    }
  };
  chart.update('none');
  function drawPriceLine(price, openPrice) {
    if (price == null || isNaN(price)) {
      priceLineLabel.style.display = 'none';
      if (chart.options.plugins.annotation && chart.options.plugins.annotation.annotations) {
        delete chart.options.plugins.annotation.annotations.priceLine;
        chart.update('none');
      }
      return;
    }
    let lineColor = '#1ecb81'; // green by default
    if (typeof openPrice === 'number' && price < openPrice) lineColor = '#ff4b5c'; // red if down
    if (!chart.options.plugins.annotation) chart.options.plugins.annotation = { annotations: {} };
    chart.options.plugins.annotation.annotations.priceLine = {
      type: 'line',
      yMin: price,
      yMax: price,
      borderColor: lineColor,
      borderWidth: 2,
      borderDash: [6,6],
      label: { display: false },
      z: 99
    };
    chart.update('none');
    // Show label
    priceLineLabel.style.display = 'block';
    priceLineLabel.textContent = `● ${price}`;
    setTimeout(() => {
      const y = chart.scales.y.getPixelForValue(price);
      priceLineLabel.style.top = (y-16) + 'px';
      priceLineLabel.style.color = lineColor;
    }, 50);
  }
  let lastPrice = null;
  function animatePriceTicker(price) {
    const el = document.getElementById('price-ticker');
    if (lastPrice !== null) {
      if (price > lastPrice) {
        el.classList.remove('down');
        el.style.color = 'var(--up)';
      } else if (price < lastPrice) {
        el.classList.add('down');
        el.style.color = 'var(--down)';
      }
    }
    el.textContent = price;
    lastPrice = price;
  }
  function rebuildIndex() {
    idxByTs.clear();
    const arr = chart.data.datasets[0].data;
    for (let i = 0; i < arr.length; i++) {
      const d = arr[i];
      const t = (d && d.x) ? d.x.getTime() : null;
      if (t !== null) idxByTs.set(t, i);
    }
  }
  function upsertCandle(c) {
    if (!chart.data || !chart.data.datasets || !chart.data.datasets[0]) return;
    const ts = Number(c.ts);
    if (!ts || Number.isNaN(ts)) return;
    const tDate = new Date(ts);
    const point = {
      x: tDate,
      o: Number(c.open),
      h: Number(c.high),
      l: Number(c.low),
      c: Number(c.close)
    };
    if (idxByTs.has(ts)) {
      const idx = idxByTs.get(ts);
      chart.data.datasets[0].data[idx] = point;
    } else {
      const arr = chart.data.datasets[0].data;
      if (arr.length === 0 || ts > arr[arr.length - 1].x.getTime()) {
        arr.push(point);
        idxByTs.set(ts, arr.length - 1);
      } else {
        let lo = 0, hi = arr.length;
        while (lo < hi) {
          const mid = (lo + hi) >> 1;
          if (arr[mid].x.getTime() < ts) lo = mid + 1;
          else hi = mid;
        }
        arr.splice(lo, 0, point);
        rebuildIndex();
      }
      if (chart.data.datasets[0].data.length > maxPoints) {
        const removeCount = chart.data.datasets[0].data.length - maxPoints;
        chart.data.datasets[0].data.splice(0, removeCount);
        rebuildIndex();
      }
    }
    updatePanels(c); // Only call updatePanels (which calls drawPriceLine and chart.update)
  }
  function updatePanels(c) {
    document.getElementById('open').textContent = c.open;
    document.getElementById('high').textContent = c.high;
    document.getElementById('low').textContent = c.low;
    document.getElementById('close').textContent = c.close;
    document.getElementById('vol').textContent = c.volume;
    document.getElementById('ts_ist').textContent = c.ts_ist || '-';
    animatePriceTicker(Number(c.close));
    document.getElementById('last').textContent = new Date().toLocaleString();
    drawPriceLine(Number(c.close), Number(c.open));
    chart.update('none'); // Ensure chart is updated instantly after panel and price line update
  }
  // --- SSE setup ---
  function startSSE() {
    const evt = new EventSource('/events');
    evt.onopen = () => console.log('SSE open');
    evt.onerror = (e) => {
      console.warn('SSE error', e);
    };
    evt.onmessage = function(e) {
      try {
        const msg = JSON.parse(e.data);
        if (!msg || !msg.type) return;
        if (msg.type === 'candles_snapshot') {
          console.log('Received candles_snapshot', msg.candles);
          if (Array.isArray(msg.candles)) {
            const arr = msg.candles.map(c => ({
              x: new Date(Number(c.ts)),
              o: Number(c.open),
              h: Number(c.high),
              l: Number(c.low),
              c: Number(c.close)
            }));
            arr.sort((a,b) => a.x - b.x);
            chart.data.datasets[0].data = arr;
            rebuildIndex();
            chart.update('none');
            if (arr.length > 0) updatePanels(msg.candles[msg.candles.length-1]);
          }
        } else if (msg.type === 'candle_update' || msg.type === 'candle_final') {
          if (msg.candle) {
            upsertCandle(msg.candle);
          }
        } else if (msg.type === 'ticker') {
          if (msg.price) animatePriceTicker(Number(msg.price));
        }
      } catch (err) {
        console.error('failed to parse SSE message', err, e.data);
      }
    };
    // If no candles loaded after 1s, fallback to /prices
    setTimeout(() => {
      if (!chart.data.datasets[0].data || chart.data.datasets[0].data.length === 0) {
        console.log('No candles from SSE, fetching /prices snapshot fallback');
        fetchSnapshotFallback();
      }
    }, 1000);
  }
  function fetchSnapshotFallback() {
    fetch('/prices')
      .then(r => r.json())
      .then(arr => {
        if (!Array.isArray(arr) || arr.length === 0) return;
        const dataArr = arr.map(c => ({
          x: new Date(Number(c.ts)),
          o: Number(c.open),
          h: Number(c.high),
          l: Number(c.low),
          c: Number(c.close)
        }));
        dataArr.sort((a,b) => a.x - b.x);
        chart.data.datasets[0].data = dataArr;
        rebuildIndex();
        chart.update('none');
        if (dataArr.length > 0) updatePanels(arr[arr.length-1]);
      })
      .catch(err => {
        console.debug('failed to load /prices snapshot', err);
      });
  }
  // --- Theme toggle ---
  const themeBtn = document.getElementById('theme-toggle');
  let dark = true;
  themeBtn.onclick = function() {
    dark = !dark;
    if (dark) {
      document.documentElement.style.setProperty('--bg', '#181c20');
      document.documentElement.style.setProperty('--panel', '#23272e');
      document.documentElement.style.setProperty('--text', '#f7f9fc');
      document.documentElement.style.setProperty('--muted', '#b0b8c1');
      themeBtn.textContent = '🌙';
    } else {
      document.documentElement.style.setProperty('--bg', '#f7f9fc');
      document.documentElement.style.setProperty('--panel', '#fff');
      document.documentElement.style.setProperty('--text', '#23272e');
      document.documentElement.style.setProperty('--muted', '#6a6a6a');
      themeBtn.textContent = '☀️';
    }
  };
  // --- Start ---
  startSSE();
  setTimeout(fetchSnapshotFallback, 500);
})();
</script>
</body>
</html>
    """
    return render_template_string(html, symbol=tradingview_symbol, timeframe=timeframe_minute)


@app.route("/prices")
def prices_snapshot():
    """Return last N candles as JSON for initial page load."""
    payload = []
    with data_lock:
        merged = list(candle_window)
        if current_candle:
            merged.append(current_candle)
    for c in merged:
        payload.append(format_candle_for_client(c))
    payload.sort(key=lambda x: x["ts"])
    return json.dumps(payload), 200, {"Content-Type": "application/json"}


@app.route("/events")
def sse_events():
    """SSE endpoint. Each connection gets its own queue."""
    # Register client queue
    q = queue.Queue(maxsize=128)
    with client_lock:
        client_queues.append(q)
    logger.info(f"SSE client connected (clients={len(client_queues)})")

    def gen():
        try:
            # Send initial snapshot immediately
            with data_lock:
                merged = list(candle_window)
                if current_candle:
                    merged.append(current_candle)
            snapshot = [format_candle_for_client(c) for c in merged]
            init_payload = {"type": "candles_snapshot", "candles": snapshot}
            yield f"data: {json.dumps(init_payload)}\n\n"

            # Then stream live messages queued by server
            while not _stop_event.is_set():
                try:
                    msg = q.get(timeout=1.0)
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    continue
        except GeneratorExit:
            logger.debug("SSE client generator closed (client disconnected)")
        except Exception:
            logger.exception("Exception in SSE generator")
        finally:
            # Clean up client queue on disconnect
            with client_lock:
                try:
                    client_queues.remove(q)
                except ValueError:
                    pass
            logger.info(f"SSE client disconnected (clients={len(client_queues)})")

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no"  # disable buffering for some proxies
    }
    return Response(stream_with_context(gen()), mimetype="text/event-stream", headers=headers)


# -------------------- Run helpers --------------------
def start_flask_thread():
    port = int(os.environ.get("PORT", DEFAULT_FLASK_PORT))
    t = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False),
                         daemon=True, name="flask-thread")
    t.start()
    logger.info(f"Flask started in thread on port {port}")


def ws_connect_loop():
    """Persistent connect loop with exponential backoff and jitter."""
    backoff = 1.0
    max_backoff = 300.0
    while not _stop_event.is_set():
        tv = TradingViewWS()
        ws_app = websocket.WebSocketApp(
            tv.url,
            on_open=tv.on_open,
            on_message=tv.on_message,
            on_error=tv.on_error,
            on_close=tv.on_close
        )
        # set ws reference for send()
        tv.ws = ws_app

        try:
            logger.info("Attempting WebSocket connection...")
            ws_app.run_forever(ping_interval=30, ping_timeout=10, ping_payload="ping")
            logger.warning("WebSocket run_forever returned (connection closed).")
        except Exception:
            logger.exception("Exception from run_forever")
        if _stop_event.is_set():
            break

        # reconnect with backoff + jitter
        sleep_for = backoff + random.uniform(0, min(5.0, backoff))
        logger.warning(f"Reconnecting in {sleep_for:.1f}s (backoff={backoff}s)")
        time_module.sleep(sleep_for)
        backoff = min(backoff * 2, max_backoff)


def _signal_handler(signum, frame):
    logger.info(f"Signal {signum} received - shutting down")
    _stop_event.set()


# -------------------- Entrypoint --------------------
if __name__ == "__main__":
    # ensure python version
    if sys.version_info < (3, 7):
        logger.critical("Python 3.7+ is required")
        sys.exit(1)

    # setup signal handlers
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # start flask
    start_flask_thread()

    # enter ws connect loop (main thread)
    try:
        ws_connect_loop()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt - stopping")
        _stop_event.set()
    except Exception:
        logger.exception("Fatal error in main")
    finally:
        logger.info("Shutdown initiated")
        _stop_event.set()
        # allow threads to exit cleanly
        time_module.sleep(1)
        logger.info("Exit")
        sys.exit(0)
