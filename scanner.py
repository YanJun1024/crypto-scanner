#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
加密货币小时线扫描器 - 三连涨/跌 + 布林带与EMA条件 + 累计幅度>2%过滤
数据源：币安备用域名 (data-api.binance.vision)，自动获取市值前200的USDT交易对
通知方式：企业微信机器人（支持批量合并推送）
优化：只使用已收盘的K线，避免未完成K线导致的信号闪烁
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime
import time
import os
import sys
import math

# ==================== 配置 ====================

WECHAT_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
if not WECHAT_WEBHOOK_URL:
    print("⚠️ 未设置 FEISHU_WEBHOOK_URL 环境变量", flush=True)

BB_PERIOD = 20
EMA_PERIOD = 89
KLINES_LIMIT = 100          # 获取100根，丢弃最后一根后剩99根，足够计算
REQUEST_DELAY = 0.3
TOP_N = 200                  # ← 改为前200名
MIN_TOTAL_CHANGE = 2.0

BINANCE_API_BASE = "https://data-api.binance.vision"
MEXC_API_BASE = "https://api.mexc.com"

# ==================== 获取币种列表 ====================

def get_top_usdt_pairs(limit: int = 200):   # ← 默认参数改为200
    urls = [
        f"{BINANCE_API_BASE}/api/v3/ticker/24hr",
        f"{MEXC_API_BASE}/api/v3/ticker/24hr"
    ]
    for url in urls:
        try:
            print(f"[INFO] 尝试从 {url} 获取交易对列表...", flush=True)
            response = requests.get(url, timeout=30)
            if response.status_code != 200:
                print(f"  ⚠️ 状态码 {response.status_code}，跳过", flush=True)
                continue
            data = response.json()
            if not data:
                continue
            usdt_pairs = []
            for item in data:
                symbol = item.get("symbol", "")
                if symbol.endswith("USDT") and not any(x in symbol for x in ["UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"]):
                    try:
                        last_price = float(item.get("lastPrice", 0))
                        volume = float(item.get("volume", 0))
                        weight = last_price * volume
                        usdt_pairs.append({
                            "symbol": symbol,
                            "weight": weight,
                            "price": last_price,
                            "volume": volume
                        })
                    except (ValueError, TypeError):
                        continue
            if not usdt_pairs:
                continue
            usdt_pairs.sort(key=lambda x: x["weight"], reverse=True)
            top = [p["symbol"] for p in usdt_pairs[:limit]]
            print(f"[INFO] 成功从 {url} 获取 {len(top)} 个交易对", flush=True)
            return top
        except Exception as e:
            print(f"  Error: {e}", flush=True)
            continue

    print("[WARN] 所有数据源均失败，使用硬编码主流币种列表", flush=True)
    return [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
        "MATICUSDT", "LTCUSDT", "NEARUSDT", "ATOMUSDT", "ALGOUSDT",
        "FILUSDT", "VETUSDT", "XTZUSDT", "EOSUSDT", "XMRUSDT"
    ]

# ==================== 获取K线 ====================

def get_klines(symbol: str, interval: str = "1h", limit: int = 100):
    url = f"{BINANCE_API_BASE}/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code != 200:
            return None
        data = response.json()
        if not data:
            return None
        df = pd.DataFrame(data, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore"
        ])
        df = df[["timestamp", "open", "high", "low", "close"]]
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df
    except Exception:
        return None

# ==================== 指标计算 ====================

def calculate_indicators(df: pd.DataFrame):
    close_prices = df["close"]
    bb_middle = close_prices.rolling(window=BB_PERIOD).mean()
    ema_89 = close_prices.ewm(span=EMA_PERIOD, adjust=False).mean()
    return bb_middle, ema_89

def check_three_candles(df: pd.DataFrame):
    if len(df) < 4:
        return False, False, 0.0
    last_3 = df.tail(3)
    is_up = last_3["close"] > last_3["open"]
    three_up = is_up.all()
    three_down = (~is_up).all()
    open_first = last_3.iloc[0]["open"]
    close_last = last_3.iloc[-1]["close"]
    total_change = (close_last - open_first) / open_first * 100
    return three_up, three_down, total_change

# ==================== 扫描单个币种 ====================

def scan_symbol(symbol: str):
    df = get_klines(symbol, limit=KLINES_LIMIT)
    if df is None:
        return None
    
    # 🟢 丢弃当前未收盘的K线（最后一行），避免信号闪烁
    df = df.iloc[:-1]   # 去掉最后一行
    
    # 丢弃后需要确保数据足够计算
    if len(df) < EMA_PERIOD + 3:
        return None
    
    bb_middle, ema_89 = calculate_indicators(df)
    latest_bb_middle = bb_middle.iloc[-1]
    latest_ema_89 = ema_89.iloc[-1]
    three_up, three_down, total_change = check_three_candles(df)
    
    if len(df) >= 4:
        price_change = ((df["close"].iloc[-1] - df["close"].iloc[-4]) / df["close"].iloc[-4] * 100)
    else:
        price_change = 0.0
    
    is_valid = False
    match_type = None
    if three_up and latest_bb_middle > latest_ema_89 and total_change > MIN_TOTAL_CHANGE:
        is_valid = True
        match_type = "UP"
    elif three_down and latest_bb_middle < latest_ema_89 and total_change < -MIN_TOTAL_CHANGE:
        is_valid = True
        match_type = "DOWN"
    
    if not is_valid:
        return None
    
    result = {
        "symbol": symbol,
        "current_price": df["close"].iloc[-1],
        "bb_middle": latest_bb_middle,
        "ema_89": latest_ema_89,
        "bb_above_ema": latest_bb_middle > latest_ema_89,
        "three_up": three_up,
        "three_down": three_down,
        "price_change_3h": price_change,
        "total_change": total_change,
        "datetime": df["datetime"].iloc[-1],
        "match": match_type
    }
    return result

# ==================== 企业微信推送（支持批量） ====================

def send_to_wechat(message: str):
    if not WECHAT_WEBHOOK_URL:
        print("  ⚠️ 企业微信 Webhook URL 未设置", flush=True)
        return False
    headers = {"Content-Type": "application/json"}
    payload = {
        "msgtype": "text",
        "text": {"content": message}
    }
    try:
        response = requests.post(WECHAT_WEBHOOK_URL, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()
        if result.get("errcode") == 0:
            print("  ✅ 企业微信消息发送成功", flush=True)
            return True
        else:
            print(f"  ⚠️ 企业微信消息发送失败: {result}", flush=True)
            return False
    except Exception as e:
        print(f"  Error: 发送企业微信消息失败: {e}", flush=True)
        return False

def send_batch_results(results: list, batch_size: int = 10):
    if not results:
        return
    total = len(results)
    batches = math.ceil(total / batch_size)
    for i in range(batches):
        start = i * batch_size
        end = min(start + batch_size, total)
        batch = results[start:end]
        lines = []
        for r in batch:
            symbol = r["symbol"]
            price = r["current_price"]
            change = r["total_change"]
            match_type = "📈" if r["match"] == "UP" else "📉"
            dt = r["datetime"].strftime("%H:%M")
            sign = "+" if change > 0 else ""
            lines.append(f"{match_type} {symbol} ${price:.4f} ({sign}{change:.2f}%) @{dt}")
        header = f"🔔 发现 {len(batch)} 个信号 (共 {total} 个)\n"
        body = "\n".join(lines)
        message = header + body
        print(f"  [批次 {i+1}/{batches}] 发送 {len(batch)} 个信号...", flush=True)
        success = send_to_wechat(message)
        if not success:
            print(f"  ⚠️ 批次 {i+1} 发送失败，跳过剩余批次", flush=True)
            break
        if i < batches - 1:
            time.sleep(2)

# ==================== 主函数 ====================

def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始扫描...", flush=True)
    
    symbols = get_top_usdt_pairs(limit=TOP_N)
    total = len(symbols)
    print(f"[INFO] 共 {total} 个币种待扫描", flush=True)
    
    matched = []
    for idx, symbol in enumerate(symbols, 1):
        print(f"  [{idx}/{total}] 扫描 {symbol}...", flush=True)
        res = scan_symbol(symbol)
        if res:
            matched.append(res)
            print(f"  ✅ {symbol} 符合条件 (累计幅度 {res['total_change']:.2f}%)", flush=True)
        time.sleep(REQUEST_DELAY)
    
    if matched:
        print(f"[INFO] 共发现 {len(matched)} 个符合条件的币种，开始分批推送...", flush=True)
        send_batch_results(matched, batch_size=10)
    else:
        heartbeat = f"🕐 扫描完成 [{datetime.now().strftime('%Y-%m-%d %H:%M')}]，扫描 {total} 个币种，未发现符合条件的标的。"
        send_to_wechat(heartbeat)
        print("[INFO] 未发现符合条件的币种，已发送心跳通知", flush=True)
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 扫描完成", flush=True)

if __name__ == "__main__":
    print("脚本入口点触发", flush=True)
    main()