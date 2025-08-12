#!/usr/bin/env python3
"""
WebSocket to n8n Webhook Forwarder
Main entry point for the application
"""

import asyncio
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
from datetime import datetime, time, timedelta, timezone
import pytz
import os

max_candle_window_len = 12
candle_window = deque(maxlen=max_candle_window_len)  # Store last 5/7/10 etc candles
current_candle = None
current_interval = None
DEFAULT_FLASK_PORT = 5000
XAU_USD_SYMBOL = "OANDA:XAUUSD"
BTC_USD_SYMBOL = "BINANCE:BTCUSDT"

app = Flask(__name__)

timeframe_minute = 5

tradingview_symbol = XAU_USD_SYMBOL
# tradingview_symbol = BTC_USD_SYMBOL


# ---------- CONFIG ----------
IST = pytz.timezone("Asia/Kolkata")

# ---------- FILTERS ----------

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
    print(
        f"\n--- Last {max_candle_window_len} Candles on {timeframe_minute} min timeframe---"
    )
    for c in list(candle_window):
        print(format_candle(c))
    if current_candle:
        print(format_candle(current_candle))
    print("---------------\n")


# ----------------- WebSocket Logic -----------------


class TradingViewWS:

    def __init__(self, symbol=tradingview_symbol):
        self.symbol = symbol
        self.session = generate_session("cs")
        self.quote_session = generate_session("qs")
        self.ws = None
        self.url = "wss://data.tradingview.com/socket.io/websocket"
        self.connected = False

    def on_open(self, ws):
        print("[+] WebSocket opened")

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
        self.send("create_series", [self.session, "s1", "symbol_1", "1", 300])

    def on_message(self, ws, message):
        # print(f"received message: {message}\n")
        try:
            # Respond to heartbeat messages
            if message.startswith("~m~") and "~m~~h~" in message:
                if self.symbol in message:
                    # print(f"Unrecognized heartbeat: {message}")
                    a = 1
                else:
                    heartbeat_msg = message.split("~m~")[2]  # e.g., ~h~7
                    response = f"~m~4~m~{heartbeat_msg}"
                    ws.send(message)
                    # print(f"[♥] Heartbeat responded with: {message}\n")
                    return

            while message.startswith("~m~"):
                # Handle multiple ~m~ wrapped messages
                message = message[3:]
                len_str, _, message = message.partition("~m~")
                length = int(len_str)
                chunk = message[:length]
                message = message[length:]

                if chunk.startswith("~h~"):
                    # Heartbeat response
                    ws.send(f"~m~{len(chunk)}~m~{chunk}")
                    # print(f"[♥] Heartbeat responded with: {chunk}")
                    continue

                data = json.loads(chunk)
                self.handle_data(data)
        except Exception as e:
            print("[!] Error parsing message:", e)

    def handle_data(self, data):
        global current_candle, current_interval

        if data.get("m") != "qsd":
            return

        payload = data.get("p", [])[1]
        if not payload or payload.get("n") != self.symbol:
            return

        values = payload.get("v", {})
        price = values.get("lp")
        volume = values.get("volume", 0)

        if price is None:
            # print(f"price is None in {data}")
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

    def on_error(self, ws, error):
        print("[!] WebSocket error:", error)

    def on_close(self, ws, close_status_code, close_msg):
        print("[x] WebSocket closed:", close_status_code, close_msg)

    def send(self, func, params):
        msg = construct_message(func, params)
        final_msg = "~m~{}~m~{}".format(len(msg), msg)
        self.ws.send(final_msg)

    def run(self):
        self.ws = websocket.WebSocketApp(self.url,
                                         on_open=self.on_open,
                                         on_message=self.on_message,
                                         on_error=self.on_error,
                                         on_close=self.on_close)
        self.ws.run_forever()


# HTTP endpoint to return current candles
@app.route('/ping', methods=['GET'])
def ping():
    return {"status": "OK"}


# HTTP endpoint to return current candles
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
    # print("[+] Received request for candles")
    # print(f"candle_window = {candle_window}")
    candles = []
    if candle_window:
        candles = list(candle_window)

    # print(f"candles = {candles}")

    if current_candle:
        candles.append(current_candle)

    # print(f"candles = {candles}")

    # Convert timestamps to IST
    for c in candles:
        dt_utc = datetime.fromtimestamp(c['timestamp'], timezone.utc)
        dt_ist = dt_utc.astimezone(IST)
        c['timestamp_ist'] = dt_ist.strftime("%Y-%m-%dT%H:%M:%S")

    if candles != []:
        candles = list(reversed(candles))

    # print(f"Current candles(reversed): {candles}")
    return {
        'meta': {
            'symbol': tradingview_symbol,
            'timeframe': f"{timeframe_minute} min"
        },
        'values': candles
    }


async def main():

    while True:
        try:
            tv = TradingViewWS()
            threading.Thread(target=tv.run, daemon=True).start()

            # Start Flask server
            port = int(os.environ.get("PORT", DEFAULT_FLASK_PORT))
            app.run(host="0.0.0.0", port=port)
        except Exception as e:
            print(f"[!] Exception occurred: {e}. Reconnecting in 2 seconds...")
            time_module.sleep(2)


def plot_candles_html(candles, title=f"📊 Jarvix - {tradingview_symbol} at {timeframe_minute} min"):
    candles_sorted = sorted(candles, key=lambda x: x['timestamp'])
    opens = [c['open'] for c in candles_sorted]
    highs = [c['high'] for c in candles_sorted]
    lows = [c['low'] for c in candles_sorted]
    closes = [c['close'] for c in candles_sorted]

    last_close = closes[-1] if closes else None

    # Convert timestamp strings to datetime objects if needed
    times_dt = [datetime.fromisoformat(c['timestamp_ist']) if isinstance(c['timestamp_ist'], str) else c['timestamp_ist'] for c in candles_sorted]

    # Add gap of 5 minutes after last candle
    if times_dt:
        last_time = times_dt[-1]
        gap_time = last_time + timedelta(minutes = timeframe_minute*4)  # Adjust gap duration as needed
        times_dt.append(gap_time)

    # Convert back to strings for Plotly (ISO format)
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

    # Add live price line at last close price
    if last_close is not None:
        last_x = times[-2]  # The last real candle's timestamp (before the gap)

        fig.add_shape(
            type="line",
            x0=last_x,
            x1=times[-1],  # the gap timestamp or right edge
            y0=last_close,
            y1=last_close,
            line=dict(
                color="#000000",
                width=1,
                dash="dot",
            ),
        )

        # Add annotation at the end of the line
        fig.add_annotation(
            x=times[-1],  # place annotation at the gap (right edge)
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
        xaxis=dict(
            showgrid=False,
            rangeslider=dict(visible=True, thickness=0.05, bgcolor="#f5f5f5"),
            type="date",
            tickformat="%Y-%m-%d<br>%H:%M",
            showspikes=True,
            spikesnap="cursor",
            spikemode="across",
            spikecolor="#2196f3",
            spikedash="solid",
            spikethickness=1
        ),
        yaxis=dict(
            side="right",
            showspikes=True,
            spikesnap="cursor",
            spikemode="across",
            spikecolor="#2196f3",
            spikedash="solid",
            spikethickness=1,
            tickformat=".2f",
        ),
        hovermode="x unified",
        dragmode="pan",
        autosize=True,
        height=700,
        margin=dict(l=40, r=80, t=60, b=40),
        font=dict(family="Inter, Arial", size=14),
        plot_bgcolor="white",
        paper_bgcolor="white",
    )

    fig.update_xaxes(
        rangeselector=dict(
            buttons=list([
                dict(step="all", label="All"),
                dict(count=15, label="15m", step="minute", stepmode="backward"),
                dict(count=1, label="1H", step="hour", stepmode="backward"),
                dict(count=1, label="1D", step="day", stepmode="backward"),
            ]),
            x=0.01,
            y=1.05
        )
    )

    config = {
        "displayModeBar": True,
        "displaylogo": False,
        "scrollZoom": True,
        "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        "toImageButtonOptions": {"format": "png", "scale": 2}
    }

    html = fig.to_html(full_html=True, include_plotlyjs="cdn", config=config)

    # Your existing CSS + JS for OHLC info box
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
    # with open("candles_live.html", "w") as f:
    #     f.write(html)
    # print("Chart generated: candles_live.html")

    return html


if __name__ == "__main__":
    # Ensure we have the required environment
    if sys.version_info < (3, 7):
        print("Python 3.7 or higher is required")
        sys.exit(1)

    # Run the async main function
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nApplication interrupted")
        sys.exit(0)
