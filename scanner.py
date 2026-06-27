#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
加密货币小时线扫描器 - 三连涨/跌 + 布林带与EMA条件
数据源：CoinGecko (OHLC 接口，无需 API Key)
通知方式：企业微信机器人 Webhook
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime
import time
import os

# ==================== 配置区域 ====================

# 企业微信机器人 Webhook 地址（从环境变量读取）
WECHAT_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
if not WECHAT_WEBHOOK_URL:
    print("⚠️ 警告: 未设置 FEISHU_WEBHOOK_URL 环境变量，消息将无法发送。")
    print("   请在 GitHub Secrets 中添加 FEISHU_WEBHOOK_URL，值为企业微信机器人的 Webhook 地址。")

# 监控的币种列表：CoinGecko ID 和对应的显示名称
# 只保留主流币种，避免 CoinGecko 免费频率限制
SYMBOLS = {
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "binancecoin": "BNBUSDT",
    "solana": "SOLUSDT",
    "ripple": "XRPUSDT",
    "cardano": "ADAUSDT",
    "dogecoin": "DOGEUSDT",
    "avalanche-2": "AVAXUSDT",
    "polkadot": "DOTUSDT",
    "chainlink": "LINKUSDT",
    "polygon": "MATICUSDT",
    "uniswap": "UNIUSDT",
    "cosmos": "ATOMUSDT",
    "litecoin": "LTCUSDT",
    "bitcoin-cash": "BCHUSDT",
    "near": "NEARUSDT",
    "filecoin": "FILUSDT",
    "aptos": "APTUSDT",
    "arbitrum": "ARBUSDT",
    "optimism": "OPUSDT"
}

# 技术指标参数
BB_PERIOD = 20      # 布林带周期
EMA_PERIOD = 89     # EMA 周期
OHLC_DAYS = 7       # 获取最近 7 天数据（足够计算 89 小时 EMA）

# ==================== 核心函数 ====================

def get_ohlc(coin_id: str, vs_currency: str = "usd", days: int = 7):
    """
    从 CoinGecko OHLC 接口获取小时级别 K 线数据
    返回: DataFrame，包含 open, high, low, close, timestamp
    """
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
    params = {
        "vs_currency": vs_currency,
        "days": days,
        "precision": "full"   # 返回完整小数位
    }
    
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        if not data:
            print(f"  ⚠️ {coin_id} 没有返回 OHLC 数据")
            return None
        
        # 数据格式: [[timestamp, open, high, low, close], ...]
        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close"])
        
        # 转换时间戳为可读格式
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
        df["timestamp"] = df["timestamp"]  # 保留毫秒时间戳
        
        # 确保数据类型为 float
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        
        return df
        
    except requests.exceptions.RequestException as e:
        print(f"  Error: 获取 {coin_id} OHLC 数据失败: {e}")
        return None


def calculate_indicators(df: pd.DataFrame):
    """
    计算布林带中线和 89EMA
    """
    close_prices = df["close"]
    
    # 布林带中线 = 20 周期简单移动平均 (SMA)
    bb_middle = close_prices.rolling(window=BB_PERIOD).mean()
    
    # 89 指数移动平均 (EMA)
    ema_89 = close_prices.ewm(span=EMA_PERIOD, adjust=False).mean()
    
    return bb_middle, ema_89


def check_three_candles(df: pd.DataFrame):
    """
    检查最近三根 K 线是否为三连涨或三连跌
    返回: (three_up, three_down)
    """
    if len(df) < 4:
        return False, False
    
    last_3 = df.tail(3)
    is_up = last_3["close"] > last_3["open"]
    
    three_up = is_up.all()
    three_down = (~is_up).all()
    
    return three_up, three_down


def scan_symbol(coin_id: str, display_name: str):
    """
    扫描单个币种，若符合条件则返回结果字典，否则返回 None
    """
    df = get_ohlc(coin_id, days=OHLC_DAYS)
    if df is None or len(df) < EMA_PERIOD + 3:
        return None
    
    bb_middle, ema_89 = calculate_indicators(df)
    latest_bb_middle = bb_middle.iloc[-1]
    latest_ema_89 = ema_89.iloc[-1]
    
    three_up, three_down = check_three_candles(df)
    
    # 计算最近 3 小时涨跌幅
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
    
    # 条件匹配
    if three_up and latest_bb_middle > latest_ema_89:
        result["match"] = "UP"
        return result
    if three_down and latest_bb_middle < latest_ema_89:
        result["match"] = "DOWN"
        return result
    
    result["match"] = None
    return result


def send_to_wechat(message: str):
    """
    发送消息到企业微信机器人
    企业微信 Webhook 格式: {"msgtype": "text", "text": {"content": "消息"}}
    """
    if not WECHAT_WEBHOOK_URL:
        print("  ⚠️ 企业微信 Webhook URL 未设置，跳过发送")
        return
    
    headers = {"Content-Type": "application/json"}
    payload = {
        "msgtype": "text",
        "text": {
            "content": message
        }
    }
    
    try:
        response = requests.post(WECHAT_WEBHOOK_URL, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()
        
        # 企业微信成功返回 errcode: 0
        if result.get("errcode") == 0:
            print("  ✅ 企业微信消息发送成功")
        else:
            print(f"  ⚠️ 企业微信消息发送失败: {result}")
    except Exception as e:
        print(f"  Error: 发送企业微信消息失败: {e}")


def format_result_message(result: dict) -> str:
    """
    格式化扫描结果为易读的文本
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
    for coin_id, display_name in SYMBOLS.items():
        print(f"  扫描 {display_name}...")
        res = scan_symbol(coin_id, display_name)
        if res and res["match"]:
            matched.append(res)
            print(f"  ✅ {display_name} 符合条件: {res['match']}")
        time.sleep(0.2)  # 避免请求过快，CoinGecko 免费版限制每秒约 1~2 次
    
    if matched:
        for r in matched:
            send_to_wechat(format_result_message(r))
            time.sleep(0.5)  # 企业微信也有频率限制
        print(f"[INFO] 共发现 {len(matched)} 个符合条件的币种，已推送")
    else:
        heartbeat = f"🕐 扫描完成 [{datetime.now().strftime('%Y-%m-%d %H:%M')}]，共扫描 {len(SYMBOLS)} 个币种，暂未发现符合条件的标的。"
        send_to_wechat(heartbeat)
        print("[INFO] 未发现符合条件的币种，已发送心跳通知")
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 扫描完成")


if __name__ == "__main__":
    main()