#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
加密货币小时线扫描器 - 三连涨/跌 + 布林带与EMA条件
数据源：币安交易所 (Binance)
通知方式：飞书机器人 Webhook
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime
import time
import json

# ==================== 配置区域 ====================

# 飞书机器人 Webhook 地址（替换成你自己的）
import os

FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")

# 要监控的币种列表（交易对名称，币安标准格式）
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "MATICUSDT", "UNIUSDT", "ATOMUSDT", "LTCUSDT", "BCHUSDT",
    "NEARUSDT", "FILUSDT", "APTUSDT", "ARBUSDT", "OPUSDT"
]

# 技术指标参数
BB_PERIOD = 20      # 布林带周期
BB_STD = 2          # 布林带标准差倍数
EMA_PERIOD = 89     # EMA周期
KLINES_LIMIT = 100  # 获取K线数量（确保足够计算89EMA）

# ==================== 核心函数 ====================

def get_klines(symbol: str, interval: str = "1h", limit: int = 100):
    """
    从币安获取K线数据
    返回: DataFrame，包含 open, high, low, close, volume
    """
    url = "https://api.binance.com/api/v3/klines"
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
        print(f"[ERROR] 获取 {symbol} K线数据失败: {e}")
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
    返回: ('up', True/False), ('down', True/False)
    """
    if len(df) < 4:
        return False, False
    
    # 取最近3根K线（索引 -3, -2, -1）
    last_3 = df.tail(3)
    
    # 判断每根是否为阳线（收盘 > 开盘）
    is_up = last_3["close"] > last_3["open"]
    
    # 三连涨：所有3根都是阳线
    three_up = is_up.all()
    
    # 三连跌：所有3根都是阴线（收盘 < 开盘）
    three_down = (~is_up).all()
    
    return three_up, three_down


def scan_symbol(symbol: str):
    """
    扫描单个币种，返回筛选结果
    """
    # 获取K线数据
    df = get_klines(symbol, limit=KLINES_LIMIT)
    if df is None or len(df) < EMA_PERIOD + 3:
        return None
    
    # 计算指标
    bb_middle, ema_89 = calculate_indicators(df)
    
    # 获取最新值
    latest_bb_middle = bb_middle.iloc[-1]
    latest_ema_89 = ema_89.iloc[-1]
    
    # 检查三连涨/跌
    three_up, three_down = check_three_candles(df)
    
    # 获取最近三根K线的详细信息（用于报告）
    last_3 = df.tail(3)
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
        "last_3_opens": last_3["open"].tolist(),
        "last_3_closes": last_3["close"].tolist(),
        "datetime": df["datetime"].iloc[-1]
    }
    
    # 判断是否符合筛选条件
    # 条件1：三连涨 + 布林带中线 > 89EMA
    if three_up and latest_bb_middle > latest_ema_89:
        result["match"] = "UP"
        return result
    
    # 条件2：三连跌 + 布林带中线 < 89EMA
    if three_down and latest_bb_middle < latest_ema_89:
        result["match"] = "DOWN"
        return result
    
    result["match"] = None
    return result


def send_to_feishu(message: str):
    """
    通过飞书机器人发送消息
    """
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
            print("[INFO] 飞书消息发送成功")
        else:
            print(f"[WARN] 飞书消息发送失败: {result}")
        return result
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] 发送飞书消息失败: {e}")
        return None


def format_result_message(result: dict) -> str:
    """
    格式化扫描结果为飞书消息文本
    """
    symbol = result["symbol"]
    price = result["current_price"]
    bb = result["bb_middle"]
    ema = result["ema_89"]
    change = result["price_change_3h"]
    match_type = "📈 三连涨" if result["match"] == "UP" else "📉 三连跌"
    dt = result["datetime"].strftime("%Y-%m-%d %H:%M")
    
    message = f"""🔔 【{symbol}】{match_type}

⏰ 时间: {dt}
💰 当前价格: ${price:.4f}
📊 布林带中线: ${bb:.4f}
📈 89EMA: ${ema:.4f}
📉 3小时涨跌幅: {change:+.2f}%

条件: 布林带中线 {'>' if result['bb_above_ema'] else '<'} 89EMA ✅
"""
    return message


def main():
    """
    主函数：扫描所有币种，发现符合条件的立即推送
    """
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始扫描...")
    
    matched_results = []
    
    for symbol in SYMBOLS:
        print(f"  扫描 {symbol}...")
        result = scan_symbol(symbol)
        
        if result and result["match"]:
            matched_results.append(result)
            print(f"  ✅ {symbol} 符合条件: {result['match']}")
    
    # 发送汇总报告
    if matched_results:
        # 逐条发送
        for r in matched_results:
            msg = format_result_message(r)
            send_to_feishu(msg)
            time.sleep(0.5)  # 避免频率限制
        
        print(f"[INFO] 共发现 {len(matched_results)} 个符合条件的币种，已推送")
    else:
        print("[INFO] 未发现符合条件的币种")
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 扫描完成")


# ==================== 入口 ====================

if __name__ == "__main__":
    main()