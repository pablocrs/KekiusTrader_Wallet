# KekiusTrader_Wallet

Ultra-low latency Solana wallet operations service with automated profit management.

## Overview

KekiusTrader_Wallet is a high-performance microservice that provides lightning-fast trading operations on Solana. Built on the same architecture as KekiusTrader_Speed, it offers REST API and WebSocket streaming for buy/sell operations with automatic USDC profit conversion and cold wallet security.

## Features

### 🚀 Core Trading Capabilities
- **Buy Orders**: Purchase tokens with SOL via Jupiter Aggregator
- **Sell Orders**: Sell specific token amounts for SOL
- **Sell All**: Liquidate entire token positions instantly
- **Wallet Info**: Real-time balance and portfolio tracking

### ⚡ Ultra-Low Latency
- **uvloop**: 2-4x faster than standard asyncio
- **ULTRA_FAST Mode**: Skip preflight simulation for maximum speed
- **Jupiter Aggregator**: Best price discovery across all Solana DEXes
- **Priority Fees**: Configurable per speed mode
- **Target**: <2s transaction confirmation with premium RPC

### 💎 Automated Profit Management
- **Auto USDC Conversion**: Automatically converts SOL profits to USDC
- **Configurable Threshold**: Set minimum profit before conversion
- **Cold Wallet Transfer**: Automatically moves USDC to cold storage
- **Balance Tracking**: Monitors profit in real-time

### 🔒 Security
- **Private Key Security**: Environment-based key management
- **Cold Wallet Backup**: Separate security for profits
- **Slippage Protection**: Configurable slippage limits
- **Transaction Validation**: Optional safety checks

### 🐳 Docker Ready
- Complete containerization with docker-compose
- Shared network with KekiusTrader ecosystem
- Environment-based configuration

## Architecture

```
┌─────────────────────┐
│   FastAPI Server    │
│   REST + WebSocket  │
│   Port: 8003        │
└──────────┬──────────┘
           │
    ┌──────┴──────┐
    │   Engine    │
    │  (uvloop)   │
    └──────┬──────┘
           │
    ┌──────┴──────────────────────┐
    │                              │
┌───▼────────┐          ┌─────────▼────────┐
│  Trading   │          │   Wallet Info    │
│  Engine    │          │   Management     │
└───┬────────┘          └──────────────────┘
    │
    ├─────────────┬──────────────┐
    │             │              │
┌───▼──────┐  ┌──▼────┐  ┌──────▼──────┐
│ Jupiter  │  │Wallet │  │   Profit    │
│  Client  │  │Client │  │  Converter  │
└──────────┘  └───────┘  └─────────────┘
                │
         ┌──────┴──────┐
         │             │
    ┌────▼───┐   ┌────▼──────┐
    │ Solana │   │   Cold    │
    │  RPC   │   │  Wallet   │
    └────────┘   └───────────┘
```

## Installation

### Docker (Recommended)

```bash
# Clone the repository
cd KekiusTrader_Wallet

# Create .env file from example
cp .env.example .env

# Edit .env with your settings
# CRITICAL: Set WALLET_PRIVATE_KEY and COLD_WALLET_ADDRESS

# Create Docker network (if not exists)
docker network create kekius-network

# Build and start
docker-compose up -d

# View logs
docker-compose logs -f wallet
```

### Local Development

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your settings

# Run the service
python main.py
```

## Configuration

Create a `.env` file with your settings:

```env
# Solana RPC (Use premium for best performance!)
SOLANA_RPC_URL=https://api.mainnet-beta.solana.com
SOLANA_WS_URL=wss://api.mainnet-beta.solana.com

# Wallet (CRITICAL: Keep secure!)
WALLET_PRIVATE_KEY=your_base58_private_key_here
COLD_WALLET_ADDRESS=your_cold_wallet_public_key_here

# Trading Defaults
DEFAULT_SLIPPAGE_BPS=100        # 1%
DEFAULT_SPEED_MODE=ULTRA_FAST   # ULTRA_FAST, BALANCED, SAFE

# Auto Profit Management
ENABLE_AUTO_PROFIT_CONVERSION=true
PROFIT_THRESHOLD_SOL=0.1        # Min profit to convert
AUTO_TRANSFER_TO_COLD_WALLET=true
TRANSFER_THRESHOLD_USDC=10.0    # Min USDC to transfer

# Priority Fees (microlamports)
PRIORITY_FEE_ULTRA_FAST=100000  # 0.0001 SOL
PRIORITY_FEE_BALANCED=50000
PRIORITY_FEE_SAFE=10000
```

### Speed Modes

| Mode | Preflight | Priority Fee | Use Case |
|------|-----------|--------------|----------|
| **ULTRA_FAST** | ❌ Skipped | High (100k µlamports) | Time-critical trades |
| **BALANCED** | ✅ Enabled | Medium (50k µlamports) | Normal trading |
| **SAFE** | ✅ Enabled | Low (10k µlamports) | Large positions |

## API Endpoints

### Health Check
```
GET /health
```

Returns service status and configuration.

**Response:**
```json
{
  "status": "healthy",
  "service": "KekiusTrader_Wallet",
  "wallet": "YourPublicKeyHere...",
  "features": {
    "auto_profit_conversion": true,
    "cold_wallet_transfer": true
  }
}
```

### Buy Token
```
POST /buy
```

Purchase tokens with SOL.

**Request:**
```json
{
  "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
  "amount_sol": 1.0,
  "slippage_bps": 100,
  "speed_mode": "ULTRA_FAST"
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "signature": "5kw...",
    "type": "BUY",
    "mint": "EPj...",
    "input_amount": 1.0,
    "output_amount": 1000000,
    "price_impact": 0.12,
    "status": "pending"
  },
  "message": "Buy order submitted. Signature: 5kw..."
}
```

### Sell Token
```
POST /sell
```

Sell tokens for SOL. Triggers auto profit conversion if enabled.

**Request:**
```json
{
  "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
  "amount": 1000000,
  "slippage_bps": 100,
  "speed_mode": "ULTRA_FAST"
}
```

### Sell All
```
POST /sell-all
```

Liquidate entire token position.

**Request:**
```json
{
  "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
  "slippage_bps": 150,
  "speed_mode": "ULTRA_FAST"
}
```

### Wallet Info
```
GET /wallet/info
```

Get complete wallet information including all token balances.

**Response:**
```json
{
  "success": true,
  "data": {
    "address": "Your...",
    "sol_balance": 10.5,
    "token_count": 5,
    "tokens": [...],
    "cold_wallet": "Cold...",
    "timestamp": "2025-11-26T22:00:00"
  }
}
```

### Quick Balance
```
GET /wallet/balance
```

Ultra-fast SOL balance check (uses cache).

### Token Balance
```
GET /wallet/balance/{mint}
```

Get balance for specific token.

### Manual Profit Conversion
```
POST /profit/convert
```

Manually trigger USDC profit conversion.

## WebSocket Streaming

### Connection
```
WS /ws
```

Connect to receive real-time updates.

### Message Types

**Connected:**
```json
{
  "type": "connected",
  "message": "Connected to KekiusTrader_Wallet",
  "features": ["transaction_updates", "balance_changes", "profit_alerts"]
}
```

**Transaction Update:**
```json
{
  "type": "transaction",
  "tx_type": "BUY",
  "signature": "5kw...",
  "status": "confirmed",
  "details": {...},
  "explorer": "https://solscan.io/tx/5kw..."
}
```

**Balance Update:**
```json
{
  "type": "balance_update",
  "data": {
    "sol_balance": 10.5,
    "timestamp": "..."
  }
}
```

**Alert:**
```json
{
  "type": "alert",
  "alert_type": "success",
  "message": "Profit converted to USDC",
  "data": {...}
}
```

## Examples

### Buy Token
```bash
python examples/test_buy.py
```

### Sell Token
```bash
python examples/test_sell.py
```

### Monitor WebSocket
```bash
python examples/websocket_listener.py
```

### Check Wallet Info
```bash
python examples/wallet_info.py

# Monitor balance changes
python examples/wallet_info.py monitor
```

## Integration with KekiusTrader Ecosystem

### Network Setup
Ensure all services are on the same Docker network:

```bash
docker network create kekius-network
```

### Service URLs (Docker)
- **Speed Service**: `ws://kekius_speed:8002/ws`
- **Wallet Service**: `http://kekius_wallet:8003`

### Example: Sniper Integration
```python
# Sniper receives price update from Speed
# Executes buy via Wallet

import aiohttp

async def execute_buy(mint: str, amount_sol: float):
    async with aiohttp.ClientSession() as session:
        await session.post(
            "http://kekius_wallet:8003/buy",
            json={
                "mint": mint,
                "amount_sol": amount_sol,
                "speed_mode": "ULTRA_FAST"
            }
        )
```

## Performance Metrics

Based on testing with premium RPC (Helius/QuickNode):

- **API Response Time**: <50ms
- **Jupiter Quote**: ~200-300ms
- **Transaction Build**: ~100ms
- **Transaction Confirm**: 1-1.5s (ULTRA_FAST mode)
- **End-to-End**: ~1.5-2s from API call to confirmed

### Optimization Tips

1. **Use Premium RPC**: Helius, QuickNode, or Triton
2. **ULTRA_FAST Mode**: Skip preflight for critical trades
3. **Higher Priority Fees**: Faster confirmation
4. **Cached Balances**: Use `/wallet/balance` for speed
5. **WebSocket Monitoring**: Real-time updates without polling

## Security Best Practices

### Private Key Management
- ✅ Store in environment variables (`.env`)
- ✅ Never commit to version control
- ✅ Use separate wallets for trading vs storage
- ✅ Monitor cold wallet transfers
- ❌ Never expose in code or logs

### Cold Wallet Setup
1. Generate separate cold wallet (offline)
2. Set `COLD_WALLET_ADDRESS` in `.env`
3. Configure `TRANSFER_THRESHOLD_USDC`
4. Monitor transfers regularly

### Network Security
- Use firewall/VPC for production
- Restrict API access to trusted IPs
- Consider API authentication for production

## Troubleshooting

### "No private key provided"
Set `WALLET_PRIVATE_KEY` in `.env` file with base58 encoded key.

### Trades not executing
- Check SOL balance (needs funds for trades + fees)
- Verify RPC endpoint is working
- Try BALANCED mode instead of ULTRA_FAST
- Check slippage settings (increase if needed)

### High latency
- Upgrade to premium RPC provider
- Increase priority fees
- Use WebSocket instead of REST polling

### Profit conversion not triggering
- Check `ENABLE_AUTO_PROFIT_CONVERSION=true`
- Verify profit exceeds `PROFIT_THRESHOLD_SOL`
- Ensure sells are completing successfully

## Development

### Project Structure
```
KekiusTrader_Wallet/
├── core/
│   ├── dex/
│   │   └── jupiter_client.py    # Jupiter Aggregator integration
│   ├── wallet_client.py          # Solana wallet operations
│   ├── trading_engine.py         # Buy/sell logic + profit conversion
│   ├── wallet_info.py            # Balance and portfolio management
│   ├── api_server.py             # FastAPI REST endpoints
│   ├── websocket_server.py       # WebSocket broadcaster
│   └── engine.py                 # Main orchestration
├── examples/                     # Example scripts
├── config.py                     # Configuration management
├── main.py                       # Entry point
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```

## License

MIT

## Contributing

Contributions welcome! Please ensure:
- Code follows existing patterns
- Add tests for new features
- Update documentation
- Minimize latency impact

---

**Built with ⚡ for the KekiusTrader ecosystem**
