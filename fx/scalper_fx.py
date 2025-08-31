import pandas as pd
from backtesting import Backtest, Strategy
from tvdatafeed import TvDatafeed, Interval
import os

# --- 1. Configuration ---
SYMBOL = 'XAUUSD'
EXCHANGE = 'OANDA'   # Options: OANDA, FOREXCOM, FX_IDC etc.
INTERVAL = Interval.in_1_day  # change to Interval.in_1_minute, Interval.in_15_minute etc.
OUTPUT_SIZE = 300
OUTPUT_FILENAME_CSV = 'custom_strategy_results.csv'
OUTPUT_FILENAME_HTML = 'custom_strategy_plot.html'

# --- 2. Function to Fetch Historical Data ---
def fetch_data(symbol, exchange, interval, output_size):
    """
    Fetches historical price data from TradingView via tvDatafeed.
    """
    print("Fetching historical data from TradingView...")
    try:
        tv = TvDatafeed()  # guest login (limited history)
        # OR: login with credentials for full data
        # tv = TvDatafeed(username='your_username', password='your_password')

        df = tv.get_hist(
            symbol=symbol,
            exchange=exchange,
            interval=interval,
            n_bars=output_size
        )

        if df is None or df.empty:
            print("❌ No data returned from TradingView.")
            return None

        # Format for backtesting.py
        df = df[['open', 'high', 'low', 'close', 'volume']].copy()
        df.rename(
            columns={
                'open': 'Open',
                'high': 'High',
                'low': 'Low',
                'close': 'Close',
                'volume': 'Volume'
            },
            inplace=True
        )
        print(f"✅ Successfully fetched {len(df)} data points for {symbol}.")
        return df

    except Exception as e:
        print(f"❌ Error fetching data: {e}")
        return None


# --- 3. Define Your Custom Trading Strategy ---
class CustomStrategy(Strategy):
    """
    Implements the user-defined strategy:
    - Long: 4 green candles with higher lows, then 1 red candle.
    - Short: 4 red candles with lower highs, then 1 green candle.
    - TP/SL: 2:1 reward/risk ratio.
    """
    def init(self):
        pass

    def next(self):
        if len(self.data.Close) < 5:
            return

        closes = self.data.Close[-5:]
        opens = self.data.Open[-5:]
        lows = self.data.Low[-5:]
        highs = self.data.High[-5:]

        is_green = lambda i: closes[i] >= opens[i]
        is_red = lambda i: opens[i] > closes[i]

        long_signal = (is_green(0) and is_green(1) and is_green(2) and is_green(3) and is_red(4) and
                       lows[0] > lows[1] and lows[1] > lows[2] and lows[2] > lows[3])

        short_signal = (is_red(0) and is_red(1) and is_red(2) and is_red(3) and is_green(4) and
                        highs[0] < highs[1] and highs[1] < highs[2] and highs[2] < highs[3])

        if not self.position:
            if long_signal:
                entry_price = closes[4]
                sl = lows[4]
                risk = entry_price - sl
                if risk > 0:
                    tp = entry_price + (risk * 2)
                    self.buy(sl=sl, tp=tp)

            elif short_signal:
                entry_price = closes[4]
                sl = highs[4]
                risk = sl - entry_price
                if risk > 0:
                    tp = entry_price - (risk * 2)
                    self.sell(sl=sl, tp=tp)


# --- 4. Main Execution Block ---
if __name__ == '__main__':
    data = fetch_data(SYMBOL, EXCHANGE, INTERVAL, OUTPUT_SIZE)

    if data is not None and not data.empty:
        print("Starting backtest with your custom strategy...")
        bt = Backtest(
            data,
            CustomStrategy,
            cash=10000,
            commission=.002,
            exclusive_orders=True
        )

        stats = bt.run()

        print("\n--- Backtest Results ---")
        print(stats)

        trades_df = stats['_trades']
        try:
            trades_df.to_csv(OUTPUT_FILENAME_CSV)
            print(f"\n✅ Trade results successfully saved to '{os.path.abspath(OUTPUT_FILENAME_CSV)}'")
        except Exception as e:
            print(f"\n❌ Error saving results to CSV: {e}")

        print("\nGenerating interactive plot... A new tab should open in your web browser.")
        bt.plot(
            open_browser=True,
            filename=OUTPUT_FILENAME_HTML
        )
