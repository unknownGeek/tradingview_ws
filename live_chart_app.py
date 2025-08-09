import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objs as go
import pandas as pd
import json
import os

DATA_PATH = os.path.join(os.path.dirname(__file__), 'candles_data.json')

def load_candles():
    if not os.path.exists(DATA_PATH):
        return pd.DataFrame(columns=['timestamp','open','high','low','close','volume'])
    with open(DATA_PATH, 'r') as f:
        data = json.load(f)
    return pd.DataFrame(data)

app = dash.Dash(__name__)
app.layout = html.Div([
    html.H2('Live Candlestick Chart'),
    dcc.Graph(id='live-candle-chart'),
    dcc.Interval(id='interval-component', interval=1000, n_intervals=0)
])

@app.callback(Output('live-candle-chart', 'figure'), [Input('interval-component', 'n_intervals')])
def update_chart(n):
    df = load_candles()
    if df.empty:
        return go.Figure()
    times = pd.to_datetime(df['timestamp'], unit='s').dt.strftime('%Y-%m-%d %H:%M')
    fig = go.Figure(data=[go.Candlestick(
        x=times,
        open=df['open'],
        high=df['high'],
        low=df['low'],
        close=df['close'],
        text=[f"Open: {o}<br>High: {h}<br>Low: {l}<br>Close: {cl}<br>Volume: {v}" for o, h, l, cl, v in zip(df['open'], df['high'], df['low'], df['close'], df['volume'])],
        hoverinfo='text',
        hovertext=[f"Time: {t}<br>Open: {o}<br>High: {h}<br>Low: {l}<br>Close: {cl}<br>Volume: {v}" for t, o, h, l, cl, v in zip(times, df['open'], df['high'], df['low'], df['close'], df['volume'])]
    )])
    fig.update_layout(
        xaxis_title="Time",
        yaxis_title="Price",
        yaxis=dict(tickformat=".2f"),
        xaxis=dict(type='category'),
        template="plotly_white",
        hovermode="x unified"
    )
    fig.update_yaxes(tickformat=".2f")
    return fig

if __name__ == '__main__':
    app.run_server(debug=True, port=8050)
