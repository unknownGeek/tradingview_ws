#!/usr/bin/env python3
"""
WebSocket to n8n Webhook Forwarder
Main entry point for the application
"""

import asyncio
import argparse
import threading
import sys
import websocket
import json
import random
import string
import time
from datetime import datetime
from collections import deque
from flask import Flask, jsonify
from datetime import datetime
import pytz

max_candle_window_len = 7
candle_window = deque(
    maxlen=max_candle_window_len)  # Store last 5/7/10 etc candles
current_candle = None
current_interval = None
timeframe_minute = 1

app = Flask(__name__)

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

    def __init__(self, symbol="OANDA:XAUUSD"):
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
    return {'values': candles}


async def main():

    while True:
        try:
            tv = TradingViewWS()
            threading.Thread(target=tv.run, daemon=True).start()

            # Start Flask server
            app.run(host='0.0.0.0', port=5000)
        except Exception as e:
            print(f"[!] Exception occurred: {e}. Reconnecting in 2 seconds...")
            time.sleep(2)


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
