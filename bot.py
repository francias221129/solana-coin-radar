
import os
import time
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BASE = "https://api.dexscreener.com"
HEADERS = {"User-Agent": "SolanaCoinRadar/1.0"}

MIN_LIQUIDITY = 15000
MIN_VOLUME_1H = 10000
MIN_TXNS_1H = 60
MIN_BUY_RATIO = 0.55
MAX_AGE_HOURS = 24

seen = set()


def get_json(url):
    response = requests.get(
        url, headers=HEADERS, timeout=20
    )
    response.raise_for_status()
    return response.json()


def send_alert(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": message,
            "disable_web_page_preview": True
        },
        timeout=20
    )
    response.raise_for_status()


def scan():
    profiles = get_json(
        f"{BASE}/token-profiles/latest/v1"
    )

    addresses = list(dict.fromkeys(
        item["tokenAddress"]
        for item in profiles
        if item.get("chainId") == "solana"
        and item.get("tokenAddress")
        and item["tokenAddress"] not in seen
    ))[:30]

    if not addresses:
        return

    url = (
        f"{BASE}/tokens/v1/solana/"
        + ",".join(addresses)
    )
    pairs = get_json(url)

    best_pairs = {}

    for pair in pairs:
        token = pair.get("baseToken", {})
        address = token.get("address")

        if not address or address not in addresses:
            continue

        liquidity = (pair.get("liquidity") or {}).get("usd", 0) or 0
        volume = (pair.get("volume") or {}).get("h1", 0) or 0
        txns = (pair.get("txns") or {}).get("h1", {})
        buys = txns.get("buys", 0) or 0
        sells = txns.get("sells", 0) or 0
        total = buys + sells

        created = pair.get("pairCreatedAt")
        if not created:
            continue

        age = (
            datetime.now(timezone.utc).timestamp()
            - created / 1000
        ) / 3600

        if age < 0 or age > MAX_AGE_HOURS:
            continue

        if total == 0:
            continue

        buy_ratio = buys / total

        if (
            liquidity < MIN_LIQUIDITY
            or volume < MIN_VOLUME_1H
            or total < MIN_TXNS_1H
            or buy_ratio < MIN_BUY_RATIO
        ):
            continue

        score = 0
        score += 25 if liquidity >= 50000 else 15
        score += 25 if volume >= 25000 else 15
        score += 25 if total >= 150 else 15
        score += 25 if buy_ratio >= 0.65 else 15

        if address not in best_pairs or score > best_pairs[address][0]:
            best_pairs[address] = (score, pair)

    for address in addresses:
        seen.add(address)

    for address, (score, pair) in best_pairs.items():
        token = pair.get("baseToken", {})
        symbol = token.get("symbol", "UNKNOWN")
        name = token.get("name", "Unknown")
        liquidity = (pair.get("liquidity") or {}).get("usd", 0)
        volume = (pair.get("volume") or {}).get("h1", 0)
        txns = (pair.get("txns") or {}).get("h1", {})
        buys = txns.get("buys", 0)
        sells = txns.get("sells", 0)
        age = (
            datetime.now(timezone.utc).timestamp()
            - pair["pairCreatedAt"] / 1000
        ) / 3600

        message = (
            f"🚨 SOLANA COIN RADAR ALERT\n\n"
            f"Coin: {name} ({symbol})\n"
            f"Score: {score}/100\n"
            f"Liquidity: ${liquidity:,.0f}\n"
            f"1H Volume: ${volume:,.0f}\n"
            f"1H Buys: {buys}\n"
            f"1H Sells: {sells}\n"
            f"Pair age: {age:.1f} hours\n\n"
            f"Contract:\n{address}\n\n"
            f"Chart: {pair.get('url', 'Unavailable')}\n"
            f"Axiom: https://axiom.trade/\n\n"
            f"⚠️ Screening alert only. Not a buy signal."
        )

        send_alert(message)
        print(f"Alert sent: {symbol} - {score}/100")


def main():
    send_alert(
        "✅ Solana Coin Radar is online!\n"
        "Monitoring newly surfaced Solana token profiles."
    )

    while True:
        try:
            scan()
        except Exception as error:
            print(f"Scan error: {error}")

        time.sleep(60)


if __name__ == "__main__":
    main()
