"""Flask dashboard for the XSP next-session predictor."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from database import prediction_stats, record_prediction
from swing_scanner import config as swing_config
from swing_scanner.scanner import download_bars as download_swing_bars
from swing_scanner.scanner import scan as scan_swing_symbols
from xsp_predictor import MODEL_VERSION, Prediction, analyze_short_put, run


app = Flask(__name__)
_cache: dict[str, object] = {}
_lock = threading.Lock()
_refresh_lock = threading.Lock()
CACHE_SECONDS = int(os.getenv("PREDICTION_CACHE_SECONDS", "900"))
PREDICTION_CACHE_FILE = Path(os.getenv("PREDICTION_CACHE_FILE", "data/prediction_cache.json"))
CSP_RESULTS_FILE = Path(__file__).parent / "legacy" / "csp_candidates_v4.csv"
CSP_SCRIPT_FILE = Path(__file__).parent / "legacy" / "v4.py"
CSP_CACHE_SECONDS = int(os.getenv("CSP_CACHE_SECONDS", "900"))
CSP_TIMEOUT_SECONDS = int(os.getenv("CSP_TIMEOUT_SECONDS", "600"))
_csp_lock = threading.Lock()
SWING_CACHE_SECONDS = int(os.getenv("SWING_CACHE_SECONDS", "900"))
SWING_CACHE_FILE = Path(os.getenv("SWING_CACHE_FILE", "data/swing_scan_cache.json"))
_swing_cache: dict[str, object] = {}
_swing_lock = threading.Lock()


def run_csp_screener(force: bool = False) -> None:
    """Generate V4 results when the saved market scan is missing or stale."""
    with _csp_lock:
        if CSP_RESULTS_FILE.exists():
            age = time.time() - CSP_RESULTS_FILE.stat().st_mtime
            if not force and age < CSP_CACHE_SECONDS:
                return

        completed = subprocess.run(
            [sys.executable, str(CSP_SCRIPT_FILE)],
            cwd=CSP_SCRIPT_FILE.parent,
            capture_output=True,
            text=True,
            timeout=CSP_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            if CSP_RESULTS_FILE.exists():
                app.logger.warning(
                    "CSP V4 refresh failed; serving stale results: %s", detail
                )
                return
            raise RuntimeError(
                f"CSP V4 scan failed with exit code {completed.returncode}: {detail}"
            )


def get_csp_results() -> tuple[list[dict[str, object]], str | None]:
    """Load the latest saved V4 screener output."""
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


def _load_swing_cache() -> None:
    if not SWING_CACHE_FILE.exists():
        return
    try:
        payload = json.loads(SWING_CACHE_FILE.read_text(encoding="utf-8"))
        _swing_cache.update(rows=payload["rows"], errors=payload.get("errors", {}),
                            time=float(payload["saved_at"]), generated=payload["generated"])
    except Exception:
        app.logger.exception("Could not load the saved swing scan")


def cached_swing_scan():
    with _swing_lock:
        rows = _swing_cache.get("rows")
        if rows is None:
            return None
        fresh = time.time() - float(_swing_cache.get("time", 0)) < SWING_CACHE_SECONDS
        return rows, _swing_cache.get("errors", {}), _swing_cache.get("generated"), fresh


def refresh_swing_scan_in_background(force: bool = False) -> bool:
    with _swing_lock:
        if _swing_cache.get("refreshing"):
            return False
        cached = _swing_cache.get("rows") is not None
        fresh = time.time() - float(_swing_cache.get("time", 0)) < SWING_CACHE_SECONDS
        if not force and cached and fresh:
            return False
        _swing_cache.update(refreshing=True, error=None)

    def refresh() -> None:
        try:
            results, errors = scan_swing_symbols(download_swing_bars(swing_config.WATCHLIST))
            saved_at = time.time()
            generated = time.strftime("%b %d, %Y at %I:%M %p", time.localtime(saved_at))
            rows = [result.to_dict() for result in results]
            payload = {"rows": rows, "errors": errors, "saved_at": saved_at,
                       "generated": generated}
            SWING_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = SWING_CACHE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            temporary.replace(SWING_CACHE_FILE)
            with _swing_lock:
                _swing_cache.update(payload)
                _swing_cache["time"] = saved_at
        except Exception as exc:
            app.logger.exception("Background swing scan failed")
            with _swing_lock:
                _swing_cache["error"] = str(exc)
        finally:
            with _swing_lock:
                _swing_cache["refreshing"] = False

    threading.Thread(target=refresh, name="swing-scan-refresh", daemon=True).start()
    return True


_load_swing_cache()


def _load_prediction_cache() -> None:
    if not PREDICTION_CACHE_FILE.exists():
        return
    try:
        payload = json.loads(PREDICTION_CACHE_FILE.read_text(encoding="utf-8"))
        _cache.update(result=Prediction(**payload["prediction"]),
                      stories=payload.get("stories", []), time=float(payload["saved_at"]))
    except Exception:
        app.logger.exception("Could not load the saved prediction cache")


def _save_prediction_cache(result: Prediction, stories: list[dict[str, str]], saved_at: float) -> None:
    PREDICTION_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = PREDICTION_CACHE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"prediction": asdict(result), "stories": stories,
                                     "saved_at": saved_at}), encoding="utf-8")
    temporary.replace(PREDICTION_CACHE_FILE)


def cached_prediction():
    """Return cached data immediately, including stale data."""
    with _lock:
        result = _cache.get("result")
        if not result:
            return None
        age = time.time() - float(_cache.get("time", 0))
        return result, _cache.get("stories", []), age < CACHE_SECONDS


def get_prediction(force: bool = False):
    cached = cached_prediction()
    if not force and cached and cached[2]:
        return cached[0], cached[1], True
    with _refresh_lock:
        cached = cached_prediction()
        if not force and cached and cached[2]:
            return cached[0], cached[1], True
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
        now = time.time()
        with _lock:
            _cache.update(result=result, stories=stories, time=now, error=None)
        _save_prediction_cache(result, stories, now)
        return result, stories, False


def refresh_prediction_in_background(force: bool = False) -> bool:
    with _lock:
        if _cache.get("refreshing"):
            return False
        _cache.update(refreshing=True, error=None)

    def refresh() -> None:
        try:
            get_prediction(force=force)
        except Exception as exc:
            app.logger.exception("Background prediction refresh failed")
            with _lock:
                _cache["error"] = str(exc)
        finally:
            with _lock:
                _cache["refreshing"] = False

    threading.Thread(target=refresh, name="prediction-refresh", daemon=True).start()
    return True


def refresh_stats_in_background(result: Prediction) -> bool:
    with _lock:
        if _cache.get("stats_refreshing"):
            return False
        _cache.update(stats_refreshing=True, storage_error=None)

    def refresh() -> None:
        try:
            saved = record_prediction(result)
            stats = prediction_stats(result.model_version)
            with _lock:
                _cache.update(saved=saved, stats=stats)
        except Exception as exc:
            app.logger.exception("Background persistence refresh failed")
            with _lock:
                _cache["storage_error"] = str(exc)
        finally:
            with _lock:
                _cache["stats_refreshing"] = False

    threading.Thread(target=refresh, name="prediction-stats-refresh", daemon=True).start()
    return True


_load_prediction_cache()


@app.get("/")
def index():
    try:
        force = request.args.get("refresh") == "1"
        cached_value = cached_prediction()
        if force or not cached_value or not cached_value[2]:
            refresh_prediction_in_background(force=force or bool(cached_value))
        if not cached_value:
            return render_template("index.html", prediction=None, stories=[], cached=False,
                                   refreshing=True, refresh_error=None, error=None)
        result, stories, _fresh = cached_value
        cached = True
        refresh_stats_in_background(result)
        with _lock:
            saved = bool(_cache.get("saved"))
            stats = _cache.get("stats")
            storage_error = _cache.get("storage_error")
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
            refreshing=bool(_cache.get("refreshing")), refresh_error=_cache.get("error"),
            latest_settled=(next((row for row in stats["recent"] if row["actual_direction"]), None)
                            if stats else None),
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


@app.get("/api/prediction/status")
def prediction_status():
    cached_value = cached_prediction()
    with _lock:
        return jsonify({"ready": cached_value is not None,
                        "fresh": bool(cached_value and cached_value[2]),
                        "refreshing": bool(_cache.get("refreshing")),
                        "error": _cache.get("error")})


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
        run_csp_screener(force=request.args.get("refresh") == "1")
        rows, generated = get_csp_results()
        return render_template("csp.html", rows=rows, generated=generated, error=None)
    except Exception as exc:
        app.logger.exception("CSP results failed")
        return render_template("csp.html", rows=[], generated=None, error=str(exc)), 503


@app.get("/swing")
def swing_screener():
    force = request.args.get("refresh") == "1"
    cached = cached_swing_scan()
    with _swing_lock:
        previous_error = _swing_cache.get("error")
    if force or (not cached and not previous_error) or (cached and not cached[3]):
        refresh_swing_scan_in_background(force=force)
    cached = cached_swing_scan()
    with _swing_lock:
        refreshing = bool(_swing_cache.get("refreshing"))
        error = _swing_cache.get("error")
    if not cached:
        return render_template("swing.html", rows=[], errors={}, generated=None,
                               refreshing=refreshing, error=error)
    rows, errors, generated, _fresh = cached
    return render_template("swing.html", rows=rows, errors=errors, generated=generated,
                           refreshing=refreshing, error=error)


@app.get("/api/swing/status")
def swing_status():
    cached = cached_swing_scan()
    with _swing_lock:
        return jsonify({"ready": cached is not None,
                        "fresh": bool(cached and cached[3]),
                        "refreshing": bool(_swing_cache.get("refreshing")),
                        "error": _swing_cache.get("error")})


@app.get("/methodology")
def methodology():
    try:
        result, stories, cached = get_prediction()
        prediction = asdict(result)
        beats_baseline = prediction["validation_accuracy"] > max(
            prediction["always_up_accuracy"], prediction["momentum_accuracy"],
            prediction["fifty_fifty_accuracy"],
        )
        return render_template(
            "methodology.html", prediction=prediction, stories=stories[:10],
            cached=cached, beats_baseline=beats_baseline, error=None,
        )
    except Exception as exc:
        app.logger.exception("Methodology failed")
        return render_template("methodology.html", prediction=None, stories=[],
                               cached=False, beats_baseline=False, error=str(exc)), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
