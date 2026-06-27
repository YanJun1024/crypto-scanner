#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
加密货币小时线扫描器 - 三连涨/跌 + 布林带与EMA条件
数据源：CoinGecko OHLC 接口（带频率控制和重试）
通知方式：企业微信机器人
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime
import time
import os

# ==================== 配置 ====================

WECHAT_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
if not WECHAT_WEBHOOK_URL:
    print("⚠️ 未设置 FEISHU_WEBHOOK_URL 环境变量")

# 监控币种（先测试前几个，避免频率超限）
SYMBOLS = {
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "binancecoin": "BNBUSDT",
    "solana": "SOLUSDT",
    "ripple": "XRPUSDT",
    # "cardano": "ADAUSDT",     # 待稳定后可逐步添加
    # "dogecoin": "DOGEUSDT",
    # "avalanche-2": "AVAXUSDT",
    # "polkadot": "DOTUSDT",
    # "chainlink": "LINKUSDT",
}

BB_PERIOD = 20
EMA_PERIOD = 89
OHLC_DAYS = 7
REQUEST_DELAY = 1.5  # 每个请求间隔（秒），防止 429

# ==================== 数据获取（带重试） ====================

def get_ohlc_with_retry(coin_id: str, vs_currency: str = "usd", days: int = 7, max_retries: int = 3):
    """
    带重试机制的 OHLC 获取，遇到 429 自动等待后重试
    """
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
    params = {
        "vs_currency": vs_currency,
        "days": days,
        "precision": "full"
    }
    
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=15)
            if response.status_code == 429:
                wait = (attempt + 1) * 2  # 2s, 4s, 6s
                print(f"  ⏳ 触发 429 限制，等待 {wait}s 后重试 ({attempt+1}/{max_retries})...")
                time.sleep(wait)
                continue
            response.raise_for_status()
            data = response.json()
            if not data:
                print(f"  ⚠️ {coin_id} 返回空数据")
                return None
            # 转为 DataFrame
            df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close"])
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
            for col in ["open", "high", "low", "close"]:
                df[col] = df[col].astype(float)
            return df
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                print(f"  Error: 获取 {coin_id} 失败，已达最大重试次数: {e}")
                return None
            time.sleep(1)
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

def scan_symbol(coin_id: str, display_name: str):
    df = get_ohlc_with_retry(coin_id, days=OHLC_DAYS)
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
        "symbol": display_name,
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
        print("  ⚠️ 企业微信 Webhook URL 未设置")
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
            print("  ✅ 企业微信消息发送成功")
        else:
            print(f"  ⚠️ 企业微信消息发送失败: {result}")
    except Exception as e:
        print(f"  Error: 发送企业微信消息失败: {e}")

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
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始扫描...")
    matched = []
    for coin_id, display_name in SYMBOLS.items():
        print(f"  扫描 {display_name}...")
        res = scan_symbol(coin_id, display_name)
        if res and res["match"]:
            matched.append(res)
            print(f"  ✅ {display_name} 符合条件: {res['match']}")
        time.sleep(REQUEST_DELAY)  # 避免频率限制
    
    if matched:
        for r in matched:
            send_to_wechat(format_result_message(r))
            time.sleep(0.5)
        print(f"[INFO] 发现 {len(matched)} 个符合条件的币种，已推送")
    else:
        heartbeat = f"🕐 扫描完成 [{datetime.now().strftime('%Y-%m-%d %H:%M')}]，扫描 {len(SYMBOLS)} 个币种，未发现符合条件的标的。"
        send_to_wechat(heartbeat)
        print("[INFO] 未发现符合条件的币种，已发送心跳通知")
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 扫描完成")

if __name__ == "__main__":
    main()