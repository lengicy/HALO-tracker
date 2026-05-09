#!/usr/bin/env python3
"""
HALO Dividend Strategy — Daily Data Fetcher (baostock 版)
数据源: baostock（专用金融 API，无 IP 限制，GitHub Actions 可用）
"""

import baostock as bs
import pandas as pd
import json
import time
from datetime import datetime, timedelta


# ─── 信号阈值 ──────────────────────────────────────────────────────────────────
YIELD_STRONG_BUY  = 4.00
YIELD_BUY         = 3.75
YIELD_WATCH       = 3.50
YIELD_TAKE_PROFIT = 3.00
YIELD_SELL        = 2.50


def to_bs_code(code):
    code = str(code).zfill(6)
    prefix = "sh" if code.startswith("6") else "sz"
    return f"{prefix}.{code}"


def safe_float(val, default=None):
    try:
        v = float(val)
        return None if (pd.isna(v) or v == 0) else v
    except:
        return default


def safe_round(val, d=2):
    try:
        return round(float(val), d) if val is not None else None
    except:
        return None


def fetch_latest(bs_code):
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=10)).strftime("%Y-%m-%d")
    rs = bs.query_history_k_data_plus(
        bs_code,
        "date,close,high,low,volume,turn,peTTM,pbMRQ",
        start_date=start, end_date=end,
        frequency="d", adjustflag="1"
    )
    df = rs.get_data()
    return df.iloc[-1] if (df is not None and not df.empty) else None


def fetch_daily_hist(bs_code, days=420):
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    rs = bs.query_history_k_data_plus(
        bs_code, "date,close,high,low",
        start_date=start, end_date=end,
        frequency="d", adjustflag="1"
    )
    df = rs.get_data()
    if df is None or df.empty:
        return None
    for c in ["close", "high", "low"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["close"])


def fetch_weekly_hist(bs_code, years=6):
    end   = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=365 * years)).strftime("%Y-%m-%d")
    rs = bs.query_history_k_data_plus(
        bs_code, "date,close",
        start_date=start, end_date=end,
        frequency="w", adjustflag="1"
    )
    df = rs.get_data()
    if df is None or df.empty:
        return None
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df.dropna(subset=["close"])


def fetch_dividends(bs_code, lookback_years=6):
    frames = []
    current_year = datetime.today().year
    for year in range(current_year - lookback_years, current_year + 1):
        try:
            rs = bs.query_dividend_data(code=bs_code, year=str(year), yearType="report")
            df = rs.get_data()
            if df is not None and not df.empty:
                frames.append(df)
        except:
            pass
        time.sleep(0.1)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def fetch_stock_name(bs_code):
    try:
        rs = bs.query_stock_basic(code=bs_code)
        df = rs.get_data()
        if df is not None and not df.empty:
            return df.iloc[0].get("code_name", "")
    except:
        pass
    return ""


def calc_ttm_dps(div_df):
    if div_df is None or div_df.empty:
        return 0.0
    cutoff = datetime.today() - timedelta(days=366)
    try:
        df = div_df.copy()
        df["dividObjectDate"] = pd.to_datetime(df["dividObjectDate"], errors="coerce")
        df["dividCashPsBeforeTax"] = pd.to_numeric(df["dividCashPsBeforeTax"], errors="coerce").fillna(0)
        recent = df[df["dividObjectDate"] > cutoff]
        return float(recent["dividCashPsBeforeTax"].sum())
    except Exception as e:
        print(f"    [WARN] TTM: {e}")
        return 0.0


def calc_div_growth(div_df):
    if div_df is None or div_df.empty:
        return None, None
    try:
        df = div_df.copy()
        df["dividObjectDate"] = pd.to_datetime(df["dividObjectDate"], errors="coerce")
        df["dividCashPsBeforeTax"] = pd.to_numeric(df["dividCashPsBeforeTax"], errors="coerce").fillna(0)
        df = df.dropna(subset=["dividObjectDate"])
        df["year"] = df["dividObjectDate"].dt.year
        yearly = df.groupby("year")["dividCashPsBeforeTax"].sum().sort_index()
        yearly = yearly[yearly > 0]
        n = len(yearly)
        cagr_3y = safe_round(((yearly.iloc[-1] / yearly.iloc[-4]) ** (1/3) - 1) * 100, 1) if n >= 4 else None
        cagr_5y = safe_round(((yearly.iloc[-1] / yearly.iloc[-6]) ** (1/5) - 1) * 100, 1) if n >= 6 else None
        return cagr_3y, cagr_5y
    except:
        return None, None


def calc_signal(dy):
    if dy is None: return "N/A"
    if dy >= YIELD_STRONG_BUY:  return "强买"
    if dy >= YIELD_BUY:         return "买入"
    if dy >= YIELD_WATCH:       return "关注"
    if dy <= YIELD_SELL:        return "卖出"
    if dy <= YIELD_TAKE_PROFIT: return "止盈"
    return "持有"


def process_stock(code):
    bs_code = to_bs_code(code)
    result  = {"code": str(code).zfill(6)}
    print(f"  → {bs_code}")

    result["name"] = fetch_stock_name(bs_code)
    time.sleep(0.3)

    latest = fetch_latest(bs_code)
    if latest is None:
        print(f"    [WARN] 无行情数据，跳过")
        return None

    price = safe_float(latest.get("close"))
    result["price"] = safe_round(price, 2)
    result["pe"]    = safe_round(safe_float(latest.get("peTTM")), 2)
    result["pb"]    = safe_round(safe_float(latest.get("pbMRQ")), 2)
    result["total_mktcap"] = None

    time.sleep(0.3)
    hist_d = fetch_daily_hist(bs_code, days=420)

    if hist_d is not None and len(hist_d) >= 60:
        closes = hist_d["close"]
        highs  = hist_d["high"]
        lows   = hist_d["low"]

        if len(closes) >= 250:
            ma250 = closes.rolling(250).mean().iloc[-1]
            result["ma250"]     = safe_round(ma250, 2)
            result["ma250_dev"] = safe_round((price / ma250 - 1) * 100, 1) if ma250 and price else None
        else:
            result["ma250"] = result["ma250_dev"] = None

        result["return_1y"]  = safe_round((price / closes.iloc[-245] - 1) * 100, 1) if len(closes) >= 245 else None
        result["return_ytd"] = safe_round((price / closes.iloc[0] - 1) * 100, 1)
        tail = min(len(closes), 252)
        result["high_52w"] = safe_round(highs.tail(tail).max(), 2)
        result["low_52w"]  = safe_round(lows.tail(tail).min(), 2)

        if len(closes) >= 2:
            prev = float(closes.iloc[-2])
            cur  = float(closes.iloc[-1])
            result["change_pct"] = safe_round((cur / prev - 1) * 100, 2) if prev else None
        else:
            result["change_pct"] = None
    else:
        result["ma250"] = result["ma250_dev"] = result["return_1y"] = None
        result["return_ytd"] = result["high_52w"] = result["low_52w"] = None
        result["change_pct"] = None

    time.sleep(0.3)
    hist_w = fetch_weekly_hist(bs_code, years=6)
    if hist_w is not None and len(hist_w) >= 100:
        closes_w = hist_w["close"]
        if len(closes_w) >= 250:
            ma250w = closes_w.rolling(250).mean().iloc[-1]
            result["ma250w"]     = safe_round(ma250w, 2)
            result["ma250w_dev"] = safe_round((price / ma250w - 1) * 100, 1) if ma250w and price else None
        else:
            result["ma250w"] = result["ma250w_dev"] = None
    else:
        result["ma250w"] = result["ma250w_dev"] = None

    time.sleep(0.3)
    div_df  = fetch_dividends(bs_code, lookback_years=6)
    ttm_dps = calc_ttm_dps(div_df)
    result["ttm_dps"] = safe_round(ttm_dps, 4)

    if price and price > 0 and ttm_dps > 0:
        dy = ttm_dps / price * 100
        result["div_yield"]    = safe_round(dy, 2)
        result["signal"]       = calc_signal(dy)
        result["price_at_400"] = safe_round(ttm_dps / 0.040,  2)
        result["price_at_375"] = safe_round(ttm_dps / 0.0375, 2)
        result["price_at_350"] = safe_round(ttm_dps / 0.035,  2)
        result["price_at_300"] = safe_round(ttm_dps / 0.030,  2)
        result["price_at_250"] = safe_round(ttm_dps / 0.025,  2)
    else:
        result["div_yield"] = result["signal"] = None
        result["price_at_400"] = result["price_at_375"] = result["price_at_350"] = None
        result["price_at_300"] = result["price_at_250"] = None

    if not div_df.empty:
        try:
            df_s = div_df.copy()
            df_s["dividObjectDate"] = pd.to_datetime(df_s["dividObjectDate"], errors="coerce")
            df_s = df_s.dropna(subset=["dividObjectDate"]).sort_values("dividObjectDate", ascending=False)
            last = df_s.iloc[0]
            result["last_ex_div_date"]  = str(last["dividObjectDate"].date())
            result["last_div_amount"]   = safe_round(pd.to_numeric(last.get("dividCashPsBeforeTax"), errors="coerce"), 4)
            result["days_since_ex_div"] = (datetime.today() - last["dividObjectDate"]).days
        except:
            result["last_ex_div_date"] = result["last_div_amount"] = result["days_since_ex_div"] = None
    else:
        result["last_ex_div_date"] = result["last_div_amount"] = result["days_since_ex_div"] = None

    cagr_3y, cagr_5y = calc_div_growth(div_df)
    result["div_cagr_3y"] = cagr_3y
    result["div_cagr_5y"] = cagr_5y

    return result


def main():
    with open("stocks.json", "r", encoding="utf-8") as f:
        stock_list = json.load(f)

    print("[HALO] 登录 baostock…")
    lg = bs.login()
    if lg.error_code != "0":
        print(f"登录失败: {lg.error_msg}")
        return

    print(f"[HALO] 共 {len(stock_list)} 只标的，开始拉取…\n")
    results = []

    for i, code in enumerate(stock_list):
        code = str(code).zfill(6)
        print(f"[{i+1}/{len(stock_list)}] {code}")
        try:
            data = process_stock(code)
            if data:
                results.append(data)
                print(f"    ✓ {data.get('name','')} | 现价={data['price']} | 股息率={data.get('div_yield')}% | 信号={data.get('signal')}")
        except Exception as e:
            print(f"    ✗ 异常: {e}")

    bs.logout()

    output = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "count": len(results),
        "thresholds": {
            "strong_buy": YIELD_STRONG_BUY, "buy": YIELD_BUY,
            "watch": YIELD_WATCH, "take_profit": YIELD_TAKE_PROFIT, "sell": YIELD_SELL,
        },
        "stocks": results,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n✅ 完成！{len(results)} 只股票写入 data.json")


if __name__ == "__main__":
    main()
