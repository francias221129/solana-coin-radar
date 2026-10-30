
import os
import time
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BASE = "https://api.dexscreener.com"
RUGCHECK = "https://api.rugcheck.xyz"
HEADERS = {"User-Agent": "SolanaCoinRadar/2.0"}

MIN_LIQUIDITY = 15000
MIN_VOLUME_1H = 10000
MIN_TXNS_1H = 60
MIN_BUY_RATIO = 0.55
MAX_AGE_HOURS = 24

seen = set()


def get_json(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        if response.status_code != 200:
            print(f"Data request failed: HTTP {response.status_code}")
            return None
        return response.json()
    except requests.RequestException:
        print("Network request failed.")
        return None


def send_alert(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": message,
                "disable_web_page_preview": True
            },
            timeout=20
        )
        if response.status_code != 200:
            print(f"Telegram failed: HTTP {response.status_code}")
            return False
        return True
    except requests.RequestException:
        print("Telegram connection failed.")
        return False


def rugcheck(address):
    summary_url = f"{RUGCHECK}/v1/tokens/{address}/report/summary"
    report_url = f"{RUGCHECK}/v1/tokens/{address}/report"

    data = get_json(summary_url)
    report = get_json(report_url)

    if not isinstance(data, dict):
        data = {}

    if not isinstance(report, dict):
        report = {}

    token = report.get("token") or {}

    if not isinstance(token, dict):
        token = {}

    risks = data.get("risks") or []

    if not isinstance(risks, list):
        risks = []

    score = data.get("score_normalised")
    lp = data.get("lpLockedPct")

    def authority_status(field):
        if field not in token:
            return "UNKNOWN"

        return "REVOKED" if token[field] is None else "ACTIVE"

    mint_authority = authority_status("mintAuthority")
    freeze_authority = authority_status("freezeAuthority")

    serious = any(
        str(item.get("level", "")).lower()
        in ("danger", "critical")
        for item in risks
        if isinstance(item, dict)
    )

    if serious:
        status = "HIGH RISK FLAGS"
    elif risks:
        status = "RISK FLAGS FOUND"
    elif data:
        status = "NO RISKS LISTED - NOT VERIFIED SAFE"
    else:
        status = "UNVERIFIED"

    return {
        "status": status,
        "score": score,
        "lp": lp,
        "risks": risks,
        "mint_authority": mint_authority,
        "freeze_authority": freeze_authority
    }


def build_security_warnings(security):
    warnings = []

    if security["status"] == "HIGH RISK FLAGS":
        warnings.append(
            "RugCheck reported DANGER or CRITICAL risk flags."
        )
    elif security["status"] == "RISK FLAGS FOUND":
        warnings.append(
            "RugCheck reported one or more risk flags."
        )
    elif security["status"] == "UNVERIFIED":
        warnings.append(
            "RugCheck data could not be verified."
        )

    if security["mint_authority"] == "ACTIVE":
        warnings.append(
            "Mint authority is ACTIVE: more tokens may be created."
        )
    elif security["mint_authority"] == "UNKNOWN":
        warnings.append(
            "Mint authority could not be verified."
        )

    if security["freeze_authority"] == "ACTIVE":
        warnings.append(
            "Freeze authority is ACTIVE: token accounts may be frozen."
        )
    elif security["freeze_authority"] == "UNKNOWN":
        warnings.append(
            "Freeze authority could not be verified."
        )

    lp = security["lp"]

    if lp is None:
        warnings.append(
            "Liquidity lock information is unavailable."
        )
    else:
        try:
            lp_value = float(lp)

            if lp_value <= 0:
                warnings.append(
                    "Reported liquidity locked is 0%."
                )

        except (TypeError, ValueError):
            warnings.append(
                "Liquidity lock information could not be read."
            )

    return warnings


def scan():
    profiles = get_json(
        f"{BASE}/token-profiles/latest/v1"
    )

    if not isinstance(profiles, list):
        return

    addresses = list(dict.fromkeys(
        item["tokenAddress"]
        for item in profiles
        if item.get("chainId") == "solana"
        and item.get("tokenAddress")
        and item["tokenAddress"] not in seen
    ))[:30]

    if not addresses:
        return

    pairs = get_json(
        f"{BASE}/tokens/v1/solana/"
        + ",".join(addresses)
    )

    if not isinstance(pairs, list):
        return

    best_pairs = {}

    for pair in pairs:
        token = pair.get("baseToken", {})
        address = token.get("address")

        if not address or address not in addresses:
            continue

        liquidity = (
            pair.get("liquidity") or {}
        ).get("usd", 0) or 0

        volume = (
            pair.get("volume") or {}
        ).get("h1", 0) or 0

        txns = (pair.get("txns") or {}).get("h1", {})
        buys = txns.get("buys", 0) or 0
        sells = txns.get("sells", 0) or 0
        total = buys + sells

        created = pair.get("pairCreatedAt")

        if not created or total == 0:
            continue

        age = (
            datetime.now(timezone.utc).timestamp()
            - created / 1000
        ) / 3600

        if age < 0 or age > MAX_AGE_HOURS:
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

        if (
            address not in best_pairs
            or score > best_pairs[address][0]
        ):
            best_pairs[address] = (score, pair)

    for address in addresses:
        seen.add(address)

    for address, (score, pair) in best_pairs.items():
        token = pair.get("baseToken", {})
        symbol = token.get("symbol", "UNKNOWN")
        name = token.get("name", "Unknown")

        liquidity = (
            pair.get("liquidity") or {}
        ).get("usd", 0) or 0

        volume = (
            pair.get("volume") or {}
        ).get("h1", 0) or 0

        txns = (pair.get("txns") or {}).get("h1", {})
        buys = txns.get("buys", 0) or 0
        sells = txns.get("sells", 0) or 0

        age = (
            datetime.now(timezone.utc).timestamp()
            - pair["pairCreatedAt"] / 1000
        ) / 3600

        print(f"Checking security: {symbol}")

        security = rugcheck(address)

        risk_score = security["score"]
        lp = security["lp"]

        risk_text = (
            f"{risk_score}/100"
            if risk_score is not None
            else "Unavailable"
        )

        lp_text = (
            f"{lp}%"
            if lp is not None
            else "Unknown"
        )

        risk_names = [
            item.get("name", "Unnamed risk")
            for item in security["risks"][:3]
            if isinstance(item, dict)
        ]

        risk_details = (
            "\n".join(f"- {item}" for item in risk_names)
            if risk_names
            else "No risk details returned."
        )

        warnings = build_security_warnings(security)

        warning_text = (
            "\n".join(
                f"⚠️ {warning}"
                for warning in warnings
            )
            if warnings
            else (
                "No automatic warning flags detected. "
                "This is not proof of safety."
            )
        )

        message = (
            f"🚨 SOLANA COIN RADAR ALERT\n\n"
            f"Coin: {name} ({symbol})\n"
            f"Activity Score: {score}/100\n\n"
            f"--- MARKET DATA ---\n"
            f"Liquidity: ${liquidity:,.0f}\n"
            f"1H Volume: ${volume:,.0f}\n"
            f"1H Buys: {buys}\n"
            f"1H Sells: {sells}\n"
            f"Pair age: {age:.1f} hours\n\n"
            f"--- SECURITY CHECK ---\n"
            f"RugCheck: {security['status']}\n"
            f"Normalized risk score: {risk_text}\n"
            f"Liquidity locked: {lp_text}\n"
            f"Mint authority: {security['mint_authority']}\n"
            f"Freeze authority: {security['freeze_authority']}\n"
            f"Reported risks:\n{risk_details}\n\n"
            f"--- AUTOMATIC WARNINGS ---\n"
            f"{warning_text}\n\n"
            f"Contract:\n{address}\n\n"
            f"Chart: {pair.get('url', 'Unavailable')}\n"
            f"Security report: "
            f"https://rugcheck.xyz/tokens/{address}\n"
            f"Axiom: https://axiom.trade/t/{address}\n"
            f"⚠️ Informational screening only. "
            f"Not a buy signal or safety guarantee."
        )

        if send_alert(message):
            print(
                f"Alert sent: {symbol} - {score}/100"
            )


def main():
    if not send_alert(
        "✅ Solana Coin Radar upgraded and online!\n"
        "Market monitoring, RugCheck, authority checks, "
        "and automatic warnings enabled."
    ):
        print("Startup Telegram message failed.")

    while True:
        try:
            scan()
        except Exception as error:
            print(
                f"Scan error: {type(error).__name__}"
            )

        time.sleep(60)


if __name__ == "__main__":
    main()
