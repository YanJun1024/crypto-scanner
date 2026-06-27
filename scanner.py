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
import sys          # 用于强制刷新输出

# ==================== 配置 ====================

WECHAT_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
if not WECHAT_WEBHOOK_URL:
    print("⚠️ 未设置 FEISHU_WEBHOOK_URL 环境变量", flush=True)

# ==================== 币种列表（清洗后，去重） ====================
SYMBOLS = {
    #  Layer 1 公链
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "binancecoin": "BNBUSDT",
    "solana": "SOLUSDT",
    "ripple": "XRPUSDT",
    "cardano": "ADAUSDT",
    "avalanche-2": "AVAXUSDT",
    "polkadot": "DOTUSDT",
    "near": "NEARUSDT",
    "aptos": "APTUSDT",
    "sui": "SUIUSDT",
    "toncoin": "TONUSDT",
    "trx": "TRXUSDT",
    "litecoin": "LTCUSDT",
    "bitcoin-cash": "BCHUSDT",
    "monero": "XMRUSDT",
    "ethereum-classic": "ETCUSDT",
    "algorand": "ALGOUSDT",
    "vechain": "VETUSDT",
    "internet-computer": "ICPUSDT",
    "hedera-hashgraph": "HBARUSDT",
    "quant-network": "QNTUSDT",
    "filecoin": "FILUSDT",
    "theta-token": "THETAUSDT",
    "tezos": "XTZUSDT",
    "eos": "EOSUSDT",
    "nano": "NANOUSDT",
    "icon": "ICXUSDT",
    "ontology": "ONTUSDT",
    "harmony": "ONEUSDT",
    "kadena": "KDAUSDT",
    "mina": "MINAUSDT",
    "celo": "CELOUSDT",
    #  DeFi 与 DEX
    "chainlink": "LINKUSDT",
    "uniswap": "UNIUSDT",
    "aave": "AAVEUSDT",
    "maker": "MKRUSDT",
    "compound": "COMPUSDT",
    "curve-dao-token": "CRVUSDT",
    "sushi": "SUSHIUSDT",
    "pancakeswap": "CAKEUSDT",
    "thorchain": "RUNEUSDT",
    "lido-dao": "LDOUSDT",
    "rocket-pool": "RPLUSDT",
    "dydx": "DYDXUSDT",
    "gmx": "GMXUSDT",
    "1inch": "1INCHUSDT",
    "balancer": "BALUSDT",
    "yearn-finance": "YFIUSDT",
    #  Layer 2 与跨链
    "polygon": "MATICUSDT",
    "arbitrum": "ARBUSDT",
    "optimism": "OPUSDT",
    "cosmos": "ATOMUSDT",
    "celestia": "TIAUSDT",
    "osmosis": "OSMOUSDT",
    "injective": "INJUSDT",
    "sei": "SEIUSDT",
    "manta-network": "MANTAUSDT",
    "zksync": "ZKUSDT",
    "starknet": "STRKUSDT",
    #  稳定币与基础设施（剔除纯稳定币，避免无意义信号）
    # "tether": "USDTUSDT",        # 稳定币无波动，不参与扫描
    # "usd-coin": "USDCUSDT",
    # "dai": "DAIUSDT",
    # "true-usd": "TUSDUSDT",
    # "frax": "FRAXUSDT",
    "wrapped-bitcoin": "WBTCUSDT",
    "weth": "WETHUSDT",
    "staked-ether": "STETHUSDT",
    "bnb": "BNBUSDT",
    "okb": "OKBUSDT",
    "cronos": "CROUSDT",
    "leo-token": "LEOUSDT",
    "bitget-token": "BGBUSDT",
    #  去中心化存储与计算
    "arweave": "ARUSDT",
    "siacoin": "SCUSDT",
    "golem": "GLMUSDT",
    "livepeer": "LPTUSDT",
    "akash-network": "AKTUSDT",
    #  Meme 与社区币
    "dogecoin": "DOGEUSDT",
    "shiba-inu": "SHIBUSDT",
    "pepe": "PEPEUSDT",
    "bonk": "BONKUSDT",
    "floki": "FLOKIUSDT",
    "dogwifhat": "WIFUSDT",
    # "bret": "BRETTUSDT",         # CoinGecko ID 可能不准确，暂注释
    # "popcat": "POPCATUSDT",
    #  游戏与元宇宙
    "decentraland": "MANAUSDT",
    "the-sandbox": "SANDUSDT",
    "axie-infinity": "AXSUSDT",
    "gala": "GALAUSDT",
    "immutable-x": "IMXUSDT",
    "illuvium": "ILVUSDT",
    "pixel": "PIXELUSDT",
    "portal": "PORTALUSDT",
    #  AI 与数据
    "fetch-ai": "FETUSDT",
    "singularitynet": "AGIXUSDT",
    "ocean-protocol": "OCEANUSDT",
    "numeraire": "NMRUSDT",
    "bittensor": "TAOUSDT",
    "render-token": "RNDRUSDT",
    #  其他
    "kucoin-shares": "KCSUSDT",
    "huobi-token": "HTUSDT",
    "gatechain-token": "GTUSDT",
    "nexo": "NEXOUSDT",
    "celsius-network": "CELUSDT",
    "helium": "HNTUSDT",
    "basic-attention-token": "BATUSDT",
    "zcash": "ZECUSDT",
    "dash": "DASHUSDT",
}

# 技术指标参数
BB_PERIOD = 20
EMA_PERIOD = 89
OHLC_DAYS = 7
REQUEST_DELAY = 2.0          # 增加到 2 秒，避免触发 429 限制

# ==================== 数据获取（带重试与404处理） ====================

def get_ohlc_with_retry(coin_id: str, vs_currency: str = "usd", days: int = 7, max_retries: int = 3):
    """
    带重试机制的 OHLC 获取，遇到 404 直接跳过，429 自动等待后重试
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
            if response.status_code == 404:
                print(f"  ⚠️ {coin_id} 无效ID，跳过", flush=True)
                return None
            if response.status_code == 429:
                wait = (attempt + 1) * 2  # 2s, 4s, 6s
                print(f"  ⏳ 触发 429 限制，等待 {wait}s 后重试 ({attempt+1}/{max_retries})...", flush=True)
                time.sleep(wait)
                continue
            response.raise_for_status()
            data = response.json()
            if not data:
                print(f"  ⚠️ {coin_id} 返回空数据", flush=True)
                return None
            # 转为 DataFrame
            df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close"])
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
            for col in ["open", "high", "low", "close"]:
                df[col] = df[col].astype(float)
            return df
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                print(f"  Error: 获取 {coin_id} 失败，已达最大重试次数: {e}", flush=True)
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
    matched = []
    total = len(SYMBOLS)
    print(f"[INFO] 共 {total} 个币种待扫描", flush=True)
    
    for idx, (coin_id, display_name) in enumerate(SYMBOLS.items(), 1):
        print(f"  [{idx}/{total}] 扫描 {display_name}...", flush=True)
        res = scan_symbol(coin_id, display_name)
        if res and res["match"]:
            matched.append(res)
            print(f"  ✅ {display_name} 符合条件: {res['match']}", flush=True)
        time.sleep(REQUEST_DELAY)  # 避免频率限制
    
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