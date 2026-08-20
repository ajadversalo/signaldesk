"""CSP Screener V4: independent V2 and V3 results using Yahoo quotes."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from html import escape
import json
from pathlib import Path

import pandas as pd
import yfinance as yf
from ib_insync import IB

import v3


OUTPUT_FILE = Path(__file__).with_name("csp_candidates_v4.csv")
V2_OUTPUT_FILE = Path(__file__).with_name("csp_candidates_v4_v2.csv")
V3_OUTPUT_FILE = Path(__file__).with_name("csp_candidates_v4_v3.csv")
REPORT_FILE = Path(__file__).with_name("csp_results_v4.html")
REJECTIONS_FILE = Path(__file__).with_name("csp_v3_rejections_v4.csv")


def v2_stock_components(history: pd.DataFrame, spy_close: pd.Series, info: dict) -> dict:
    """Recreate the useful v2 stock signals without v2's duplicated code."""
    data = v3.indicators(history)
    row, previous = data.iloc[-1], data.iloc[-2]
    close = data["Close"].dropna()
    spy = spy_close.dropna()

    trend = 0.0
    trend += 5 if row["Close"] > row["SMA20"] else 0
    trend += 5 if row["Close"] > row["SMA50"] else 0
    trend += 10 if row["Close"] > row["SMA200"] else 0
    trend += 5 if row["SMA20"] > row["SMA50"] else 0
    trend += 5 if row["SMA50"] > row["SMA200"] else 0
    trend += 5 if row["SMA200"] > previous["SMA200"] else 0

    momentum = 0.0
    momentum += 5 if 50 <= v3.number(row["RSI"], 0) <= 70 else 0
    momentum += 5 if row["MACD"] > row["MACD_SIGNAL"] else 0
    momentum += 5 if row["ROC20"] > 0 else 0
    momentum += 5 if row["ROC20"] > 5 else 0

    relative = 0.0
    relative_values = []
    for periods in (21, 63, 126):
        if len(close) > periods and len(spy) > periods:
            stock_return = close.iloc[-1] / close.iloc[-periods - 1] - 1
            spy_return = spy.iloc[-1] / spy.iloc[-periods - 1] - 1
            value = (stock_return - spy_return) * 100
            relative_values.append(value)
            relative += 5 if value > 0 else 0
        else:
            relative_values.append(None)

    fundamentals = 0.0
    fundamentals += 3 if v3.number(info.get("revenueGrowth"), -1) > 0 else 0
    fundamentals += 3 if v3.number(info.get("earningsGrowth"), -1) > 0 else 0
    fundamentals += 4 if v3.number(info.get("trailingPE"), -1) > 0 else 0

    stock_risk = 0.0
    stock_risk += 2 if v3.number(info.get("marketCap"), 0) >= 20_000_000_000 else 0
    beta = v3.number(info.get("beta"))
    stock_risk += 2 if beta is not None and beta < 2 else 0
    debt = v3.number(info.get("debtToEquity"))
    stock_risk += 2 if debt is not None and debt < 150 else 0
    stock_risk += 2 if v3.number(info.get("freeCashflow"), 0) > 0 else 0

    # The original v2 stock components total 88 points before option liquidity.
    raw = trend + momentum + relative + fundamentals + stock_risk
    return {
        "v2_trend_score": trend,
        "v2_momentum_score": momentum,
        "v2_relative_strength_score": relative,
        "v2_fundamentals_score": fundamentals,
        "v2_stock_risk_score": stock_risk,
        "v2_stock_score": round(raw / 88 * 100, 2),
        "v2_rs_1m_pct": v3.rounded(relative_values[0]),
        "v2_rs_3m_pct": v3.rounded(relative_values[1]),
        "v2_rs_6m_pct": v3.rounded(relative_values[2]),
    }


def v2_option_score(candidate: dict) -> float:
    """Apply v2's option formula using v3's conservative bid credit."""
    strike = v3.number(candidate.get("strike"), 0)
    bid = v3.number(candidate.get("bid_credit"), 0)
    otm = v3.number(candidate.get("otm_pct"), 0)
    oi = v3.number(candidate.get("open_interest"), 0)
    dte = int(v3.number(candidate.get("dte"), 0))
    yield_pct = bid / strike * 100 if strike > 0 else 0
    score = v3.clamp(yield_pct * 12, 0, 35)
    score += v3.clamp(otm * 3, 0, 25)
    score += v3.clamp(oi / 50, 0, 20)
    score += 20 if 14 <= dte <= 21 else (10 if dte <= 30 else 0)
    return round(min(score, 100), 2)


def scan_v2_candidates(
    ticker: yf.Ticker,
    history: pd.DataFrame,
    info: dict,
    components: dict,
) -> list[dict]:
    """Run the cleaned v2 contract logic independently of v3's hard filters."""
    if len(history) < 220:
        return []
    data = v3.indicators(history)
    row = data.iloc[-1]
    price = v3.number(row.get("Close"))
    if price is None or price <= 0 or price > 100:
        return []
    earnings = None if str(info.get("quoteType", "")).upper() in {"ETF", "MUTUALFUND", "INDEX"} else v3.next_earnings_date(ticker)
    earnings_days = (earnings - datetime.now().date()).days if earnings else None
    if earnings_days is not None and earnings_days <= 21:
        return []

    stock_raw = sum(components[key] for key in (
        "v2_trend_score", "v2_momentum_score", "v2_relative_strength_score",
        "v2_fundamentals_score", "v2_stock_risk_score",
    )) + 2  # v2's no-earnings-in-window contribution
    found: list[dict] = []
    for expiry, dte in v3.valid_expirations(ticker):
        if dte > 21:
            continue
        puts = ticker.option_chain(expiry).puts
        for _, put in puts.iterrows():
            strike = v3.number(put.get("strike"))
            bid = v3.number(put.get("bid"))
            ask = v3.number(put.get("ask"))
            oi = v3.number(put.get("openInterest"), 0)
            if strike is None or bid is None or ask is None or strike >= price or min(bid, ask) <= 0 or oi < 100:
                continue
            otm_pct = (price - strike) / price * 100
            if otm_pct < 5:
                continue
            midpoint = (bid + ask) / 2
            spread_pct = (ask - bid) / ask * 100
            liquidity = (4 if v3.number(row.get("AVG_VOL20"), 0) >= 1_000_000 else 0)
            liquidity += 4 if oi >= 500 else 0
            liquidity += 2 if spread_pct <= 5 else 0
            option_input = {
                "strike": strike, "bid_credit": midpoint, "otm_pct": otm_pct,
                "open_interest": oi, "dte": dte,
            }
            option_score = v2_option_score(option_input)
            v2_score = round(0.70 * (stock_raw + liquidity) + 0.30 * option_score, 2)
            found.append({
                "symbol": str(info.get("symbol") or getattr(ticker, "ticker", "")),
                "contract": put.get("contractSymbol", ""), "price": v3.rounded(price),
                "expiry": expiry, "dte": dte, "strike": v3.rounded(strike),
                "bid_credit": v3.rounded(midpoint), "otm_pct": v3.rounded(otm_pct),
                "annualized_return_pct": v3.rounded(midpoint / strike * 100 * 365 / dte),
                "open_interest": int(oi), "spread_pct": v3.rounded(spread_pct),
                "quote_source": "Yahoo Finance (V2)", "v2_option_score": option_score,
                "v2_score": v2_score, **components,
            })
    return found


def prepare_v3_candidate(candidate: dict) -> dict:
    result = dict(candidate)
    if str(result.get("quote_source", "")).startswith("Yahoo Finance"):
        result["quote_source"] = "Yahoo Finance (V3)"
    result["v3_quality_score"] = result.pop("quality_score")
    result["v3_safety_score"] = result.pop("safety_score")
    result["v3_income_score"] = result.pop("income_score")
    result["v3_liquidity_score"] = result.pop("liquidity_score")
    result["v3_score"] = result.pop("score")
    return result


def separate_results(v2_rows: list[dict], v3_rows: list[dict]) -> list[dict]:
    """Keep the two model outputs independent; do not match contracts."""
    rows = []
    for item in v2_rows:
        row = dict(item)
        row.update({"source_model": "V2", "v3_score": None, "v4_score": None})
        row["ranking_score"] = row["v2_score"]
        rows.append(row)
    for item in v3_rows:
        row = dict(item)
        row.update({"source_model": "V3", "v2_score": None, "v4_score": None})
        row["ranking_score"] = row["v3_score"]
        rows.append(row)
    return rows


def render_report(frame: pd.DataFrame, diagnostics: dict) -> None:
    records = frame.where(pd.notna(frame), None).to_dict(orient="records")
    payload = json.dumps(records, separators=(",", ":")).replace("</", "<\\/")
    generated = datetime.now().astimezone().strftime("%b %d, %Y at %I:%M %p %Z")
    source = escape(str(diagnostics.get("quote_source", "No qualifying quotes")))
    html = REPORT_TEMPLATE.replace("__DATA__", payload)
    html = html.replace("__GENERATED__", escape(generated)).replace("__SOURCE__", source)
    REPORT_FILE.write_text(html, encoding="utf-8")


REPORT_TEMPLATE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Waverider CSP Screener V4</title><style>
:root{--ink:#14251f;--muted:#65736c;--paper:#f3f5ef;--card:#fff;--line:#dce4dc;--green:#164f3b;--lime:#c9f36b;--blue:#315f9a;--amber:#b5661a}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 85% 0,#dfead9 0,transparent 32rem),var(--paper);color:var(--ink);font:14px/1.5 Inter,ui-sans-serif,system-ui}main{max-width:1440px;margin:auto;padding:42px 28px 64px}.hero{display:grid;grid-template-columns:1.3fr .7fr;gap:28px;align-items:end}.eyebrow{letter-spacing:.18em;font-size:11px;font-weight:800;color:var(--green)}h1{font:500 clamp(40px,6vw,78px)/.96 Georgia,serif;margin:8px 0 18px;max-width:850px}.lede{font-size:17px;color:var(--muted);max-width:710px}.formula{background:var(--ink);color:white;padding:22px;border-radius:18px}.formula strong{display:block;font-size:28px;color:var(--lime)}.formula small{color:#b9c7c0}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:30px 0}.stat,.panel{background:#ffffffd9;border:1px solid var(--line);box-shadow:0 12px 40px #203a2c0b;border-radius:16px}.stat{padding:18px}.stat span{display:block;color:var(--muted);font-size:12px}.stat strong{font-size:24px}.controls{display:flex;gap:12px;align-items:center;margin:0 0 14px;flex-wrap:wrap}input,select{background:white;border:1px solid var(--line);border-radius:10px;padding:11px 13px;color:var(--ink)}input{min-width:230px}.legend{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap}.pill{border-radius:999px;padding:5px 9px;font-weight:750;font-size:11px}.v2{background:#e7effb;color:var(--blue)}.v3{background:#fff0df;color:var(--amber)}.v4{background:#e6f3c7;color:#376111}.panel{overflow:auto}table{width:100%;border-collapse:collapse;min-width:1180px}th{position:sticky;top:0;background:#f9fbf7;color:var(--muted);font-size:11px;letter-spacing:.08em;text-transform:uppercase;cursor:pointer}th,td{text-align:left;padding:14px 13px;border-bottom:1px solid #edf1ec;white-space:nowrap}tbody tr:hover{background:#f7faF4}.ticker{font-size:16px;font-weight:850}.score{font-size:18px;font-weight:850}.meter{height:5px;width:64px;background:#e6ebe5;border-radius:9px;overflow:hidden;margin-top:5px}.meter i{display:block;height:100%;background:var(--green)}.muted{color:var(--muted)}.empty{text-align:center;padding:60px;color:var(--muted)}footer{color:var(--muted);font-size:12px;margin-top:20px}@media(max-width:760px){main{padding:25px 14px}.hero{grid-template-columns:1fr}.stats{grid-template-columns:1fr 1fr}.legend{margin-left:0}}
</style></head><body><main>
<section class="hero"><div><div class="eyebrow">WAVERIDER / CSP SCREENER V4</div><h1>Two models. Two honest result sets.</h1><p class="lede">V2 ranks market quality and income independently. V3 applies its stricter assignment-risk budget independently. No joining, score filling, or cross-model filtering.</p></div><aside class="formula"><small>REPORT STRUCTURE</small><strong>V2 / V3</strong><small>Both use the same Yahoo Finance quote source, but retain their own selection and scoring logic.</small></aside></section>
<section class="stats"><div class="stat"><span>VISIBLE RESULTS</span><strong id="count">0</strong></div><div class="stat"><span>V2 RESULTS</span><strong id="v2count">0</strong></div><div class="stat"><span>V3 RESULTS</span><strong id="v3count">0</strong></div><div class="stat"><span>QUOTE SOURCE</span><strong style="font-size:15px">__SOURCE__</strong></div></section>
<div class="controls"><input id="search" aria-label="Search ticker" placeholder="Search ticker or contract"><select id="model" aria-label="Model filter"><option value="all">Show V2 and V3</option><option>V2</option><option>V3</option></select><select id="risk" aria-label="Risk filter"><option value="all">All results</option><option value="10">V3 delta ≤ 0.10</option><option value="8">V3 delta ≤ 0.08</option></select><div class="legend"><span class="pill v2">V2 independent</span><span class="pill v3">V3 independent</span></div></div>
<section class="panel"><table><thead><tr><th data-key="source_model">Model</th><th data-key="symbol">Ticker</th><th data-key="ranking_score">Model score</th><th data-key="strike">Contract</th><th data-key="bid_credit">Credit</th><th data-key="annualized_return_pct">Annualized</th><th data-key="abs_delta_est">V3 Delta</th><th data-key="prob_itm_est_pct">V3 Prob. ITM</th><th data-key="otm_pct">OTM</th><th data-key="v2_relative_strength_score">V2 RS</th><th data-key="spread_pct">Spread</th><th data-key="quote_source">Data</th></tr></thead><tbody id="rows"></tbody></table><div class="empty" id="empty">No contracts match this view.</div></section>
<footer>Generated __GENERATED__. Estimates are informational, not guarantees or investment advice. American-style puts may be assigned before expiration; verify executable prices with your broker.</footer>
</main><script>const data=__DATA__;let view=[...data],sortKey='ranking_score',direction=-1;const f=(v,d=2)=>v==null?'—':Number(v).toFixed(d);function draw(){const q=document.querySelector('#search').value.toUpperCase(),limit=document.querySelector('#risk').value,model=document.querySelector('#model').value;view=data.filter(x=>(!q||`${x.symbol} ${x.contract}`.toUpperCase().includes(q))&&(model==='all'||x.source_model===model)&&(limit==='all'||(x.source_model==='V3'&&x.abs_delta_est!=null&&x.abs_delta_est<=Number(limit)/100))).sort((a,b)=>{let x=a[sortKey],y=b[sortKey];return(typeof x==='string'?String(x).localeCompare(String(y)):(Number(x)||0)-(Number(y)||0))*direction});document.querySelector('#rows').innerHTML=view.map(x=>`<tr><td><span class="pill ${x.source_model==='V2'?'v2':'v3'}">${x.source_model}</span></td><td><span class="ticker">${x.symbol}</span><div class="muted">${x.expiry} · ${x.dte}d</div></td><td><span class="score">${f(x.ranking_score,1)}</span><div class="meter"><i style="width:${x.ranking_score}%"></i></div></td><td>$${f(x.strike)} PUT</td><td>$${f(x.bid_credit)}</td><td>${f(x.annualized_return_pct,1)}%</td><td>${f(x.abs_delta_est,3)}</td><td>${f(x.prob_itm_est_pct,1)}%</td><td>${f(x.otm_pct,1)}%</td><td>${x.v2_relative_strength_score==null?'—':f(x.v2_relative_strength_score,0)+'/15'}</td><td>${f(x.spread_pct,1)}%</td><td>${x.quote_source||'—'}</td></tr>`).join('');document.querySelector('#empty').hidden=!!view.length;document.querySelector('#count').textContent=view.length;document.querySelector('#v2count').textContent=data.filter(x=>x.source_model==='V2').length;document.querySelector('#v3count').textContent=data.filter(x=>x.source_model==='V3').length}document.querySelectorAll('th[data-key]').forEach(th=>th.onclick=()=>{direction=sortKey===th.dataset.key?-direction:-1;sortKey=th.dataset.key;draw()});document.querySelector('#search').oninput=draw;document.querySelector('#risk').onchange=draw;document.querySelector('#model').onchange=draw;draw();</script></body></html>'''


def main() -> None:
    print("=" * 88)
    print("CSP SCREENER V4 - v2 stock quality + v3 option risk")
    print("=" * 88)
    print("V2 and V3 run independently against the same Yahoo Finance option quotes")

    spy_history = yf.Ticker("SPY").history(period="1y", auto_adjust=True)
    spy_close = spy_history["Close"] if not spy_history.empty else pd.Series(dtype=float)

    # V4 deliberately uses one quote source for both models. v3's processing
    # function accepts an IB object, but this flag routes every option quote to
    # its Yahoo adapter without opening an IBKR session.
    ib = IB()
    ib.market_data_disabled = True
    print("Quote source: Yahoo Finance for both V2 and V3")

    v2_candidates: list[dict] = []
    v3_candidates: list[dict] = []
    rejections: list[dict] = []
    component_cache: dict[str, dict] = {}
    try:
        for symbol in v3.WATCHLIST:
            ticker = yf.Ticker(symbol)
            history = ticker.history(period="1y", auto_adjust=True)
            try:
                info = ticker.info or {}
            except Exception:
                info = {}
            if len(history) >= 220:
                components = v2_stock_components(history, spy_close, info)
                component_cache[symbol] = components
                symbol_v2 = scan_v2_candidates(ticker, history, info, components)
                for item in symbol_v2:
                    item["symbol"] = symbol
                v2_candidates.extend(symbol_v2)

            found, rejected = v3.process_symbol(symbol, ib)
            rejections.extend(rejected)
            if found:
                v3_candidates.extend(prepare_v3_candidate(item) for item in found)
            print(f"{symbol:<6} V2 {len(symbol_v2) if len(history) >= 220 else 0:>3} | V3 {len(found):>3}")
    finally:
        if ib.isConnected():
            ib.disconnect()

    separate = separate_results(v2_candidates, v3_candidates)
    if separate:
        frame = pd.DataFrame(separate).sort_values(
            ["source_model", "ranking_score"], ascending=[True, False]
        )
    else:
        frame = pd.DataFrame()

    quote_sources = sorted(set(frame.get("quote_source", pd.Series(dtype=str)).dropna()))
    render_report(frame, {"quote_source": ", ".join(quote_sources)})
    if rejections:
        pd.DataFrame(rejections).to_csv(REJECTIONS_FILE, index=False, encoding="utf-8")
    if frame.empty:
        counts = Counter(item.get("reason", "unknown") for item in rejections)
        print("\nNo contracts passed all v3 risk and liquidity filters.")
        for reason, count in counts.most_common(8):
            print(f"  {count:>5}  {reason}")
        print(f"Empty-state report saved to {REPORT_FILE}")
        return

    frame.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")
    v2_frame = pd.DataFrame(v2_candidates)
    if not v2_frame.empty:
        v2_frame = v2_frame.sort_values("v2_score", ascending=False)
    v3_frame = pd.DataFrame(v3_candidates)
    if not v3_frame.empty:
        v3_frame = v3_frame.sort_values("v3_score", ascending=False)
    v2_frame.to_csv(V2_OUTPUT_FILE, index=False, encoding="utf-8")
    v3_frame.to_csv(V3_OUTPUT_FILE, index=False, encoding="utf-8")
    print("\nSEPARATE RESULT COUNTS")
    print(f"  {'V2':<8} {len(v2_candidates):>5}")
    print(f"  {'V3':<8} {len(v3_candidates):>5}")

    if not v3_candidates:
        counts = Counter(item.get("reason", "unknown") for item in rejections)
        print("\nV3 produced zero qualifying contracts. Top V3 rejection reasons:")
        for reason, count in counts.most_common(8):
            print(f"  {count:>5}  {reason}")
        print(f"V3 rejection audit saved to {REJECTIONS_FILE}")

    print("\nTOP V2 CSP RESULTS")
    v2_columns = ["symbol", "v2_score", "strike", "bid_credit",
                  "annualized_return_pct", "quote_source"]
    if v2_frame.empty:
        print("No V2 contracts passed all V2 filters.")
    else:
        print(v2_frame[v2_columns].head(20).to_string(index=False))
    print("\nTOP V3 CSP RESULTS")
    if v3_frame.empty:
        print("No V3 contracts passed all V3 filters; see the rejection audit above.")
    else:
        v3_columns = ["symbol", "v3_score", "strike", "bid_credit", "abs_delta_est",
                      "prob_itm_est_pct", "annualized_return_pct", "quote_source"]
        print(v3_frame[v3_columns].head(20).to_string(index=False))
    print(f"\nSaved CSV: {OUTPUT_FILE}")
    print(f"Saved V2 CSV: {V2_OUTPUT_FILE}")
    print(f"Saved V3 CSV: {V3_OUTPUT_FILE}")
    print(f"Saved results page: {REPORT_FILE}")


if __name__ == "__main__":
    main()
