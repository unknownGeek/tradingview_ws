import plotly.graph_objs as go
from datetime import datetime
from flask import Flask, jsonify
import requests

app = Flask(__name__)

def plot_candles_html(candles, title="Beautiful Interactive Candlestick Chart"):
    # Sort candles by timestamp ascending
    candles_sorted = sorted(candles, key=lambda x: x['timestamp'])
    times = [datetime.fromtimestamp(c['timestamp']).strftime('%Y-%m-%d %H:%M:%S') for c in candles_sorted]
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


@app.route("/candles")
def get_candles():

    # Example usage:
    api_url = "https://c1da7f58-f307-406d-917e-bddab526c7f6-00-3vam5nt790bd4.kirk.replit.dev/candles"
    candles = fetch_candles_from_api(api_url)
    c = candles['values']
    print(c)
    html = plot_candles_html(c)
    with open("candles_live.html", "w") as f:
        f.write(html)
    print("Chart generated: candles_live.html")

    return html


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
