from core.wallet_client import WalletClient
from loguru import logger
from typing import Dict, Any, List
from config import settings
from datetime import datetime

class WalletInfo:
    """
    Wallet information and portfolio management.
    Provides real-time balance and holdings data.
    """
    
    def __init__(self, wallet: WalletClient):
        self.wallet = wallet
    
    async def get_full_wallet_info(self) -> Dict[str, Any]:
        """
        Get complete wallet information including SOL and all tokens.
        
        Returns:
            Dict with wallet address, SOL balance, and token holdings
        """
        try:
            # Get SOL balance
            sol_balance = await self.wallet.get_sol_balance(use_cache=False)
            
            # Get token accounts - ALWAYS force refresh to get latest data
            logger.info(f"🔄 Fetching token accounts (force_refresh=True)...")
            try:
                token_accounts = await self.wallet.get_token_accounts(force_refresh=True)
                logger.info(f"📊 Received {len(token_accounts)} token accounts from wallet client")
                
                # If we got 0 tokens, log a warning but don't fail
                if not token_accounts or len(token_accounts) == 0:
                    logger.warning(f"⚠️ Wallet service returned 0 token accounts for wallet {self.wallet.get_public_key_str()}")
                    logger.warning(f"⚠️ This might indicate: 1) Wallet has no tokens, 2) RPC error, 3) Parsing issue")
            except Exception as e:
                logger.error(f"❌ Error fetching token accounts: {e}")
                import traceback
                logger.error(traceback.format_exc())
                token_accounts = []  # Return empty list on error
            
            # Build wallet info
            wallet_info = {
                "address": self.wallet.get_public_key_str(),
                "sol_balance": sol_balance,
                "token_count": len(token_accounts),
                "tokens": [],
                "timestamp": datetime.now().isoformat(),
                "cold_wallet": settings.COLD_WALLET_ADDRESS or "Not configured"
            }
            
            logger.info(f"📦 Building wallet info: {sol_balance:.4f} SOL, {len(token_accounts)} token accounts")
            
            # Add token details - parse token account data
            # IMPORTANT: Include ALL tokens, even with zero balance
            for account in token_accounts:
                try:
                    # Parse token account data
                    account_pubkey = account.get("pubkey", "")
                    account_data = account.get("data")
                    
                    # Extract token info from account data
                    mint = account.get("mint", "Unknown")
                    balance = account.get("balance", 0)
                    ui_amount = account.get("ui_amount", 0)
                    decimals = account.get("decimals", 0)
                    symbol = account.get("symbol", "UNK")
                    name = account.get("name", "Unknown Token")
                    
                    # IMPORTANT: Include token even if balance is 0
                    token_info = {
                        "account": account_pubkey,
                        "mint": mint,
                        "balance": balance,
                        "decimals": decimals,
                        "ui_amount": ui_amount,
                        "symbol": symbol,
                        "name": name,
                        "amount": ui_amount,
                        "value_usd": 0.0  # Would need price data to calculate
                    }
                    
                    wallet_info["tokens"].append(token_info)
                    logger.debug(f"Added token to wallet info: {symbol} ({mint[:8]}...) - balance: {ui_amount}")
                except Exception as e:
                    logger.warning(f"Error parsing token account {account.get('pubkey', 'unknown')}: {e}")
                    # Add minimal info even if parsing fails
                    wallet_info["tokens"].append({
                        "account": account.get("pubkey", ""),
                        "mint": account.get("mint", "Unknown"),
                        "balance": account.get("balance", 0),
                        "ui_amount": account.get("ui_amount", 0),
                        "decimals": account.get("decimals", 0),
                        "symbol": "UNK",
                        "amount": account.get("ui_amount", 0),
                        "value_usd": 0.0
                    })
            
            logger.info(f"✅ Wallet info: {sol_balance:.4f} SOL, {len(wallet_info['tokens'])} tokens (including zero balances)")
            
            logger.debug(f"Wallet info retrieved: {sol_balance:.4f} SOL, {len(token_accounts)} tokens")
            return wallet_info
            
        except Exception as e:
            logger.error(f"Error getting wallet info: {e}")
            return {
                "address": self.wallet.get_public_key_str(),
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    async def get_balance_summary(self) -> Dict[str, Any]:
        """
        Get quick balance summary (SOL only for speed).
        Ultra-low latency endpoint.
        
        Returns:
            Dict with SOL balance
        """
        try:
            sol_balance = await self.wallet.get_sol_balance(use_cache=True)
            
            return {
                "address": self.wallet.get_public_key_str(),
                "sol_balance": sol_balance,
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Error getting balance summary: {e}")
            return {
                "address": self.wallet.get_public_key_str(),
                "error": str(e)
            }
    
    async def get_token_balance_info(self, mint: str) -> Dict[str, Any]:
        """
        Get balance for a specific token.
        
        Args:
            mint: Token mint address
        
        Returns:
            Token balance info
        """
        try:
            balance_info = await self.wallet.get_token_balance(mint)
            balance_info["timestamp"] = datetime.now().isoformat()
            return balance_info
        except Exception as e:
            logger.error(f"Error getting token balance for {mint}: {e}")
            return {
                "mint": mint,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
