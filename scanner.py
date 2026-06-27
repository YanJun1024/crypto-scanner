#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
加密货币小时线扫描器 - 三连涨/跌 + 布林带与EMA条件
数据源：MEXC 交易所 (使用 Min60 表示1小时)
通知方式：飞书机器人 Webhook
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime
import time
import os

# ==================== 配置区域 ====================

# 飞书机器人 Webhook 地址（从环境变量读取，安全）
FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
if not FEISHU_WEBHOOK_URL:
    print("⚠️ 警告: 未设置 FEISHU_WEBHOOK_URL 环境变量，消息将无法发送。")

# 要监控的币种列表（交易对名称，与币安一致）
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "MATICUSDT", "UNIUSDT", "ATOMUSDT", "LTCUSDT", "BCHUSDT",
    "NEARUSDT", "FILUSDT", "APTUSDT", "ARBUSDT", "OPUSDT"
]

# 技术指标参数
BB_PERIOD = 20      # 布林带周期
EMA_PERIOD = 89     # EMA周期
KLINES_LIMIT = 100  # 获取K线数量（确保足够计算89EMA）

# MEXC 的 interval 参数：Min60 表示 1 小时 K 线
INTERVAL = "Min60"

# ==================== 核心函数 ====================

def get_klines(symbol: str, interval: str = "Min60", limit: int = 100):
    """
    从 MEXC 获取 K 线数据
    MEXC interval 参数: Min1, Min5, Min15, Min30, Min60, Hour4, Hour8, Day1, Week1, Month1
    返回: DataFrame，包含 open, high, low, close, volume
    """
    url = "https://api.mexc.com/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        df = pd.DataFrame(data, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
        ])
        
        # 转换数据类型
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)
        
        # 转换时间为可读格式
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
        
        return df
        
    except requests.exceptions.RequestException as e:
        print(f"  Error: 获取 {symbol} K线数据失败: {e}")
        return None


def calculate_indicators(df: pd.DataFrame):
    """
    计算布林带中线和89EMA
    """
    close_prices = df["close"]
    
    # 布林带中线 = 20周期SMA
    bb_middle = close_prices.rolling(window=BB_PERIOD).mean()
    
    # 89EMA
    ema_89 = close_prices.ewm(span=EMA_PERIOD, adjust=False).mean()
    
    return bb_middle, ema_89


def check_three_candles(df: pd.DataFrame):
    """
    检查最近三根K线是否为三连涨或三连跌
    返回: (three_up, three_down)
    """
    if len(df) < 4:
        return False, False
    
    last_3 = df.tail(3)
    is_up = last_3["close"] > last_3["open"]
    
    three_up = is_up.all()
    three_down = (~is_up).all()
    
    return three_up, three_down


def scan_symbol(symbol: str):
    """
    扫描单个币种，返回筛选结果（若符合条件）
    """
    df = get_klines(symbol, interval=INTERVAL, limit=KLINES_LIMIT)
    if df is None or len(df) < EMA_PERIOD + 3:
        return None
    
    bb_middle, ema_89 = calculate_indicators(df)
    latest_bb_middle = bb_middle.iloc[-1]
    latest_ema_89 = ema_89.iloc[-1]
    
    three_up, three_down = check_three_candles(df)
    
    price_change = ((df["close"].iloc[-1] - df["close"].iloc[-4]) / df["close"].iloc[-4] * 100) if len(df) >= 4 else 0
    
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
    
    # 条件匹配
    if three_up and latest_bb_middle > latest_ema_89:
        result["match"] = "UP"
        return result
    if three_down and latest_bb_middle < latest_ema_89:
        result["match"] = "DOWN"
        return result
    
    result["match"] = None
    return result


def send_to_feishu(message: str):
    """
    通过飞书机器人发送消息
    """
    if not FEISHU_WEBHOOK_URL:
        print("  ⚠️ 飞书 Webhook URL 未设置，跳过发送")
        return
    
    headers = {"Content-Type": "application/json"}
    payload = {
        "msg_type": "text",
        "content": {"text": message}
    }
    
    try:
        response = requests.post(FEISHU_WEBHOOK_URL, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()
        if result.get("code") == 0:
            print("  ✅ 飞书消息发送成功")
        else:
            print(f"  ⚠️ 飞书消息发送失败: {result}")
    except Exception as e:
        print(f"  Error: 发送飞书消息失败: {e}")


def format_result_message(result: dict) -> str:
    """
    格式化结果为飞书文本
    """
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


def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始扫描...")
    
    matched = []
    for symbol in SYMBOLS:
        print(f"  扫描 {symbol}...")
        res = scan_symbol(symbol)
        if res and res["match"]:
            matched.append(res)
            print(f"  ✅ {symbol} 符合条件: {res['match']}")
    
    if matched:
        for r in matched:
            send_to_feishu(format_result_message(r))
            time.sleep(0.5)
        print(f"[INFO] 共发现 {len(matched)} 个符合条件的币种，已推送")
    else:
        heartbeat = f"🕐 扫描完成 [{datetime.now().strftime('%Y-%m-%d %H:%M')}]，共扫描 {len(SYMBOLS)} 个币种，暂未发现符合条件的标的。"
        send_to_feishu(heartbeat)
        print("[INFO] 未发现符合条件的币种，已发送心跳通知")
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 扫描完成")


if __name__ == "__main__":
    main()