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
from flask import Flask, jsonify, redirect, render_template, request, send_from_directory

from database import (prediction_stats, record_prediction, record_stock_outlook,
                      stock_outlook_history)
from scan_runs import fail_run, finish_run, latest_runs, start_run
from stock_outlook import analyze_symbol, normalize_symbol
from swing_scanner import config as swing_config
from swing_scanner.scanner import download_bars as download_swing_bars
from swing_scanner.scanner import scan as scan_swing_symbols
from swing_scanner.persistence import save_predictions as save_swing_predictions
from swing_scanner.persistence import prediction_history as swing_prediction_history
from swing_scanner.persistence import pending_prediction_symbols
from swing_scanner.persistence import settle_predictions as settle_swing_predictions
from xsp_predictor import MODEL_VERSION, Prediction, analyze_short_put, run


app = Flask(__name__)


@app.get("/service-worker.js")
def service_worker():
    """Serve the worker at the root so it can control every page."""
    response = send_from_directory(app.static_folder, "service-worker.js")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


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
_csp_state: dict[str, object] = {}
_csp_state_lock = threading.Lock()
SWING_CACHE_SECONDS = int(os.getenv("SWING_CACHE_SECONDS", "900"))
SWING_CACHE_FILE = Path(os.getenv("SWING_CACHE_FILE", "data/swing_scan_cache.json"))
_swing_cache: dict[str, object] = {}
_swing_lock = threading.Lock()
_swing_settlement: dict[str, object] = {}
_swing_settlement_lock = threading.Lock()
_scan_run_cache: dict[str, object] = {}
_scan_run_lock = threading.Lock()


def _start_tracked_run(system: str) -> str | None:
    try:
        run_id = start_run(system)
        with _scan_run_lock:
            _scan_run_cache["loaded"] = False
        return run_id
    except Exception:
        app.logger.exception("Could not persist %s scan start", system)
        return None


def _finish_tracked_run(run_id: str | None, result_count: int | None = None,
                        error: Exception | None = None) -> None:
    if not run_id:
        return
    try:
        if error is None:
            finish_run(run_id, int(result_count or 0))
        else:
            fail_run(run_id, str(error))
    except Exception:
        app.logger.exception("Could not persist scan completion")
    finally:
        with _scan_run_lock:
            _scan_run_cache["loaded"] = False


def refresh_scan_run_history_in_background() -> bool:
    with _scan_run_lock:
        if _scan_run_cache.get("loading") or _scan_run_cache.get("loaded"):
            return False
        _scan_run_cache["loading"] = True

    def refresh() -> None:
        try:
            runs = latest_runs()
            with _scan_run_lock:
                _scan_run_cache.update(runs=runs, loaded=True, error=None)
        except Exception as exc:
            app.logger.exception("Could not load scan run history")
            with _scan_run_lock:
                _scan_run_cache["error"] = str(exc)
        finally:
            with _scan_run_lock:
                _scan_run_cache["loading"] = False

    threading.Thread(target=refresh, name="scan-run-history", daemon=True).start()
    return True


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
                raise RuntimeError(
                    f"CSP V4 refresh failed; previous saved results were preserved: {detail}"
                )
            raise RuntimeError(
                f"CSP V4 scan failed with exit code {completed.returncode}: {detail}"
            )
        if not CSP_RESULTS_FILE.exists():
            raise RuntimeError("CSP V4 finished without creating its results file.")


def refresh_csp_in_background(force: bool = True) -> bool:
    with _csp_state_lock:
        if _csp_state.get("refreshing"):
            return False
        _csp_state.update(refreshing=True, completed=False, error=None,
                          started_at=time.time(), notice=None)

    def refresh() -> None:
        run_id = _start_tracked_run("csp")
        try:
            run_csp_screener(force=force)
            rows, generated = get_csp_results()
            with _csp_state_lock:
                _csp_state.update(result_count=len(rows), generated=generated,
                                  completed=True,
                                  notice=("Scan completed with no qualifying contracts."
                                          if not rows else None))
            _finish_tracked_run(run_id, len(rows))
        except Exception as exc:
            app.logger.exception("Background CSP refresh failed")
            with _csp_state_lock:
                _csp_state["error"] = str(exc)
            _finish_tracked_run(run_id, error=exc)
        finally:
            with _csp_state_lock:
                _csp_state.update(refreshing=False, finished_at=time.time())

    threading.Thread(target=refresh, name="csp-refresh", daemon=True).start()
    return True


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


def group_csp_results_by_model(rows: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    """Group every qualifying contract under its stock, separately by model."""
    grouped: dict[str, dict[str, dict[str, object]]] = {"v2": {}, "v3": {}}
    for row in rows:
        dte = row.get("dte")
        if dte is None or not 7 <= float(dte) <= 14:
            continue
        symbol = str(row.get("symbol") or "")
        model = str(row.get("source_model") or "").lower()
        if not symbol or model not in {"v2", "v3"}:
            continue
        stock = grouped[model].setdefault(
            symbol, {"symbol": symbol, "price": row.get("price"), "contracts": []}
        )
        stock["contracts"].append(row)
    result: dict[str, list[dict[str, object]]] = {"v2": [], "v3": []}
    for model, stocks in grouped.items():
        for stock in stocks.values():
            stock["contracts"].sort(
                key=lambda contract: float(contract.get("ranking_score") or 0), reverse=True
            )
            stock["best_score"] = stock["contracts"][0].get("ranking_score")
        result[model] = sorted(
            stocks.values(), key=lambda stock: float(stock.get("best_score") or 0), reverse=True
        )
    return result


def _load_swing_cache() -> None:
    if not SWING_CACHE_FILE.exists():
        return
    try:
        payload = json.loads(SWING_CACHE_FILE.read_text(encoding="utf-8"))
        _swing_cache.update(rows=payload["rows"], errors=payload.get("errors", {}),
                            time=float(payload["saved_at"]), generated=payload["generated"],
                            saved=int(payload.get("saved", 0)),
                            forecast_for=payload.get("forecast_for"),
                            history=payload.get("history", []),
                            # Turso is authoritative. A scan cache may have been written
                            # before database history finished loading or under another
                            # deployment configuration, so verify once per app process.
                            history_loaded=False)
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
        run_id = _start_tracked_run("swing")
        try:
            bars_by_symbol = download_swing_bars(swing_config.WATCHLIST)
            settled = settle_swing_predictions(bars_by_symbol)
            results, errors = scan_swing_symbols(bars_by_symbol)
            saved, forecast_for = save_swing_predictions(results)
            history = swing_prediction_history()
            saved_at = time.time()
            generated = time.strftime("%b %d, %Y at %I:%M %p", time.localtime(saved_at))
            rows = [result.to_dict() for result in results]
            payload = {"rows": rows, "errors": errors, "saved_at": saved_at,
                       "generated": generated, "saved": saved,
                       "forecast_for": forecast_for, "history": history,
                       "history_loaded": True,
                       "history_complete": True,
                       "settled": settled}
            SWING_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = SWING_CACHE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            temporary.replace(SWING_CACHE_FILE)
            with _swing_lock:
                _swing_cache.update(payload)
                _swing_cache["time"] = saved_at
            _finish_tracked_run(run_id, sum(result.candidate for result in results))
        except Exception as exc:
            app.logger.exception("Background swing scan failed")
            with _swing_lock:
                _swing_cache["error"] = str(exc)
            _finish_tracked_run(run_id, error=exc)
        finally:
            with _swing_lock:
                _swing_cache["refreshing"] = False

    threading.Thread(target=refresh, name="swing-scan-refresh", daemon=True).start()
    return True


def refresh_swing_history_in_background() -> bool:
    """Load saved predictions independently of the slower market scan."""
    with _swing_lock:
        if _swing_cache.get("history_refreshing") or _swing_cache.get("history_loaded"):
            return False
        _swing_cache["history_refreshing"] = True

    def refresh() -> None:
        try:
            history = swing_prediction_history()
            with _swing_lock:
                _swing_cache.update(history=history, history_loaded=True, history_error=None)
        except Exception as exc:
            app.logger.exception("Background swing history load failed")
            with _swing_lock:
                _swing_cache["history_error"] = str(exc)
        finally:
            with _swing_lock:
                _swing_cache["history_refreshing"] = False

    threading.Thread(target=refresh, name="swing-history-refresh", daemon=True).start()
    return True


def settle_swing_in_background() -> bool:
    """Settle pending calls without running the watchlist strategy."""
    with _swing_settlement_lock:
        if _swing_settlement.get("running"):
            return False
        _swing_settlement.update(running=True, error=None, settled=0)

    def settle() -> None:
        try:
            symbols = pending_prediction_symbols()
            settled = 0
            if symbols:
                bars = download_swing_bars(symbols)
                settled = settle_swing_predictions(bars)
            history = swing_prediction_history()
            with _swing_lock:
                _swing_cache.update(history=history, history_loaded=True)
            with _swing_settlement_lock:
                _swing_settlement["settled"] = settled
        except Exception as exc:
            app.logger.exception("Swing result settlement failed")
            with _swing_settlement_lock:
                _swing_settlement["error"] = str(exc)
        finally:
            with _swing_settlement_lock:
                _swing_settlement["running"] = False

    threading.Thread(target=settle, name="swing-settlement", daemon=True).start()
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
        run_id = _start_tracked_run("xsp")
        try:
            result, _stories, _cached = get_prediction(force=force)
            saved = record_prediction(result)
            stats = prediction_stats(result.model_version)
            with _lock:
                _cache.update(saved=saved, stats=stats, stats_loaded=True,
                              storage_error=None)
            _finish_tracked_run(run_id, 1)
        except Exception as exc:
            app.logger.exception("Background prediction refresh failed")
            with _lock:
                _cache["error"] = str(exc)
            _finish_tracked_run(run_id, error=exc)
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


def refresh_xsp_history_in_background() -> bool:
    """Load XSP history without generating or recording a prediction."""
    with _lock:
        if _cache.get("stats_refreshing") or _cache.get("stats_loaded"):
            return False
        _cache["stats_refreshing"] = True

    def refresh() -> None:
        try:
            stats = prediction_stats(MODEL_VERSION)
            with _lock:
                _cache.update(stats=stats, stats_loaded=True, storage_error=None)
        except Exception as exc:
            app.logger.exception("Background XSP history load failed")
            with _lock:
                _cache["storage_error"] = str(exc)
        finally:
            with _lock:
                _cache["stats_refreshing"] = False

    threading.Thread(target=refresh, name="xsp-history-refresh", daemon=True).start()
    return True


_load_prediction_cache()


@app.get("/")
def dashboard():
    refresh_scan_run_history_in_background()
    prediction_cache = cached_prediction()
    prediction = asdict(prediction_cache[0]) if prediction_cache else None
    refresh_xsp_history_in_background()

    try:
        csp_rows, csp_generated = get_csp_results()
    except Exception:
        app.logger.exception("Dashboard CSP summary failed")
        csp_rows, csp_generated = [], None
    csp_stocks_by_model = group_csp_results_by_model(csp_rows)
    csp_saved_by_model = {
        model: sorted(stocks, key=lambda stock: float(stock.get("best_score") or 0),
                      reverse=True)
        for model, stocks in csp_stocks_by_model.items()
    }

    swing_cache = cached_swing_scan()
    refresh_swing_history_in_background()
    swing_cache = cached_swing_scan()
    swing_rows = swing_cache[0] if swing_cache else []
    swing_candidates = [row for row in swing_rows if row.get("candidate")][:swing_config.MAX_RESULTS]

    with _lock:
        stats = _cache.get("stats")
        xsp_refreshing = bool(_cache.get("refreshing"))
        xsp_history_loading = bool(_cache.get("stats_refreshing"))
    with _swing_lock:
        swing_refreshing = bool(_swing_cache.get("refreshing"))
        swing_history = list(_swing_cache.get("history", []))
        swing_history_loading = bool(_swing_cache.get("history_refreshing"))
    with _csp_state_lock:
        csp_refreshing = bool(_csp_state.get("refreshing"))
        csp_error = _csp_state.get("error")
    with _swing_settlement_lock:
        swing_settlement_running = bool(_swing_settlement.get("running"))
    with _scan_run_lock:
        scan_runs = dict(_scan_run_cache.get("runs", {}))
        scan_runs_loading = bool(_scan_run_cache.get("loading"))

    return render_template(
        "dashboard.html", prediction=prediction, stats=stats,
        scan_runs=scan_runs, scan_runs_loading=scan_runs_loading,
        xsp_refreshing=xsp_refreshing, xsp_history_loading=xsp_history_loading,
        csp_rows=csp_rows, csp_generated=csp_generated,
        csp_saved_v3=csp_saved_by_model.get("v3", []),
        csp_saved_v2=csp_saved_by_model.get("v2", []),
        csp_v2=sum(row.get("source_model") == "V2" for row in csp_rows),
        csp_v3=sum(row.get("source_model") == "V3" for row in csp_rows),
        csp_refreshing=csp_refreshing, csp_error=csp_error,
        csp_has_scan=csp_generated is not None,
        swing_candidates=swing_candidates,
        swing_history=swing_history[:6],
        swing_generated=(swing_cache[2] if swing_cache else None),
        swing_refreshing=swing_refreshing,
        swing_history_loading=swing_history_loading,
        swing_settlement_running=swing_settlement_running,
        swing_forecast=_swing_cache.get("forecast_for"),
    )


def strategy_is_running() -> bool:
    """Keep memory-heavy strategy jobs from overlapping on small instances."""
    return bool(_cache.get("refreshing") or _csp_state.get("refreshing") or
                _swing_cache.get("refreshing") or _swing_settlement.get("running"))


@app.post("/api/run/xsp")
def run_xsp_api():
    if strategy_is_running():
        return jsonify({"started": False, "error": "Another strategy is already running."}), 409
    started = refresh_prediction_in_background(force=True)
    return jsonify({"started": started, "running": True}), 202


@app.post("/api/run/csp")
def run_csp_api():
    if strategy_is_running():
        return jsonify({"started": False, "error": "Another strategy is already running."}), 409
    started = refresh_csp_in_background(force=True)
    return jsonify({"started": started, "running": True}), 202


@app.post("/api/run/swing")
def run_swing_api():
    if strategy_is_running():
        return jsonify({"started": False, "error": "Another strategy is already running."}), 409
    started = refresh_swing_scan_in_background(force=True)
    return jsonify({"started": started, "running": True}), 202


@app.post("/api/settle/swing")
def settle_swing_api():
    if strategy_is_running():
        return jsonify({"started": False, "error": "A strategy is already running."}), 409
    started = settle_swing_in_background()
    return jsonify({"started": started, "running": True}), 202


@app.get("/xsp")
def xsp_signal():
    try:
        cached_value = cached_prediction()
        refresh_xsp_history_in_background()
        if not cached_value:
            return render_template("index.html", prediction=None, stories=[], cached=False,
                                   refreshing=bool(_cache.get("refreshing")),
                                   refresh_error=None, error=None)
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
        cached_value = cached_prediction()
        if not cached_value:
            return jsonify({"error": "No saved XSP prediction. Run XSP from the dashboard."}), 404
        result, stories, fresh = cached_value
        cached = True
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
                        "stats_ready": _cache.get("stats") is not None,
                        "stats_refreshing": bool(_cache.get("stats_refreshing")),
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
    return redirect("/xsp#history", code=302)


@app.get("/outlook")
def stock_outlook():
    symbol = request.args.get("symbol", "").strip()
    result, error, storage_error = None, None, None
    if symbol:
        try:
            symbol = normalize_symbol(symbol)
            result = analyze_symbol(symbol)
        except Exception as exc:
            app.logger.warning("Stock outlook failed for %s: %s", symbol, exc)
            error = str(exc)
        if result:
            try:
                record_stock_outlook(result)
            except Exception as exc:
                app.logger.exception("Could not save stock outlook")
                storage_error = str(exc)
    try:
        history = stock_outlook_history()
    except Exception as exc:
        app.logger.exception("Stock outlook history failed")
        history = []
        storage_error = storage_error or str(exc)
    return render_template("outlook.html", symbol=symbol, result=result, error=error,
                           history=history, storage_error=storage_error)


@app.get("/api/outlook/<symbol>")
def stock_outlook_api(symbol: str):
    try:
        result = analyze_symbol(symbol)
        record_stock_outlook(result)
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        app.logger.exception("Stock outlook API failed")
        return jsonify({"error": str(exc)}), 503


@app.get("/csp")
def csp_screener():
    try:
        rows, generated = get_csp_results()
        stocks = group_csp_results_by_model(rows)
        with _csp_state_lock:
            error = _csp_state.get("error")
            refreshing = bool(_csp_state.get("refreshing"))
        return render_template("csp.html", rows=rows, v2_stocks=stocks["v2"],
                               v3_stocks=stocks["v3"], generated=generated,
                               error=error, refreshing=refreshing)
    except Exception as exc:
        app.logger.exception("CSP results failed")
        return render_template("csp.html", rows=[], v2_stocks=[], v3_stocks=[],
                               generated=None, error=str(exc)), 503


@app.get("/api/csp/status")
def csp_status():
    with _csp_state_lock:
        return jsonify({"refreshing": bool(_csp_state.get("refreshing")),
                        "completed": bool(_csp_state.get("completed")),
                        "result_count": _csp_state.get("result_count"),
                        "generated": _csp_state.get("generated"),
                        "notice": _csp_state.get("notice"),
                        "error": _csp_state.get("error")})


@app.get("/swing")
def swing_screener():
    cached = cached_swing_scan()
    refresh_swing_history_in_background()
    cached = cached_swing_scan()
    with _swing_lock:
        refreshing = bool(
            _swing_cache.get("refreshing") or _swing_cache.get("history_refreshing")
        )
        error = _swing_cache.get("error") or _swing_cache.get("history_error")
        history_refreshing = bool(_swing_cache.get("history_refreshing"))
        history_error = _swing_cache.get("history_error")
        history = list(_swing_cache.get("history", []))
        saved = int(_swing_cache.get("saved", 0))
        forecast_for = _swing_cache.get("forecast_for")
    if not cached:
        return render_template("swing.html", rows=[], errors={}, generated=None,
                               refreshing=refreshing, error=error, saved=0,
                               forecast_for=forecast_for, history=history,
                               scanner_version=swing_config.SCANNER_VERSION,
                               history_refreshing=history_refreshing,
                               history_error=history_error)
    rows, errors, generated, _fresh = cached
    return render_template("swing.html", rows=rows, errors=errors, generated=generated,
                           refreshing=refreshing, error=error,
                           saved=saved, forecast_for=forecast_for, history=history,
                           scanner_version=swing_config.SCANNER_VERSION,
                           history_refreshing=history_refreshing,
                           history_error=history_error)


@app.get("/api/swing/status")
def swing_status():
    cached = cached_swing_scan()
    with _swing_lock:
        return jsonify({"ready": cached is not None,
                        "fresh": bool(cached and cached[3]),
                        "refreshing": bool(
                            _swing_cache.get("refreshing") or
                            _swing_cache.get("history_refreshing")
                        ),
                        "history_ready": bool(_swing_cache.get("history_loaded")),
                        "history_refreshing": bool(_swing_cache.get("history_refreshing")),
                        "history_error": _swing_cache.get("history_error"),
                        "error": _swing_cache.get("error")})


@app.get("/methodology")
def methodology():
    try:
        cached_value = cached_prediction()
        if not cached_value:
            return render_template("methodology.html", prediction=None, stories=[],
                                   cached=False, beats_baseline=False,
                                   error="No saved XSP prediction. Run XSP from the dashboard."), 404
        result, stories, _fresh = cached_value
        cached = True
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
