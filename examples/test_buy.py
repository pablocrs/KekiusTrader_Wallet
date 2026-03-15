"""
Example: Trade USDC with SOL.

Modes:
- wallet  : call KekiusTrader_Wallet /buy directly
- humming : submit a BUY signal to Scout and let KekiusTrader_Humming execute
- both    : run wallet then humming flow
- roundtrip: wallet buy then wallet sell-all (USDC -> SOL)
"""
import asyncio
import os
import time
import uuid
from typing import Any, Dict, Optional

import aiohttp

API_URL = os.getenv("WALLET_API_URL", "http://localhost:8003")
SCOUT_URL = os.getenv("SCOUT_API_URL", "http://localhost:8001")
SIGNAL_TOKEN = os.getenv("SIGNAL_API_TOKEN", "test_token_123")
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
TRADE_LIMIT_SOL = float(os.getenv("TRADE_LIMIT_SOL", "0.05"))


def _capped_buy_amount() -> float:
    requested = float(os.getenv("BUY_AMOUNT_SOL", "0.05"))
    capped = min(requested, TRADE_LIMIT_SOL)
    if capped < requested:
        print(
            f"[wallet] Requested BUY_AMOUNT_SOL={requested} exceeds trade limit "
            f"{TRADE_LIMIT_SOL}. Using {capped}."
        )
    return capped


def _wallet_payload() -> dict:
    return {
        "mint": USDC_MINT,
        "amount_sol": _capped_buy_amount(),
        "slippage_bps": int(os.getenv("BUY_SLIPPAGE_BPS", "100")),
        "speed_mode": os.getenv("BUY_SPEED_MODE", "SAFE"),
    }


def _signal_payload() -> dict:
    spend = min(
        float(os.getenv("HUMMING_SPEND_SOL", os.getenv("BUY_AMOUNT_SOL", "0.05"))),
        TRADE_LIMIT_SOL,
    )
    signal_id = os.getenv("HUMMING_SIGNAL_ID", f"codex-buy-{int(time.time())}-{uuid.uuid4().hex[:6]}")
    return {
        "signal_id": signal_id,
        "source": os.getenv("HUMMING_SIGNAL_SOURCE", "manual_test"),
        "mint": USDC_MINT,
        "spend_amount": spend,
        "spend_denom": "SOL",
        "max_slippage_bps": int(os.getenv("HUMMING_MAX_SLIPPAGE_BPS", "500")),
        "ttl_sec": int(os.getenv("HUMMING_TTL_SEC", "180")),
        "payload": {
            "symbol": "USDC",
            "decision": "buy",
            "confidence": int(os.getenv("HUMMING_CONFIDENCE", "90")),
        },
    }


async def _read_json_response(response: aiohttp.ClientResponse) -> Dict[str, Any]:
    text = await response.text()
    try:
        return await response.json()
    except Exception:
        return {"raw": text}


async def _get_wallet_snapshot(session: aiohttp.ClientSession) -> Dict[str, float]:
    sol = 0.0
    usdc = 0.0

    async with session.get(f"{API_URL}/wallet/balance") as response:
        body = await _read_json_response(response)
        if response.status == 200:
            sol = float(((body.get("data") or {}).get("sol_balance") or 0.0))

    async with session.get(f"{API_URL}/wallet/balance/{USDC_MINT}") as response:
        body = await _read_json_response(response)
        if response.status == 200:
            data = body.get("data") or {}
            usdc = float(data.get("ui_amount") or 0.0)

    return {"sol": sol, "usdc": usdc}


async def run_wallet_buy(session: aiohttp.ClientSession) -> bool:
    req = _wallet_payload()
    print(f"[wallet] POST {API_URL}/buy")
    print(f"[wallet] Payload: {req}")
    req_timeout = aiohttp.ClientTimeout(total=float(os.getenv("BUY_REQUEST_TIMEOUT_SEC", "180")))
    try:
        async with session.post(f"{API_URL}/buy", json=req, timeout=req_timeout) as response:
            body = await response.text()
            if response.status != 200:
                print(f"[wallet] Error (HTTP {response.status}): {body}")
                return False
            result = await response.json()
            data = result.get("data", {})
            sig = data.get("signature")
            print("[wallet] Buy order submitted")
            print(f"[wallet] Signature: {sig}")
            print(f"[wallet] Output amount: {data.get('output_amount')}")
            if sig:
                print(f"[wallet] Solscan: https://solscan.io/tx/{sig}")
            return True
    except asyncio.TimeoutError:
        print("[wallet] Buy request timed out. Checking balances to determine if it landed on-chain.")
        return False
    except aiohttp.ClientError as exc:
        print(f"[wallet] Buy request transport error: {exc}")
        return False


async def run_wallet_sell_all(session: aiohttp.ClientSession) -> bool:
    payload = {
        "mint": USDC_MINT,
        "slippage_bps": int(os.getenv("SELL_ALL_SLIPPAGE_BPS", "150")),
        "speed_mode": os.getenv("SELL_ALL_SPEED_MODE", "SAFE"),
    }
    print(f"[wallet] POST {API_URL}/sell-all")
    print(f"[wallet] Payload: {payload}")
    req_timeout = aiohttp.ClientTimeout(total=float(os.getenv("SELL_REQUEST_TIMEOUT_SEC", "180")))
    try:
        async with session.post(f"{API_URL}/sell-all", json=payload, timeout=req_timeout) as response:
            body = await response.text()
            if response.status != 200:
                print(f"[wallet] Sell-all error (HTTP {response.status}): {body}")
                return False
            result = await response.json()
            data = result.get("data", {})
            sig = data.get("signature")
            print("[wallet] Sell-all submitted")
            print(f"[wallet] Signature: {sig}")
            if sig:
                print(f"[wallet] Solscan: https://solscan.io/tx/{sig}")
            return True
    except asyncio.TimeoutError:
        print("[wallet] Sell-all request timed out. Checking balances to determine if it landed on-chain.")
        return False
    except aiohttp.ClientError as exc:
        print(f"[wallet] Sell-all request transport error: {exc}")
        return False


async def run_wallet_roundtrip(session: aiohttp.ClientSession) -> None:
    print(f"[wallet] Roundtrip with trade limit {TRADE_LIMIT_SOL} SOL")
    before = await _get_wallet_snapshot(session)
    print(f"[wallet] Before  SOL={before['sol']:.9f}  USDC={before['usdc']:.6f}")

    buy_ok = await run_wallet_buy(session)

    wait_sec = float(os.getenv("ROUNDTRIP_BUY_SETTLE_SEC", "8"))
    await asyncio.sleep(wait_sec)

    mid = await _get_wallet_snapshot(session)
    print(f"[wallet] After buy  SOL={mid['sol']:.9f}  USDC={mid['usdc']:.6f}")

    # On Solana, a send timeout can still land on-chain later. Continue the
    # roundtrip if we hold any USDC (or if requested explicitly).
    force_sell_all = os.getenv("ROUNDTRIP_FORCE_SELL_ALL", "1").strip() not in {"0", "false", "False"}
    should_sell_all = force_sell_all and mid["usdc"] > 0.0
    if not buy_ok and not should_sell_all:
        print("[wallet] Buy failed and no USDC detected; skipping sell-all.")
        return

    sell_ok = await run_wallet_sell_all(session)
    if not sell_ok:
        return

    wait_sec = float(os.getenv("ROUNDTRIP_SELL_SETTLE_SEC", "8"))
    await asyncio.sleep(wait_sec)

    after = await _get_wallet_snapshot(session)
    print(f"[wallet] After sell-all  SOL={after['sol']:.9f}  USDC={after['usdc']:.6f}")
    print(
        f"[wallet] Delta  SOL={after['sol'] - before['sol']:+.9f}  "
        f"USDC={after['usdc'] - before['usdc']:+.6f}"
    )


async def _find_signal_status(session: aiohttp.ClientSession, signal_id: str) -> Optional[str]:
    headers = {"Authorization": f"Bearer {SIGNAL_TOKEN}"}
    async with session.get(f"{SCOUT_URL}/v1/signals", headers=headers) as response:
        if response.status != 200:
            return None
        body = await response.json()
        for row in body.get("signals", []):
            if row.get("signal_id") == signal_id:
                return row.get("status")
    return None


async def run_humming_buy(session: aiohttp.ClientSession) -> None:
    payload = _signal_payload()
    headers = {
        "Authorization": f"Bearer {SIGNAL_TOKEN}",
        "Content-Type": "application/json",
    }
    print(f"[humming] POST {SCOUT_URL}/v1/signals")
    print(f"[humming] Payload: {payload}")
    async with session.post(f"{SCOUT_URL}/v1/signals", headers=headers, json=payload) as response:
        body = await response.text()
        if response.status != 200:
            print(f"[humming] Error (HTTP {response.status}): {body}")
            return
        print(f"[humming] Signal accepted: {body}")

    signal_id = payload["signal_id"]
    for _ in range(int(os.getenv("HUMMING_WAIT_POLLS", "30"))):
        status = await _find_signal_status(session, signal_id)
        if status in {"executed", "rejected", "failed", "simulated"}:
            print(f"[humming] Final signal status for {signal_id}: {status}")
            return
        await asyncio.sleep(float(os.getenv("HUMMING_POLL_SEC", "2")))

    print(f"[humming] Timed out waiting final status for signal {signal_id}")


async def main() -> None:
    mode = os.getenv("BUY_TEST_MODE", "roundtrip").strip().lower()
    session_timeout = aiohttp.ClientTimeout(
        total=None,
        connect=float(os.getenv("SESSION_CONNECT_TIMEOUT_SEC", "20")),
        sock_connect=float(os.getenv("SESSION_SOCK_CONNECT_TIMEOUT_SEC", "20")),
        sock_read=float(os.getenv("SESSION_SOCK_READ_TIMEOUT_SEC", "240")),
    )
    async with aiohttp.ClientSession(timeout=session_timeout) as session:
        if mode in {"wallet", "both"}:
            await run_wallet_buy(session)
        if mode in {"roundtrip", "wallet_roundtrip"}:
            await run_wallet_roundtrip(session)
        if mode in {"humming", "both"}:
            await run_humming_buy(session)


if __name__ == "__main__":
    asyncio.run(main())
