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
import time
import plotly.graph_objs as go
from collections import deque
from flask import Flask, jsonify
from datetime import datetime
import pytz
import os

max_candle_window_len = 12
candle_window = deque(
    maxlen=max_candle_window_len)  # Store last 5/7/10 etc candles
current_candle = None
current_interval = None
timeframe_minute = 5

app = Flask(__name__)

# tradingview_symbol = "OANDA:XAUUSD"

tradingview_symbol = "BINANCE:BTCUSDT"

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
        volume = values.get("volume")
        if price is None:
            # print(f"price is None in {data}")
            return

        # Use current time as fallback if timestamp not available
        timestamp = time.time()

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
    ist = pytz.timezone("Asia/Kolkata")
    for c in candles:
        dt_utc = datetime.utcfromtimestamp(
            c['timestamp']).replace(tzinfo=pytz.utc)
        dt_ist = dt_utc.astimezone(ist)
        c['timestamp_ist'] = dt_ist.strftime("%Y-%m-%dT%H:%M:%S")

    if candles != []:
        candles = list(reversed(candles))

    # print(f"candles = {candles}")

    # print(f"Current candles(reversed): {candles}")
    jsonifyCandles = jsonify(candles)
    # print(f"Returning last {max_candle_window_len} candles: {jsonifyCandles}")
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
            port = int(os.environ.get("PORT", 5000))
            app.run(host="0.0.0.0", port=port)
        except Exception as e:
            print(f"[!] Exception occurred: {e}. Reconnecting in 2 seconds...")
            time.sleep(2)



def plot_candles_html(candles, title="Beautiful Interactive Candlestick Chart"):
    # Sort candles by timestamp ascending
    candles_sorted = sorted(candles, key=lambda x: x['timestamp'])
    times = [c['timestamp_ist'] for c in candles_sorted]
    opens = [c['open'] for c in candles_sorted]
    highs = [c['high'] for c in candles_sorted]
    lows = [c['low'] for c in candles_sorted]
    closes = [c['close'] for c in candles_sorted]
    volumes = [c['volume'] for c in candles_sorted]

    hover_text = [
        f"<b>Time:</b> {t}<br>"
        f"<b>Open:</b> {o}<br>"
        f"<b>High:</b> {h}<br>"
        f"<b>Low:</b> {l}<br>"
        f"<b>Close:</b> {cl}<br>"
        f"<b>Volume:</b> {v:,}"
        for t, o, h, l, cl, v in zip(times, opens, highs, lows, closes, volumes)
    ]

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=times,
        open=opens,
        high=highs,
        low=lows,
        close=closes,
        increasing_line_color='#26a69a',
        decreasing_line_color='#ef5350',
        hovertext=hover_text,
        hoverinfo='text',
        showlegend=False,
        name='Candles',
        yaxis='y1'
    ))
    fig.add_trace(go.Bar(
        x=times,
        y=volumes,
        marker_color="#90caf9",
        opacity=0.4,
        name="Volume",
        yaxis="y2",
        hoverinfo="skip"
    ))
    fig.update_layout(
        title=title,
        xaxis_title="Time",
        yaxis_title="Price",
        yaxis=dict(tickformat=".2f", showgrid=True, gridcolor="#e0e0e0", domain=[0.25, 1]),
        yaxis2=dict(title="Volume", anchor="x", overlaying=None, side="bottom", showgrid=False, domain=[0, 0.2]),
        xaxis=dict(type='category', showgrid=False),
        template="plotly_white",
        hovermode="x unified",
        margin=dict(l=40, r=40, t=60, b=40),
        font=dict(family="Inter, Arial", size=14),
        dragmode="zoom",
        autosize=True,
        height=None,
        width=None,
        xaxis_showspikes=True,
        yaxis_showspikes=True,
        xaxis_spikemode="across",
        yaxis_spikemode="across",
        xaxis_spikesnap="cursor",
        yaxis_spikesnap="cursor",
        xaxis_spikedash="dot",
        yaxis_spikedash="dot",
        xaxis_spikethickness=1,
        yaxis_spikethickness=1,
        xaxis_spikecolor="#90caf9",
        yaxis_spikecolor="#90caf9",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    fig.update_xaxes(rangeslider_visible=True)
    fig.update_yaxes(fixedrange=False)

    # Export to HTML string
    html = fig.to_html(full_html=True, include_plotlyjs="cdn")
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
