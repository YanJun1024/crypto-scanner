#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
加密货币小时线扫描器 - 三连涨/跌 + 布林带与EMA条件
数据源：币安备用域名 (data-api.binance.vision)，自动获取市值前300的USDT交易对
通知方式：企业微信机器人
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime
import time
import os
import sys

# ==================== 配置 ====================

WECHAT_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
if not WECHAT_WEBHOOK_URL:
    print("⚠️ 未设置 FEISHU_WEBHOOK_URL 环境变量", flush=True)

# 技术指标参数
BB_PERIOD = 20
EMA_PERIOD = 89
KLINES_LIMIT = 100
REQUEST_DELAY = 0.3         # 币安备用域名限流宽松，0.3秒/个，300个约90秒
TOP_N = 300

# API 备用域名
BINANCE_API_BASE = "https://data-api.binance.vision"
MEXC_API_BASE = "https://api.mexc.com"   # 备用

# ==================== 动态获取币种列表（支持多数据源） ====================

def get_top_usdt_pairs(limit: int = 300):
    """
    尝试从币安备用域名获取 USDT 交易对列表，失败则尝试 MEXC，再失败则返回固定列表
    """
    # 候选 API 地址
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
    
    # 所有数据源均失败，返回降级列表
    print("[WARN] 所有数据源均失败，使用硬编码主流币种列表", flush=True)
    return [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
        "MATICUSDT", "LTCUSDT", "NEARUSDT", "ATOMUSDT", "ALGOUSDT",
        "FILUSDT", "VETUSDT", "XTZUSDT", "EOSUSDT", "XMRUSDT"
    ]

# ==================== K线数据获取（使用备用域名） ====================

def get_klines(symbol: str, interval: str = "1h", limit: int = 100):
    """
    从币安备用域名获取 K 线数据
    """
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
    except Exception as e:
        return None

# ==================== 指标计算 ====================

def calculate_indicators(df: pd.DataFrame):
    close_prices = df["close"]
    bb_middle = close_prices.rolling(window=BB_PERIOD).mean()
    ema_89 = close_prices.ewm(span=EMA_PERIOD, adjust=False).mean()
    return bb_middle, ema_89

def check_three_candles(df: pd.DataFrame):
    if len(df) < 4:
        return False, False
    last_3 = df.tail(3)
    is_up = last_3["close"] > last_3["open"]
    three_up = is_up.all()
    three_down = (~is_up).all()
    return three_up, three_down

# ==================== 扫描单个币种 ====================

def scan_symbol(symbol: str):
    df = get_klines(symbol, limit=KLINES_LIMIT)
    if df is None or len(df) < EMA_PERIOD + 3:
        return None
    
    bb_middle, ema_89 = calculate_indicators(df)
    latest_bb_middle = bb_middle.iloc[-1]
    latest_ema_89 = ema_89.iloc[-1]
    three_up, three_down = check_three_candles(df)
    
    if len(df) >= 4:
        price_change = ((df["close"].iloc[-1] - df["close"].iloc[-4]) / df["close"].iloc[-4] * 100)
    else:
        price_change = 0.0
    
    result = {
        "symbol": symbol,
        "current_price": df["close"].iloc[-1],
        "bb_middle": latest_bb_middle,
        "ema_89": latest_ema_89,
        "bb_above_ema": latest_bb_middle > latest_ema_89,
        "three_up": three_up,
        "three_down": three_down,
        "price_change_3h": price_change,
        "datetime": df["datetime"].iloc[-1]
    }
    
    if three_up and latest_bb_middle > latest_ema_89:
        result["match"] = "UP"
        return result
    if three_down and latest_bb_middle < latest_ema_89:
        result["match"] = "DOWN"
        return result
    result["match"] = None
    return result

# ==================== 企业微信推送 ====================

def send_to_wechat(message: str):
    if not WECHAT_WEBHOOK_URL:
        print("  ⚠️ 企业微信 Webhook URL 未设置", flush=True)
        return
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
        else:
            print(f"  ⚠️ 企业微信消息发送失败: {result}", flush=True)
    except Exception as e:
        print(f"  Error: 发送企业微信消息失败: {e}", flush=True)

def format_result_message(result: dict) -> str:
    symbol = result["symbol"]
    price = result["current_price"]
    bb = result["bb_middle"]
    ema = result["ema_89"]
    change = result["price_change_3h"]
    match_type = "📈 三连涨" if result["match"] == "UP" else "📉 三连跌"
    dt = result["datetime"].strftime("%Y-%m-%d %H:%M")
    return f"""🔔 【{symbol}】{match_type}

⏰ 时间: {dt}
💰 当前价格: ${price:.4f}
📊 布林带中线: ${bb:.4f}
📈 89EMA: ${ema:.4f}
📉 3小时涨跌幅: {change:+.2f}%

条件: 布林带中线 {'>' if result['bb_above_ema'] else '<'} 89EMA ✅"""

# ==================== 主函数 ====================

def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始扫描...", flush=True)
    
    # 第一步：动态获取交易对列表
    symbols = get_top_usdt_pairs(limit=TOP_N)
    total = len(symbols)
    print(f"[INFO] 共 {total} 个币种待扫描", flush=True)
    
    matched = []
    for idx, symbol in enumerate(symbols, 1):
        print(f"  [{idx}/{total}] 扫描 {symbol}...", flush=True)
        res = scan_symbol(symbol)
        if res and res["match"]:
            matched.append(res)
            print(f"  ✅ {symbol} 符合条件: {res['match']}", flush=True)
        time.sleep(REQUEST_DELAY)
    
    if matched:
        for r in matched:
            send_to_wechat(format_result_message(r))
            time.sleep(0.5)
        print(f"[INFO] 发现 {len(matched)} 个符合条件的币种，已推送", flush=True)
    else:
        heartbeat = f"🕐 扫描完成 [{datetime.now().strftime('%Y-%m-%d %H:%M')}]，扫描 {total} 个币种，未发现符合条件的标的。"
        send_to_wechat(heartbeat)
        print("[INFO] 未发现符合条件的币种，已发送心跳通知", flush=True)
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 扫描完成", flush=True)

if __name__ == "__main__":
    print("脚本入口点触发", flush=True)
    main()