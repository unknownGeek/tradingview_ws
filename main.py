#!/usr/bin/env python3
"""
WebSocket to n8n Webhook Forwarder
Main entry point for the application

Changes:
- Flask runs in its own daemon thread so it doesn't block reconnection loop.
- WebSocket runs with automatic reconnect (exponential backoff + jitter).
- Logging to stdout (Render-friendly).
- Graceful shutdown on KeyboardInterrupt.
"""

import requests
import threading
import sys
import websocket
import json
import random
import string
import time as time_module
import plotly.graph_objs as go
from collections import deque
from flask import Flask, render_template_string
from datetime import datetime, timedelta, timezone
import pytz
import os
import logging
import signal

# -------------------- Logging Setup --------------------
logging.basicConfig(
    level=logging.INFO,  # adjust to DEBUG for more verbosity
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# -------------------- Globals & Config --------------------
max_candle_window_len = 24
candle_window = deque(maxlen=max_candle_window_len)  # Store last N candles
current_candle = None
current_interval = None

DEFAULT_FLASK_PORT = 5000
XAU_USD_SYMBOL = "OANDA:XAUUSD"
BTC_USD_SYMBOL = "CRYPTO:BTCUSD" # "BINANCE:BTCUSDT"
ETH_USD_SYMBOL = "CRYPTO:ETHUSD" # "BINANCE:BTCUSDT"

app = Flask(__name__)
DEFAULT_TIMEFRAME_MINUTE = "5"
DEFAULT_TV_WS_URL = "wss://data.tradingview.com/socket.io/websocket"


tradingview_symbol = os.environ.get("SYMBOL", BTC_USD_SYMBOL)
timeframe_minute = int(os.environ.get("TIMEFRAME_MINUTE", DEFAULT_TIMEFRAME_MINUTE))

# TradingView socket url
TV_WS_URL = os.environ.get("TV_WS_URL", DEFAULT_TV_WS_URL)

# tradingview_symbol = BTC_USD_SYMBOL
# tradingview_symbol = ETH_USD_SYMBOL

IST = pytz.timezone("Asia/Kolkata")


# Control events
_stop_event = threading.Event()


# -------------------- Utilities --------------------
def get_now_ist():
    ts = time_module.time()
    dt_utc = datetime.fromtimestamp(ts, timezone.utc)
    return dt_utc.astimezone(IST)

# ----------------- Utility Functions -----------------


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


def format_candle(c):
    ts = datetime.fromtimestamp(c['timestamp']).strftime('%Y-%m-%d %H:%M')
    return f"{ts} | O: {c['open']}, H: {c['high']}, L: {c['low']}, C: {c['close']}, V: {c['volume']}"


def print_candles():
    logger.info(
        f"--- Last {max_candle_window_len} Candles on {timeframe_minute} min timeframe---"
    )
    for c in list(candle_window):
        logger.info(format_candle(c))
    if current_candle:
        logger.info(format_candle(current_candle))


# -------------------- TradingView WebSocket Client --------------------
class TradingViewWS:
    def __init__(self, symbol=tradingview_symbol, url=TV_WS_URL):
        self.symbol = symbol
        self.session = generate_session("cs")
        self.quote_session = generate_session("qs")
        self.ws = None
        self.url = url
        self.connected = False

    # WebSocket callbacks - signatures match WebSocketApp expectations
    def on_open(self, ws):
        logger.info("[+] WebSocket opened")
        try:
            # Save ws reference so send() can use it
            self.ws = ws

            # Send initial messages
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
            # create_series may require different args depending on TV API; keep as-is
            self.send("create_series", [self.session, "s1", "symbol_1", "1", 300])

            self.connected = True
        except Exception:
            logger.exception("Exception in on_open")

    def on_message(self, ws, message):
        # Trim very long logs a bit
        try:
            display_msg = message if len(message) < 1000 else message[:1000] + "...(truncated)"
            logger.info(f"received message: {display_msg}")
            # Respond to heartbeat messages
            if isinstance(message, str) and message.startswith("~m~") and "~m~~h~" in message:
                # detect and handle heartbeat style messages
                if self.symbol in message:
                    logger.debug(f"Unrecognized heartbeat: {message}")
                else:
                    # attempt to reply with the same as previous logic
                    try:
                        heartbeat_msg = message.split("~m~")[2]  # e.g., ~h~7
                        ws.send(message)  # echo back whole message (old behavior)
                        logger.info(f"[♥] Heartbeat responded with same received message as: {display_msg}")
                        return
                    except Exception:
                        logger.debug("Failed to respond to heartbeat with full echo")

            # Process multiple ~m~ chunks (existing logic)
            while isinstance(message, str) and message.startswith("~m~"):
                message = message[3:]
                len_str, _, message = message.partition("~m~")
                try:
                    length = int(len_str)
                except Exception:
                    logger.debug("Length parse error while handling ~m~ wrapper")
                    break
                chunk = message[:length]
                message = message[length:]

                if chunk.startswith("~h~"):
                    # Heartbeat response
                    try:
                        ws.send(f"~m~{len(chunk)}~m~{chunk}")
                        logger.info(f"[♥] Heartbeat responded with: {chunk}")
                    except Exception:
                        logger.debug("Failed to send heartbeat response")
                    continue

                try:
                    data = json.loads(chunk)
                    self.handle_data(data)
                except Exception:
                    logger.debug("Failed to json-decode chunk; ignoring")
        except Exception:
            logger.exception("Unhandled exception in on_message")

    def handle_data(self, data):
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

            if current_interval is None or interval != current_interval:
                if current_candle:
                    candle_window.append(current_candle)
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
                current_candle['high'] = max(current_candle['high'], price)
                current_candle['low'] = min(current_candle['low'], price)
                current_candle['close'] = price
                if volume:
                    current_candle['volume'] = volume  # Replace with latest volume

            # print_candles()
        except Exception:
            logger.exception("Error in handle_data")

    def on_error(self, ws, error):
        logger.error(f"[!] WebSocket error: {error}", exc_info=True)
        self.connected = False

    def on_close(self, ws, close_status_code, close_msg):
        logger.warning(f"[x] WebSocket closed: {close_status_code} {close_msg}")
        self.connected = False

    def send(self, func, params):
        try:
            msg = construct_message(func, params)
            final_msg = "~m~{}~m~{}".format(len(msg), msg)
            if self.ws:
                self.ws.send(final_msg)
            else:
                logger.debug("ws not set yet; can't send")
        except Exception:
            logger.exception("Error sending message")

    # Don't use run() here; we'll manage WebSocketApp lifecycle externally for reconnects.


# -------------------- Flask Endpoints --------------------
@app.route('/ping', methods=['GET'])
def ping():
    return {"status": "OK"}


@app.route('/', methods=['GET'])
def homepage():
    now_ist = get_now_ist().strftime("%Y-%m-%d %H:%M:%S")
    routes = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint != "static":
            methods = ",".join(rule.methods - {"HEAD", "OPTIONS"})
            routes.append({"path": str(rule), "methods": methods})

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Jarvix - Live Price Websocket Status</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f9fafc; margin: 0; padding: 0;}
            header { background: linear-gradient(135deg, #1abc9c, #16a085); color: white; padding: 20px 40px; text-align: center; box-shadow: 0 4px 10px rgba(0,0,0,0.1);}
            header h1 { margin: 0; font-size: 2em; font-weight: 600;}
            .container { max-width: 900px; margin: 40px auto; padding: 20px;}
            .status-card { background: white; border-radius: 15px; padding: 30px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); text-align: center; margin-bottom: 40px; transition: transform 0.2s ease;}
            .status-card:hover { transform: translateY(-5px);}
            .status-card .symbol { font-size: 1.5em; margin-bottom: 10px;}
            .status-card .status { font-size: 1.2em; color: #27ae60; font-weight: bold;}
            .status-card .timestamp { font-size: 0.9em; color: #888;}
            table { width: 100%; border-collapse: collapse; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 15px rgba(0,0,0,0.05);}
            th, td { padding: 12px 15px; text-align: left;}
            th { background-color: #16a085; color: white; font-weight: 600;}
            tr { transition: background 0.2s ease;}
            tr:nth-child(even) { background-color: #f8f8f8;}
            tr:hover { background-color: #eafaf1;}
            td a { text-decoration: none; color: #2980b9; font-weight: 500;}
            td a:hover { text-decoration: underline;}
        </style>
    </head>
    <body>
        <header>
            <h1>📊 Jarvix - Live Price Websocket Status</h1>
        </header>
        <div class="container">
            <div class="status-card">
                <div class="symbol"><b>Symbol:</b> {{ symbol }}</div>
                <div class="status"><b>Timeframe:</b> {{ timeframe }} min</div>
                <div class="status">✅ OK - Connected</div>
                <div class="timestamp">Last updated (IST): {{ now }}</div>
            </div>
            <h2>📍 Available Endpoints</h2>
            <table>
                <tr><th>Path</th><th>Methods</th></tr>
                {% for r in routes %}
                    <tr>
                        <td><a href="{{ r.path }}">{{ r.path }}</a></td>
                        <td>{{ r.methods }}</td>
                    </tr>
                {% endfor %}
            </table>
        </div>
    </body>
    </html>
    """
    return render_template_string(
        html,
        symbol=tradingview_symbol,
        timeframe=timeframe_minute,
        now=now_ist,
        routes=routes
    )


# HTTP endpoint to return current candles
@app.route('/candles', methods=['GET'])
def get_candle_window():
    logger.info("[+] Received request for candles")
    logger.debug(f"candle_window = {candle_window}")
    candles = []
    if candle_window:
        candles = list(candle_window)

    logger.debug(f"candles before current: {candles}")

    if current_candle:
        candles.append(current_candle)

    logger.debug(f"candles with current: {candles}")

    # Convert timestamps to IST
    for c in candles:
        dt_utc = datetime.fromtimestamp(c['timestamp'], timezone.utc)
        dt_ist = dt_utc.astimezone(IST)
        c['timestamp_ist'] = dt_ist.strftime("%Y-%m-%dT%H:%M:%S")

    if candles:
        candles = list(reversed(candles))

    logger.info(f"Current candles(reversed): {candles}")
    return {
        'meta': {
            'symbol': tradingview_symbol,
            'timeframe': f"{timeframe_minute} min"
        },
        'values': candles
    }


# -------------------- Plotly HTML builder (unchanged) --------------------
def plot_candles_html(candles, title=f"📊 Jarvix - {tradingview_symbol} at {timeframe_minute} min"):
    candles_sorted = sorted(candles, key=lambda x: x['timestamp'])
    opens = [c['open'] for c in candles_sorted]
    highs = [c['high'] for c in candles_sorted]
    lows = [c['low'] for c in candles_sorted]
    closes = [c['close'] for c in candles_sorted]

    last_close = closes[-1] if closes else None

    times_dt = [datetime.fromisoformat(c['timestamp_ist']) if isinstance(c['timestamp_ist'], str) else c['timestamp_ist'] for c in candles_sorted]

    if times_dt:
        last_time = times_dt[-1]
        gap_time = last_time + timedelta(minutes=timeframe_minute * 3)
        times_dt.append(gap_time)

    times = [dt.isoformat() for dt in times_dt]

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=times,
        open=opens + [None],
        high=highs + [None],
        low=lows + [None],
        close=closes + [None],
        increasing_line_color="#26a69a",
        decreasing_line_color="#ef5350",
        increasing_fillcolor="#26a69a",
        decreasing_fillcolor="#ef5350",
        line_width=2,
        name="Price"
    ))

    if last_close is not None:
        last_x = times[-2]
        fig.add_shape(
            type="line",
            x0=last_x,
            x1=times[-1],
            y0=last_close,
            y1=last_close,
            line=dict(color="#000000", width=1, dash="dot"),
        )
        fig.add_annotation(
            x=times[-1],
            y=last_close,
            text=f"{last_close:.2f}",
            showarrow=True,
            arrowhead=1,
            arrowsize=1,
            arrowwidth=1,
            arrowcolor="#000000",
            ax=0,
            ay=0,
            bgcolor="#000000",
            font=dict(color="white", size=14, family="Inter, Arial"),
            bordercolor="#b26a00",
            borderwidth=1,
            borderpad=6,
            align="center",
            valign="middle",
        )

    fig.update_layout(
        title=dict(text=title, x=0.5, font=dict(size=24, family="Inter, Arial")),
        template="plotly_white",
        xaxis=dict(showgrid=False, rangeslider=dict(visible=True, thickness=0.05, bgcolor="#f5f5f5"), type="date",
                   tickformat="%Y-%m-%d<br>%H:%M", showspikes=True, spikesnap="cursor", spikemode="across",
                   spikecolor="#2196f3", spikedash="solid", spikethickness=1),
        yaxis=dict(side="right", showspikes=True, spikesnap="cursor", spikemode="across", spikecolor="#2196f3",
                   spikedash="solid", spikethickness=1, tickformat=".2f"),
        hovermode="x unified",
        dragmode="pan",
        autosize=True,
        height=700,
        margin=dict(l=40, r=80, t=60, b=40),
        font=dict(family="Inter, Arial", size=14),
        plot_bgcolor="white",
        paper_bgcolor="white",
    )

    fig.update_xaxes(rangeselector=dict(buttons=list([
        dict(step="all", label="All"),
        dict(count=15, label="15m", step="minute", stepmode="backward"),
        dict(count=1, label="1H", step="hour", stepmode="backward"),
        dict(count=1, label="1D", step="day", stepmode="backward"),
    ]), x=0.01, y=1.05))

    config = {
        "displayModeBar": True,
        "displaylogo": False,
        "scrollZoom": True,
        "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        "toImageButtonOptions": {"format": "png", "scale": 2}
    }

    html = fig.to_html(full_html=True, include_plotlyjs="cdn", config=config)

    style_js = """
    <style>
        #ohlc-info {
            position: absolute;
            top: 12px;
            left: 12px;
            z-index: 1000;
            background: rgba(20, 20, 20, 0.85);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            border-radius: 8px;
            padding: 10px 24px;   /* a bit more horizontal padding */
            box-shadow: 0 6px 20px rgba(0,0,0,0.6);
            font-family: 'Inter', Arial, sans-serif;
            color: #eee;
            min-width: 360px;     /* slightly wider for spacing */
            display: flex;
            justify-content: space-between;  /* distribute evenly */
            align-items: center;
            cursor: default;
            user-select: none;
            transition: background-color 0.3s ease;
        }
        #ohlc-info:hover {
            background: rgba(30, 30, 30, 0.95);
            box-shadow: 0 10px 30px rgba(0,0,0,0.8);
        }
        #ohlc-info > div {
            display: flex;
            flex-direction: column;
            align-items: center;
            min-width: 70px;
            padding: 0 10px;       /* horizontal padding between OHLC */
        }
        #ohlc-info > div:not(:last-child) {
            border-right: 1px solid rgba(255,255,255,0.2); /* subtle separator */
        }
        #ohlc-info > div span.label {
            font-size: 0.75em;
            color: #aaa;
            margin-bottom: 4px;    /* more spacing between label and value */
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        #ohlc-info > div span.value {
            font-weight: 700;
            font-size: 1.1em;
        }
        #ohlc-open { color: #42a5f5; }    /* Light blue */
        #ohlc-high { color: #66bb6a; }    /* Green */
        #ohlc-low { color: #ef5350; }     /* Red */
        #ohlc-close { color: #ffa726; }   /* Orange */
        </style>
        <div id="ohlc-info">
          <div><span class="label">Open</span><span class="value" id="ohlc-open">-</span></div>
          <div><span class="label">High</span><span class="value" id="ohlc-high">-</span></div>
          <div><span class="label">Low</span><span class="value" id="ohlc-low">-</span></div>
          <div><span class="label">Close</span><span class="value" id="ohlc-close">-</span></div>
        </div>

        <script>
        const plot = document.querySelector('.plotly-graph-div');
        const openEl = document.getElementById('ohlc-open');
        const highEl = document.getElementById('ohlc-high');
        const lowEl = document.getElementById('ohlc-low');
        const closeEl = document.getElementById('ohlc-close');

        plot.on('plotly_hover', function(event) {
            if(event.points.length > 0) {
                const pt = event.points[0];
                if(pt.data.type === 'candlestick') {
                    const o = pt.data.open[pt.pointNumber].toFixed(2);
                    const h = pt.data.high[pt.pointNumber].toFixed(2);
                    const l = pt.data.low[pt.pointNumber].toFixed(2);
                    const c = pt.data.close[pt.pointNumber].toFixed(2);

                    openEl.textContent = o;
                    highEl.textContent = h;
                    lowEl.textContent = l;
                    closeEl.textContent = c;
                }
            }
        });

        plot.on('plotly_unhover', function(event) {
            // Optional: keep last values or reset
            /*
            openEl.textContent = '-';
            highEl.textContent = '-';
            lowEl.textContent = '-';
            closeEl.textContent = '-';
            */
        });
        </script>
    """

    wrapper_css = """
    <style>
      body, html {
        margin: 0; padding: 0; height: 100%;
        display: flex;
        justify-content: center;  /* center horizontally */
        align-items: center;      /* center vertically */
        background-color: #f7f9fc;
        font-family: 'Inter', Arial, sans-serif;
      }
      #chart-wrapper {
        width: 70vw;
        max-width: 1100px;
        background: white;
        padding: 24px;
        border-radius: 12px;
        box-shadow: 0 8px 20px rgba(0,0,0,0.12);
        box-sizing: border-box;
      }
      /* Ensure Plotly fills container */
      .plotly-graph-div {
        width: 100% !important;
        height: 700px !important;  /* keep your chart height */
      }
    </style>
    """

    # Wrap Plotly chart div inside #chart-wrapper
    html = html.replace('<body>', f'<body>{wrapper_css}<div id="chart-wrapper">')
    html = html.replace('</body>', '</div></body>')
    html = html.replace("</body>", style_js + "</body>")

    return html


def fetch_candles_from_api(api_url):
    resp = requests.get(api_url)
    resp.raise_for_status()
    return resp.json()


@app.route("/chart/<hostType>", methods=["GET"])
def get_chart(hostType):
    if hostType == "remote":
        api_url = "https://tradingview-ws.onrender.com/candles"
        candles = fetch_candles_from_api(api_url)
    else:
        candles = get_candle_window()
    c = candles['values']
    html = plot_candles_html(c)
    logger.info("Chart generated")
    return html


# -------------------- Run logic: Flask thread + WS reconnect loop --------------------
def start_flask_in_thread():
    port = int(os.environ.get("PORT", DEFAULT_FLASK_PORT))
    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False),
        daemon=True,
        name="flask-thread"
    )
    flask_thread.start()
    logger.info(f"Flask started in thread (port={port})")


def ws_connect_loop():
    """Persistent connect loop with exponential backoff and jitter."""
    backoff = 1.0
    max_backoff = 300.0  # 5 minutes
    while not _stop_event.is_set():
        tv = TradingViewWS()
        ws_app = websocket.WebSocketApp(
            tv.url,
            on_open=tv.on_open,
            on_message=tv.on_message,
            on_error=tv.on_error,
            on_close=tv.on_close
        )

        # set reference so tv.send() can use it
        tv.ws = ws_app

        try:
            logger.info("Attempting WebSocket connection...")
            # run_forever will block until connection closes or errors.
            # Provide ping settings to keep connection alive.
            ws_app.run_forever(ping_interval=30, ping_timeout=10, ping_payload="ping")
            logger.warning("WebSocket run_forever returned (connection closed).")
        except Exception:
            logger.exception("Exception from run_forever")

        if _stop_event.is_set():
            logger.info("Stop event set; breaking reconnect loop.")
            break

        # Exponential backoff with jitter
        sleep_for = backoff + random.uniform(0, min(5.0, backoff))
        logger.warning(f"Reconnecting WebSocket in {sleep_for:.1f}s (backoff={backoff}s)...")
        time_module.sleep(sleep_for)
        backoff = min(backoff * 2, max_backoff)


def _signal_handler(signum, frame):
    logger.info(f"Signal {signum} received, shutting down...")
    _stop_event.set()


if __name__ == "__main__":
    # Require Python 3.7+
    if sys.version_info < (3, 7):
        logger.critical("Python 3.7 or higher is required")
        sys.exit(1)

    # Setup OS signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Start Flask once in a daemon thread
    start_flask_in_thread()

    # Start WebSocket connect loop in main thread (so process lifecycle revolves around it)
    try:
        ws_connect_loop()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, setting stop event")
        _stop_event.set()
    except Exception:
        logger.exception("Fatal exception in main")
    finally:
        logger.info("Shutting down - waiting briefly for threads to exit")
        _stop_event.set()
        # give threads a moment
        time_module.sleep(1)
        logger.info("Exit")
        sys.exit(0)
