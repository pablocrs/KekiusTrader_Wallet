import aiohttp
import asyncio
from loguru import logger
from typing import Optional, Dict, Any
from config import settings
import base64
import json

class JupiterClient:
    """
    Jupiter Aggregator client for optimal swap routing.
    Provides best price discovery across Solana DEXes.
    """
    
    def __init__(self):
        self.api_url = settings.JUPITER_API_URL
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def initialize(self):
        """Initialize HTTP session"""
        if not self.session:
            self.session = aiohttp.ClientSession()
            logger.info("🪐 Jupiter client initialized")
    
    async def close(self):
        """Close HTTP session"""
        if self.session:
            await self.session.close()
            logger.info("Jupiter client closed")
    
    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 100
    ) -> Optional[Dict[str, Any]]:
        """
        Get quote from Jupiter for a swap.
        
        Args:
            input_mint: Input token mint address
            output_mint: Output token mint address
            amount: Amount in base units (lamports/token decimals)
            slippage_bps: Slippage tolerance in basis points (100 = 1%)
        
        Returns:
            Quote data with route information
        """
        try:
            url = f"{self.api_url}/quote"
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount),
                "slippageBps": slippage_bps,
                "onlyDirectRoutes": False,  # Allow split routes for best price
                "asLegacyTransaction": False  # Use v0 transactions
            }
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    quote = await response.json()
                    logger.debug(f"Jupiter quote: {amount} → {quote.get('outAmount', 0)}")
                    return quote
                else:
                    error_text = await response.text()
                    logger.error(f"Jupiter quote failed: {response.status} - {error_text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting Jupiter quote: {e}")
            return None
    
    async def get_swap_transaction(
        self,
        quote: Dict[str, Any],
        user_public_key: str,
        priority_fee: Optional[int] = None,
        dynamic_compute_units: bool = True
    ) -> Optional[str]:
        """
        Get swap transaction from Jupiter quote.
        
        Args:
            quote: Quote data from get_quote()
            user_public_key: User's wallet public key
            priority_fee: Priority fee in microlamports (optional)
            dynamic_compute_units: Enable dynamic compute unit calculation
        
        Returns:
            Base64 encoded transaction
        """
        try:
            url = f"{self.api_url}/swap"
            
            payload = {
                "quoteResponse": quote,
                "userPublicKey": user_public_key,
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": dynamic_compute_units,
                "asLegacyTransaction": False
            }
            
            # Add priority fee if specified for ULTRA_FAST mode
            if priority_fee is not None:
                payload["prioritizationFeeLamports"] = priority_fee
            
            async with self.session.post(url, json=payload) as response:
                if response.status == 200:
                    swap_data = await response.json()
                    transaction = swap_data.get("swapTransaction")
                    logger.debug("✅ Jupiter swap transaction created")
                    return transaction
                else:
                    error_text = await response.text()
                    logger.error(f"Jupiter swap failed: {response.status} - {error_text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting swap transaction: {e}")
            return None
    
    async def execute_swap(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        user_public_key: str,
        slippage_bps: int = 100,
        priority_fee: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Complete swap flow: Get quote + build transaction.
        Returns transaction ready to sign.
        
        Args:
            input_mint: Input token mint address
            output_mint: Output token mint address  
            amount: Amount in base units
            user_public_key: Wallet public key
            slippage_bps: Slippage in basis points
            priority_fee: Priority fee in microlamports
        
        Returns:
            Dict with quote and transaction data
        """
        # Get quote
        quote = await self.get_quote(input_mint, output_mint, amount, slippage_bps)
        if not quote:
            logger.error("Failed to get Jupiter quote")
            return None
        
        # Get swap transaction
        transaction = await self.get_swap_transaction(
            quote, 
            user_public_key,
            priority_fee
        )
        
        if not transaction:
            logger.error("Failed to get swap transaction")
            return None
        
        return {
            "quote": quote,
            "transaction": transaction,
            "input_amount": amount,
            "output_amount": int(quote.get("outAmount", 0)),
            "price_impact": quote.get("priceImpactPct", 0)
        }


# Utility function for quick swaps
async def create_jupiter_swap(
    input_mint: str,
    output_mint: str,
    amount: int,
    wallet_pubkey: str,
    slippage_bps: int = 100,
    speed_mode: str = "ULTRA_FAST"
) -> Optional[Dict[str, Any]]:
    """
    Convenience function to create a Jupiter swap.
    """
    client = JupiterClient()
    await client.initialize()
    
    try:
        # Get priority fee based on speed mode
        priority_fee = None
        if speed_mode == "ULTRA_FAST":
            priority_fee = settings.PRIORITY_FEE_ULTRA_FAST
        elif speed_mode == "BALANCED":
            priority_fee = settings.PRIORITY_FEE_BALANCED
        elif speed_mode == "SAFE":
            priority_fee = settings.PRIORITY_FEE_SAFE
        
        result = await client.execute_swap(
            input_mint,
            output_mint,
            amount,
            wallet_pubkey,
            slippage_bps,
            priority_fee
        )
        return result
    finally:
        await client.close()
