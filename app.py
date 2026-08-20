"""Flask dashboard for the XSP next-session predictor."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict

from flask import Flask, jsonify, render_template, request

from database import prediction_stats, record_prediction
from xsp_predictor import MODEL_VERSION, run


app = Flask(__name__)
_cache: dict[str, object] = {}
_lock = threading.Lock()
CACHE_SECONDS = int(os.getenv("PREDICTION_CACHE_SECONDS", "900"))


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
        beats_baseline = prediction["validation_accuracy"] > max(
            prediction["always_up_accuracy"], prediction["momentum_accuracy"],
            prediction["fifty_fifty_accuracy"],
        )
        return render_template(
            "index.html", prediction=prediction, stories=stories[:10], cached=cached,
            beats_baseline=beats_baseline, error=None, stats=stats, saved=saved,
            storage_error=storage_error,
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
        return jsonify({"prediction": asdict(result), "headlines": stories,
                        "stats": prediction_stats(result.model_version), "cached": cached})
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
