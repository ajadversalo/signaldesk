"""Flask dashboard for the XSP next-session predictor."""

from __future__ import annotations

import csv
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from database import prediction_stats, record_prediction
from xsp_predictor import MODEL_VERSION, analyze_short_put, run


app = Flask(__name__)
_cache: dict[str, object] = {}
_lock = threading.Lock()
CACHE_SECONDS = int(os.getenv("PREDICTION_CACHE_SECONDS", "900"))
CSP_RESULTS_FILE = Path(__file__).parent / "legacy" / "csp_candidates_v4.csv"


def get_csp_results() -> tuple[list[dict[str, object]], str | None]:
    """Load the latest V4 screener output without rerunning the market scan."""
    if not CSP_RESULTS_FILE.exists():
        return [], None
    numeric_fields = {
        "ranking_score", "strike", "bid_credit", "annualized_return_pct",
        "abs_delta_est", "prob_itm_est_pct", "otm_pct", "spread_pct",
        "v2_relative_strength_score", "open_interest", "dte", "price",
    }
    rows: list[dict[str, object]] = []
    with CSP_RESULTS_FILE.open(encoding="utf-8-sig", newline="") as handle:
        for source in csv.DictReader(handle):
            row: dict[str, object] = dict(source)
            for field in numeric_fields:
                value = source.get(field, "").strip()
                row[field] = float(value) if value else None
            rows.append(row)
    generated = time.strftime(
        "%b %d, %Y at %I:%M %p",
        time.localtime(CSP_RESULTS_FILE.stat().st_mtime),
    )
    return rows, generated


def get_prediction(force: bool = False):
    now = time.time()
    with _lock:
        if not force and _cache.get("result") and now - float(_cache.get("time", 0)) < CACHE_SECONDS:
            return _cache["result"], _cache["stories"], True
        result, stories = run(
            symbol=os.getenv("PREDICTION_SYMBOL", "^XSP"),
            period=os.getenv("PREDICTION_PERIOD", "10y"),
            news_query=os.getenv(
                "NEWS_QUERY",
                '(S&P 500 OR "Federal Reserve" OR inflation OR earnings) when:1d',
            ),
            news_weight=float(os.getenv("NEWS_WEIGHT", "0.15")),
            validation_days=int(os.getenv("VALIDATION_DAYS", "252")),
            put_call_csv=os.getenv("PUT_CALL_CSV") or None,
            events_csv=os.getenv("EVENTS_CSV") or None,
            news_history_csv=os.getenv("NEWS_HISTORY_CSV", "data/news_history.csv"),
        )
        _cache.update(result=result, stories=stories, time=now)
        return result, stories, False


@app.get("/")
def index():
    try:
        result, stories, cached = get_prediction(force=request.args.get("refresh") == "1")
        storage_error = None
        try:
            saved = record_prediction(result)
            stats = prediction_stats(result.model_version)
        except Exception as exc:
            app.logger.exception("Persistence failed")
            saved, stats, storage_error = False, None, str(exc)
        prediction = asdict(result)
        short_put, short_put_error = None, None
        strike_value = request.args.get("strike", "")
        premium_value = request.args.get("premium", "")
        if strike_value or premium_value:
            try:
                if not strike_value or not premium_value:
                    raise ValueError("Enter both a strike and premium.")
                short_put = analyze_short_put(result, float(strike_value), float(premium_value))
            except ValueError as exc:
                short_put_error = str(exc)
        beats_baseline = prediction["validation_accuracy"] > max(
            prediction["always_up_accuracy"], prediction["momentum_accuracy"],
            prediction["fifty_fifty_accuracy"],
        )
        return render_template(
            "index.html", prediction=prediction, stories=stories[:10], cached=cached,
            beats_baseline=beats_baseline, error=None, stats=stats, saved=saved,
            storage_error=storage_error, short_put=short_put,
            short_put_error=short_put_error, strike_value=strike_value,
            premium_value=premium_value,
        )
    except Exception as exc:
        app.logger.exception("Prediction failed")
        return render_template("index.html", prediction=None, stories=[], cached=False,
                               beats_baseline=False, error=str(exc)), 503


@app.get("/api/prediction")
def prediction_api():
    try:
        result, stories, cached = get_prediction(force=request.args.get("refresh") == "1")
        record_prediction(result)
        short_put = None
        if request.args.get("strike") is not None or request.args.get("premium") is not None:
            if request.args.get("strike") is None or request.args.get("premium") is None:
                raise ValueError("Provide both strike and premium.")
            short_put = analyze_short_put(
                result, float(request.args["strike"]), float(request.args["premium"])
            )
        return jsonify({"prediction": asdict(result), "headlines": stories,
                        "stats": prediction_stats(result.model_version),
                        "short_put": short_put, "cached": cached})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/stats")
def stats_api():
    try:
        return jsonify(prediction_stats(MODEL_VERSION))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503


@app.get("/history")
def history():
    try:
        result, _, _ = get_prediction()
        record_prediction(result)
        return render_template("history.html", stats=prediction_stats(result.model_version), error=None)
    except Exception as exc:
        app.logger.exception("History failed")
        return render_template("history.html", stats=None, error=str(exc)), 503


@app.get("/csp")
def csp_screener():
    try:
        rows, generated = get_csp_results()
        return render_template("csp.html", rows=rows, generated=generated, error=None)
    except Exception as exc:
        app.logger.exception("CSP results failed")
        return render_template("csp.html", rows=[], generated=None, error=str(exc)), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
