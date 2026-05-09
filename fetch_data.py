#!/usr/bin/env python3
"""
HALO Dividend Strategy — Daily Data Fetcher
运行时间: 每个交易日 15:30 后
输出: data.json（供前端读取）
"""

import akshare as ak
import pandas as pd
import json
import time
from datetime import datetime, timedelta


# ─── 配置 ────────────────────────────────────────────────────────────────────

YIELD_BUY_STRONG   = 0.0400  # 强买阈值：股息率 ≥ 4%
YIELD_BUY          = 0.0375  # 买入阈值：股息率 ≥ 3.75%
YIELD_WATCH        = 0.0350  # 关注阈值：股息率 ≥ 3.5%
YIELD_TAKE_PROFIT  = 0.0300  # 止盈阈值：股息率 ≤ 3%
YIELD_SELL         = 0.0250  # 卖出阈值：股息率 ≤ 2.5%

API_DELAY = 0.8  # 每次请求间隔（秒），避免被封

# ─── 工具函数 ──────────────────────────────────────────────────────────────────

def safe_float(val, default=None):
    try:
        v = float(val)
        return None if pd.isna(v) else v
    except:
        return default


def safe_round(val, digits=2):
    try:
        return round(float(val), digits) if val is not None else None
    except:
        return None


# ─── 数据获取 ──────────────────────────────────────────────────────────────────

def fetch_spot_df():
    """拉取全A股当日行情（一次性拉全量，效率高）"""
    print("  → 获取全市场行情...")
    df = ak.stock_zh_a_spot_em()
    df = df.rename(columns={
        "代码": "code",
        "名称": "name",
        "最新价": "price",
        "涨跌幅": "change_pct",
        "市盈率-动态": "pe",
        "市净率": "pb",
        "总市值": "total_mktcap",
        "年初至今涨跌幅": "return_ytd",
        "60日涨跌幅": "return_60d",
    })
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df.set_index("code")


def fetch_daily_hist(code, years=2):
    """获取日K（用于 MA250、年涨跌幅、52W 高低）"""
    end = datetime.today().strftime("%Y%m%d")
    start = (datetime.today() - timedelta(days=365 * years + 30)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start, end_date=end, adjust="qfq"
        )
        df.columns = [c.strip() for c in df.columns]
        return df
    except Exception as e:
        print(f"    [WARN] 日K获取失败 {code}: {e}")
        return None


def fetch_weekly_hist(code, years=6):
    """获取周K（用于 MA250W，需约5年数据）"""
    end = datetime.today().strftime("%Y%m%d")
    start = (datetime.today() - timedelta(days=365 * years + 30)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist(
            symbol=code, period="weekly",
            start_date=start, end_date=end, adjust="qfq"
        )
        df.columns = [c.strip() for c in df.columns]
        return df
    except Exception as e:
        print(f"    [WARN] 周K获取失败 {code}: {e}")
        return None


def fetch_dividends(code):
    """获取分红历史（优先东财接口，失败则用巨潮备用）"""
    try:
        df = ak.stock_fhps_detail_em(symbol=code)
        if df is not None and len(df) > 0:
            df.columns = [c.strip() for c in df.columns]
            return df, "em"
    except:
        pass
    try:
        df = ak.stock_history_div_detail(symbol=code, indicator="分红")
        if df is not None and len(df) > 0:
            df.columns = [c.strip() for c in df.columns]
            return df, "cninfo"
    except:
        pass
    return None, None


# ─── 计算逻辑 ──────────────────────────────────────────────────────────────────

def calc_ttm_dps(div_df, source):
    """计算过去12个月每股累计派息（税前）"""
    if div_df is None or len(div_df) == 0:
        return 0.0
    cutoff = datetime.today() - timedelta(days=366)
    try:
        if source == "em":
            date_col = "除权除息日"
            amt_col = "每股派息(税前)"
        else:
            date_col = "除权除息日"
            amt_col = "每股派息(税前)"

        df = div_df.copy()
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col])
        df[amt_col] = pd.to_numeric(df[amt_col], errors="coerce").fillna(0)
        recent = df[df[date_col] > cutoff]
        return float(recent[amt_col].sum())
    except Exception as e:
        print(f"    [WARN] TTM计算异常: {e}")
        return 0.0


def calc_div_growth(div_df, source):
    """计算股息3年/5年CAGR"""
    if div_df is None or len(div_df) == 0:
        return None, None
    try:
        if source == "em":
            date_col = "除权除息日"
            amt_col = "每股派息(税前)"
        else:
            date_col = "除权除息日"
            amt_col = "每股派息(税前)"

        df = div_df.copy()
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df[amt_col] = pd.to_numeric(df[amt_col], errors="coerce").fillna(0)
        df = df.dropna(subset=[date_col])
        df["year"] = df[date_col].dt.year
        yearly = df.groupby("year")[amt_col].sum().sort_index()
        yearly = yearly[yearly > 0]

        cagr_3y, cagr_5y = None, None
        n = len(yearly)
        if n >= 4:
            cagr_3y = round(((yearly.iloc[-1] / yearly.iloc[-4]) ** (1 / 3) - 1) * 100, 1)
        if n >= 6:
            cagr_5y = round(((yearly.iloc[-1] / yearly.iloc[-6]) ** (1 / 5) - 1) * 100, 1)
        return cagr_3y, cagr_5y
    except:
        return None, None


def calc_signal(div_yield):
    """根据股息率判断操作信号"""
    if div_yield is None:
        return "N/A"
    if div_yield >= YIELD_BUY_STRONG * 100:
        return "强买"
    elif div_yield >= YIELD_BUY * 100:
        return "买入"
    elif div_yield >= YIELD_WATCH * 100:
        return "关注"
    elif div_yield <= YIELD_SELL * 100:
        return "卖出"
    elif div_yield <= YIELD_TAKE_PROFIT * 100:
        return "止盈"
    else:
        return "持有"


# ─── 主处理 ────────────────────────────────────────────────────────────────────

def process_stock(code, spot_df):
    result = {"code": code}

    # ── 基本行情 ──
    code_str = str(code).zfill(6)
    if code_str not in spot_df.index:
        print(f"    [WARN] {code_str} 不在行情列表中，跳过")
        return None

    spot = spot_df.loc[code_str]
    result["name"]         = str(spot.get("name", ""))
    result["price"]        = safe_round(spot.get("price"), 2)
    result["change_pct"]   = safe_round(spot.get("change_pct"), 2)
    result["pe"]           = safe_round(spot.get("pe"), 2)
    result["pb"]           = safe_round(spot.get("pb"), 2)
    result["total_mktcap"] = safe_round(safe_float(spot.get("total_mktcap"), 0) / 1e8, 1)  # 亿元
    result["return_ytd"]   = safe_round(spot.get("return_ytd"), 2)

    price = result["price"] or 0

    # ── 日K：MA250 / 年涨跌幅 / 52W高低 ──
    time.sleep(API_DELAY)
    hist_d = fetch_daily_hist(code_str, years=2)

    if hist_d is not None and "收盘" in hist_d.columns and len(hist_d) >= 60:
        closes = hist_d["收盘"].dropna()
        highs  = hist_d.get("最高", closes)
        lows   = hist_d.get("最低", closes)

        if len(closes) >= 250:
            ma250 = closes.rolling(250).mean().iloc[-1]
            result["ma250"]     = safe_round(ma250, 2)
            result["ma250_dev"] = safe_round((price / ma250 - 1) * 100, 1) if ma250 else None
        else:
            result["ma250"] = result["ma250_dev"] = None

        # 年涨跌幅（用实际252交易日前的收盘价）
        if len(closes) >= 252:
            result["return_1y"] = safe_round((price / closes.iloc[-252] - 1) * 100, 1)
        else:
            result["return_1y"] = safe_round(spot.get("return_ytd"), 2)

        tail = min(len(closes), 252)
        result["high_52w"] = safe_round(highs.tail(tail).max(), 2)
        result["low_52w"]  = safe_round(lows.tail(tail).min(), 2)
    else:
        result["ma250"] = result["ma250_dev"] = result["return_1y"] = None
        result["high_52w"] = result["low_52w"] = None

    # ── 周K：MA250W ──
    time.sleep(API_DELAY)
    hist_w = fetch_weekly_hist(code_str, years=6)

    if hist_w is not None and "收盘" in hist_w.columns and len(hist_w) >= 100:
        closes_w = hist_w["收盘"].dropna()
        if len(closes_w) >= 250:
            ma250w = closes_w.rolling(250).mean().iloc[-1]
            result["ma250w"]     = safe_round(ma250w, 2)
            result["ma250w_dev"] = safe_round((price / ma250w - 1) * 100, 1) if ma250w else None
        else:
            result["ma250w"] = result["ma250w_dev"] = None
    else:
        result["ma250w"] = result["ma250w_dev"] = None

    # ── 分红数据 ──
    time.sleep(API_DELAY)
    div_df, source = fetch_dividends(code_str)

    ttm_dps = calc_ttm_dps(div_df, source)
    result["ttm_dps"] = safe_round(ttm_dps, 4)

    if price > 0 and ttm_dps > 0:
        dy = ttm_dps / price * 100
        result["div_yield"]      = safe_round(dy, 2)
        result["signal"]         = calc_signal(dy)
        # 买卖参考价（按不同股息率阈值反算）
        result["price_at_400"]   = safe_round(ttm_dps / 0.040, 2)   # 强买 4%
        result["price_at_375"]   = safe_round(ttm_dps / 0.0375, 2)  # 买入 3.75%
        result["price_at_350"]   = safe_round(ttm_dps / 0.035, 2)   # 关注 3.5%
        result["price_at_300"]   = safe_round(ttm_dps / 0.030, 2)   # 止盈 3%
        result["price_at_250"]   = safe_round(ttm_dps / 0.025, 2)   # 卖出 2.5%
    else:
        result["div_yield"] = result["signal"] = None
        result["price_at_400"] = result["price_at_375"] = result["price_at_350"] = None
        result["price_at_300"] = result["price_at_250"] = None

    # 最近一次除息信息
    if div_df is not None and len(div_df) > 0:
        try:
            date_col = "除权除息日"
            amt_col  = "每股派息(税前)"
            df_sorted = div_df.copy()
            df_sorted[date_col] = pd.to_datetime(df_sorted[date_col], errors="coerce")
            df_sorted = df_sorted.dropna(subset=[date_col]).sort_values(date_col, ascending=False)
            last = df_sorted.iloc[0]
            result["last_ex_div_date"]   = str(last[date_col].date())
            result["last_div_amount"]    = safe_round(last[amt_col], 4)

            # 距上次除息天数
            days_ago = (datetime.today() - last[date_col]).days
            result["days_since_ex_div"]  = int(days_ago)
        except:
            result["last_ex_div_date"] = result["last_div_amount"] = result["days_since_ex_div"] = None
    else:
        result["last_ex_div_date"] = result["last_div_amount"] = result["days_since_ex_div"] = None

    # 股息成长性
    cagr_3y, cagr_5y = calc_div_growth(div_df, source)
    result["div_cagr_3y"] = cagr_3y
    result["div_cagr_5y"] = cagr_5y

    return result


# ─── 入口 ──────────────────────────────────────────────────────────────────────

def main():
    with open("stocks.json", "r", encoding="utf-8") as f:
        stock_list = json.load(f)

    print(f"[HALO Fetcher] 共 {len(stock_list)} 只标的，开始拉取...")
    spot_df = fetch_spot_df()
    results = []

    for i, code in enumerate(stock_list):
        code = str(code).zfill(6)
        print(f"\n[{i+1}/{len(stock_list)}] {code}")
        try:
            data = process_stock(code, spot_df)
            if data:
                results.append(data)
                print(f"    ✓ {data['name']} | 现价={data['price']} | 股息率={data.get('div_yield')}% | 信号={data.get('signal')}")
        except Exception as e:
            print(f"    ✗ 处理失败: {e}")

    output = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "count": len(results),
        "thresholds": {
            "strong_buy": YIELD_BUY_STRONG * 100,
            "buy":        YIELD_BUY * 100,
            "watch":      YIELD_WATCH * 100,
            "take_profit": YIELD_TAKE_PROFIT * 100,
            "sell":       YIELD_SELL * 100,
        },
        "stocks": results,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n✅ 完成！{len(results)} 只股票写入 data.json")


if __name__ == "__main__":
    main()
