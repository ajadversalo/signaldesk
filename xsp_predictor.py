"""Research-grade next-session XSP direction predictor.

This is an educational signal, not investment advice. The technical model is
walk-forward validated. Live headline sentiment is an overlay because an RSS
feed is not a point-in-time historical news archive.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import numpy as np
import pandas as pd
import yfinance as yf
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score, brier_score_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer


AUX_SYMBOLS = ["SPY", "^VIX", "^VIX3M", "^TNX", "DX-Y.NYB", "ES=F", "RSP", "HYG", "TLT"]
FEATURES = [
    "ret_1", "ret_5", "ret_20", "gap", "range", "vol_10", "vol_20",
    "rsi_14", "sma_10_gap", "sma_50_gap", "spy_ret_1", "spy_volume_z20",
    "vix_level", "vix_change_1", "vix_term", "tnx_level", "tnx_change_5",
    "dxy_ret_5", "es_ret_1", "es_gap", "breadth_5", "credit_risk_5",
    "put_call", "event_tomorrow", "historical_news_sentiment",
]


@dataclass
class Prediction:
    generated_at_utc: str
    market_session_date: str
    forecast_for: str
    symbol: str
    market_data_symbol: str
    current_price: float
    market_session_complete: bool
    direction: str
    probability_up: float
    probability_down: float
    technical_probability_up: float
    news_sentiment: float
    news_weight: float
    headlines_used: int
    validation_accuracy: float
    validation_balanced_accuracy: float
    validation_brier_score: float
    validation_samples: int
    always_up_accuracy: float
    momentum_accuracy: float
    active_features: int
    total_features: int


def download_prices(symbol: str, period: str = "10y") -> pd.DataFrame:
    # Yahoo occasionally withholds index history from cloud-provider IP ranges.
    # For XSP direction, ^GSPC and SPY are valid directional proxies.
    candidates = [symbol]
    if symbol == "^XSP":
        candidates.extend(["^GSPC", "SPY"])
    errors = []
    for candidate in dict.fromkeys(candidates):
        try:
            data = yf.download(candidate, period=period, auto_adjust=True,
                               progress=False, threads=False, timeout=20)
            if data.empty:
                errors.append(f"{candidate}: empty response")
                continue
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)
            required = {"Open", "High", "Low", "Close", "Volume"}
            if not required.issubset(data.columns):
                errors.append(f"{candidate}: missing columns")
                continue
            data = data.sort_index()
            data.attrs["source_symbol"] = candidate
            return data
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
    raise RuntimeError("Market data providers returned no usable XSP proxy data (" +
                       "; ".join(errors) + ").")


def download_auxiliary(period: str) -> dict[str, pd.DataFrame]:
    """Download cross-market proxies in one request; missing tickers remain optional."""
    raw = yf.download(AUX_SYMBOLS, period=period, auto_adjust=True, progress=False,
                      group_by="ticker", threads=True)
    result = {}
    if raw.empty:
        return result
    for symbol in AUX_SYMBOLS:
        try:
            part = raw[symbol].dropna(how="all").copy()
            if not part.empty:
                result[symbol] = part
        except (KeyError, TypeError):
            continue
    return result


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / window, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _aligned_close(aux: dict[str, pd.DataFrame], symbol: str, index: pd.Index) -> pd.Series:
    if symbol not in aux or "Close" not in aux[symbol]:
        return pd.Series(np.nan, index=index)
    series = aux[symbol]["Close"].astype(float)
    series.index = pd.to_datetime(series.index).tz_localize(None)
    return series.reindex(index).ffill(limit=3)


def _optional_daily_csv(path: str | None, value_column: str, index: pd.Index) -> pd.Series:
    if not path or not Path(path).exists():
        return pd.Series(np.nan, index=index)
    data = pd.read_csv(path)
    if "date" not in data or value_column not in data:
        raise ValueError(f"{path} must contain date and {value_column} columns")
    values = pd.Series(pd.to_numeric(data[value_column], errors="coerce").values,
                       index=pd.to_datetime(data["date"]).dt.normalize())
    return values[~values.index.duplicated(keep="last")].reindex(index).ffill(limit=3)


def make_dataset(prices: pd.DataFrame, aux: dict[str, pd.DataFrame] | None = None,
                 put_call_csv: str | None = None, events_csv: str | None = None,
                 news_history_csv: str | None = None) -> pd.DataFrame:
    aux = aux or {}
    close, open_, high, low, volume = (prices[x].astype(float) for x in ["Close", "Open", "High", "Low", "Volume"])
    returns = close.pct_change()
    frame = pd.DataFrame(index=prices.index)
    frame["ret_1"] = returns
    frame["ret_5"] = close.pct_change(5)
    frame["ret_20"] = close.pct_change(20)
    frame["gap"] = open_ / close.shift(1) - 1
    frame["range"] = (high - low) / close
    frame["vol_10"] = returns.rolling(10).std() * math.sqrt(252)
    frame["vol_20"] = returns.rolling(20).std() * math.sqrt(252)
    frame["rsi_14"] = rsi(close) / 100
    frame["sma_10_gap"] = close / close.rolling(10).mean() - 1
    frame["sma_50_gap"] = close / close.rolling(50).mean() - 1
    spy = _aligned_close(aux, "SPY", frame.index)
    spy_volume = (aux.get("SPY", pd.DataFrame()).get("Volume", pd.Series(dtype=float)))
    spy_volume.index = pd.to_datetime(spy_volume.index).tz_localize(None)
    spy_volume = spy_volume.reindex(frame.index).ffill(limit=3)
    frame["spy_ret_1"] = spy.pct_change(fill_method=None)
    frame["spy_volume_z20"] = (spy_volume - spy_volume.rolling(20).mean()) / spy_volume.rolling(20).std()
    vix, vix3m = (_aligned_close(aux, s, frame.index) for s in ["^VIX", "^VIX3M"])
    frame["vix_level"] = vix / 100
    frame["vix_change_1"] = vix.pct_change(fill_method=None)
    frame["vix_term"] = vix / vix3m - 1
    tnx = _aligned_close(aux, "^TNX", frame.index)
    frame["tnx_level"] = tnx / 100
    frame["tnx_change_5"] = tnx.diff(5) / 100
    dxy = _aligned_close(aux, "DX-Y.NYB", frame.index)
    frame["dxy_ret_5"] = dxy.pct_change(5, fill_method=None)
    es = _aligned_close(aux, "ES=F", frame.index)
    frame["es_ret_1"] = es.pct_change(fill_method=None)
    if "ES=F" in aux and "Open" in aux["ES=F"]:
        es_open = aux["ES=F"]["Open"].astype(float)
        es_open.index = pd.to_datetime(es_open.index).tz_localize(None)
        frame["es_gap"] = es_open.reindex(frame.index).ffill(limit=3) / es.shift(1) - 1
    else:
        frame["es_gap"] = np.nan
    rsp = _aligned_close(aux, "RSP", frame.index)
    frame["breadth_5"] = rsp.pct_change(5, fill_method=None) - spy.pct_change(5, fill_method=None)
    hyg, tlt = (_aligned_close(aux, s, frame.index) for s in ["HYG", "TLT"])
    frame["credit_risk_5"] = hyg.pct_change(5, fill_method=None) - tlt.pct_change(5, fill_method=None)
    frame["put_call"] = _optional_daily_csv(put_call_csv, "put_call", frame.index)
    frame["historical_news_sentiment"] = _optional_daily_csv(
        news_history_csv, "sentiment", frame.index
    )
    frame["event_tomorrow"] = 0.0
    if events_csv:
        events = pd.read_csv(events_csv)
        if "date" not in events:
            raise ValueError(f"{events_csv} must contain a date column")
        event_dates = set(pd.to_datetime(events["date"]).dt.normalize())
        frame["event_tomorrow"] = [float((pd.Timestamp(day) + pd.offsets.BDay(1)).normalize() in event_dates)
                                     for day in frame.index]
    next_return = close.shift(-1) / close - 1
    frame["target"] = np.where(next_return > 0, "UP", "DOWN")
    frame.loc[frame.index[-1], "target"] = np.nan
    return frame.replace([np.inf, -np.inf], np.nan)


def model_pipeline() -> Pipeline:
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scale", StandardScaler()),
        ("model", LogisticRegression(C=0.25, max_iter=2000, class_weight="balanced")),
    ])


def fit_and_validate(frame: pd.DataFrame, validation_days: int = 252):
    labelled = frame.dropna(subset=["target"])
    if len(labelled) < validation_days + 252:
        raise RuntimeError("Not enough history for training and validation.")
    split = len(labelled) - validation_days
    test = labelled.iloc[split:]
    # Expanding walk-forward validation, refit monthly to balance rigor and speed.
    predictions, up_probabilities = [], []
    for start in range(split, len(labelled), 21):
        stop = min(start + 21, len(labelled))
        validator = model_pipeline().fit(labelled.iloc[:start][FEATURES], labelled.iloc[:start]["target"])
        block = labelled.iloc[start:stop]
        probs = validator.predict_proba(block[FEATURES])
        class_index = {name: i for i, name in enumerate(validator.classes_)}
        predictions.extend(validator.predict(block[FEATURES]))
        up_probabilities.extend(probs[:, class_index["UP"]])
    prior_direction = np.where(labelled["ret_1"].iloc[split:] > 0, "UP", "DOWN")
    metrics = {
        "accuracy": accuracy_score(test["target"], predictions),
        "balanced_accuracy": balanced_accuracy_score(test["target"], predictions),
        "brier": brier_score_loss((test["target"] == "UP").astype(int), up_probabilities),
        "samples": len(test),
        "always_up": accuracy_score(test["target"], ["UP"] * len(test)),
        "momentum": accuracy_score(test["target"], prior_direction),
    }
    final_model = model_pipeline().fit(labelled[FEATURES], labelled["target"])
    return final_model, metrics


def fetch_news(query: str, limit: int = 30) -> list[dict[str, str]]:
    encoded = urllib.parse.quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    feed = feedparser.parse(url)
    if getattr(feed, "bozo", False) and not feed.entries:
        raise RuntimeError(f"News feed failed: {feed.bozo_exception}")
    seen, stories = set(), []
    for entry in feed.entries:
        title = entry.get("title", "").strip()
        key = title.casefold()
        if title and key not in seen:
            seen.add(key)
            stories.append({"title": title, "link": entry.get("link", ""), "published": entry.get("published", "")})
        if len(stories) >= limit:
            break
    return stories


def score_news(stories: list[dict[str, str]]) -> float:
    if not stories:
        return 0.0
    analyzer = SentimentIntensityAnalyzer()
    compounds = [analyzer.polarity_scores(story["title"])["compound"] for story in stories]
    # Median is intentionally robust to one sensational headline.
    return float(np.median(compounds))


def next_business_day(day: pd.Timestamp) -> str:
    # Approximation for display only; exchange holidays require a calendar package.
    return str((day + pd.offsets.BDay(1)).date())


def session_is_complete(day: pd.Timestamp) -> bool:
    """Use a 15-minute post-close buffer for an in-progress daily Yahoo bar."""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    session_date = pd.Timestamp(day).date()
    return session_date < now_et.date() or (
        session_date == now_et.date() and (now_et.hour, now_et.minute) >= (16, 15)
    )


def save_news_observation(path: str | None, day: pd.Timestamp, sentiment: float) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = pd.read_csv(target) if target.exists() else pd.DataFrame(columns=["date", "sentiment"])
    row = pd.DataFrame([{"date": str(day.date()), "sentiment": sentiment}])
    output = row if existing.empty else pd.concat([existing, row], ignore_index=True)
    output = output.drop_duplicates("date", keep="last")
    output.to_csv(target, index=False)


def run(symbol: str, period: str, news_query: str, news_weight: float, validation_days: int,
        put_call_csv: str | None = None, events_csv: str | None = None,
        news_history_csv: str | None = None) -> tuple[Prediction, list[dict[str, str]]]:
    prices = download_prices(symbol, period)
    aux = download_auxiliary(period)
    frame = make_dataset(prices, aux, put_call_csv, events_csv, news_history_csv)
    model, metrics = fit_and_validate(frame, validation_days)
    raw = model.predict_proba(frame.iloc[[-1]][FEATURES])[0]
    technical_probs = dict(zip(model.classes_, raw))
    technical = float(technical_probs.get("UP", 0.0))
    try:
        stories = fetch_news(news_query)
        sentiment = score_news(stories)
    except Exception as exc:
        print(f"Warning: news unavailable ({exc}); using neutral sentiment.")
        stories, sentiment = [], 0.0
    last_day = pd.Timestamp(prices.index[-1])
    save_news_observation(news_history_csv, last_day, sentiment)
    # Map sentiment from [-1, 1] into a binary up probability.
    news_probs = {
        "UP": (sentiment + 1.0) / 2.0,
        "DOWN": (1.0 - sentiment) / 2.0,
    }
    combined_probs = {
        label: (1 - news_weight) * float(technical_probs.get(label, 0.0)) + news_weight * news_probs[label]
        for label in ("DOWN", "UP")
    }
    direction = max(combined_probs, key=combined_probs.get)
    active_features = int(frame[FEATURES].notna().any().sum())
    result = Prediction(
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        market_session_date=str(last_day.date()), forecast_for=next_business_day(last_day),
        symbol=symbol, market_data_symbol=str(prices.attrs.get("source_symbol", symbol)),
        current_price=round(float(prices["Close"].iloc[-1]), 4),
        market_session_complete=session_is_complete(last_day),
        direction=direction,
        probability_up=round(combined_probs["UP"], 4),
        probability_down=round(combined_probs["DOWN"], 4),
        technical_probability_up=round(technical, 4),
        news_sentiment=round(sentiment, 4), news_weight=news_weight,
        headlines_used=len(stories), validation_accuracy=round(metrics["accuracy"], 4),
        validation_balanced_accuracy=round(metrics["balanced_accuracy"], 4),
        validation_brier_score=round(metrics["brier"], 4), validation_samples=metrics["samples"],
        always_up_accuracy=round(metrics["always_up"], 4),
        momentum_accuracy=round(metrics["momentum"], 4),
        active_features=active_features, total_features=len(FEATURES),
    )
    return result, stories


def main() -> None:
    # Prevent a non-ASCII publisher/headline from crashing legacy Windows consoles.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="^XSP", help="Yahoo Finance ticker (default: ^XSP)")
    parser.add_argument("--period", default="10y")
    parser.add_argument("--news-query", default='(S&P 500 OR "Federal Reserve" OR inflation OR earnings) when:1d')
    parser.add_argument("--news-weight", type=float, default=0.15)
    parser.add_argument("--validation-days", type=int, default=252)
    parser.add_argument("--put-call-csv", help="Dated CSV with date,put_call columns")
    parser.add_argument("--events-csv", help="Dated CSV with a date column for known macro events")
    parser.add_argument("--news-history-csv", default="data/news_history.csv",
                        help="Dated sentiment archive; today's observation is appended")
    parser.add_argument("--show-headlines", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.news_weight <= 0.5:
        parser.error("--news-weight must be between 0 and 0.5")
    result, stories = run(args.symbol, args.period, args.news_query, args.news_weight,
                          args.validation_days, args.put_call_csv, args.events_csv,
                          args.news_history_csv)
    print(json.dumps(asdict(result), indent=2))
    if args.show_headlines:
        for story in stories:
            print(f"- {story['title']}\n  {story['link']}")


if __name__ == "__main__":
    main()
