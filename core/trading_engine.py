from core.wallet_client import WalletClient, SOL_MINT
from core.dex.jupiter_client import JupiterClient
from loguru import logger
from typing import Optional, Dict, Any
from config import settings
import asyncio
from decimal import Decimal

class TradingEngine:
    """
    High-performance trading engine with buy/sell operations.
    Includes automatic profit conversion to USDC and cold wallet transfer.
    """
    
    def __init__(self, wallet: WalletClient):
        self.wallet = wallet
        self.jupiter = JupiterClient()
        self.initial_sol_balance: Optional[float] = None
        
    async def initialize(self):
        """Initialize trading engine"""
        await self.jupiter.initialize()
        
        # Record initial balance for profit tracking
        self.initial_sol_balance = await self.wallet.get_sol_balance()
        logger.info(f"💰 Initial SOL balance: {self.initial_sol_balance:.4f} SOL")
    
    async def close(self):
        """Close trading engine"""
        await self.jupiter.close()
    
    async def buy(
        self,
        mint: str,
        amount_sol: float,
        slippage_bps: Optional[int] = None,
        speed_mode: Optional[str] = None,
        max_retries: int = 3
    ) -> Optional[Dict[str, Any]]:
        """
        Buy a token with SOL.
        
        Args:
            mint: Token mint address to buy
            amount_sol: Amount of SOL to spend
            slippage_bps: Slippage tolerance (uses default if None)
            speed_mode: ULTRA_FAST, BALANCED, or SAFE
        
        Returns:
            Transaction result with signature and amounts
        """
        attempt = 1
        last_error: Optional[Exception] = None
        signature: Optional[str] = None

        logger.info(f"🛒 BUY: {amount_sol} SOL → {mint}")

        # Use defaults if not specified
        base_slippage_bps = settings.DEFAULT_SLIPPAGE_BPS
        speed_mode = speed_mode or settings.DEFAULT_SPEED_MODE
        # Compute dynamic slippage when not provided
        if slippage_bps is None:
            slippage_bps = await self.calculate_dynamic_slippage(
                mint=mint,
                trade_amount_sol=amount_sol,
                base_slippage_bps=base_slippage_bps
            )
        else:
            slippage_bps = slippage_bps

        # Convert SOL to lamports
        amount_lamports = int(amount_sol * 1e9)

        while attempt <= max_retries:
            wait_time = (2 ** (attempt - 1)) * 0.5 if attempt > 1 else 0
            try:
                logger.info(f"Attempt {attempt}/{max_retries} with slippage {slippage_bps} bps")
                if wait_time:
                    logger.info(f"Waiting {wait_time:.2f}s before retry")
                    await asyncio.sleep(wait_time)

                priority_fee = self._get_priority_fee(speed_mode)

                swap_data = await self.jupiter.execute_swap(
                    input_mint=SOL_MINT,
                    output_mint=mint,
                    amount=amount_lamports,
                    user_public_key=self.wallet.get_public_key_str(),
                    slippage_bps=slippage_bps,
                    priority_fee=priority_fee
                )

                if not swap_data:
                    raise RuntimeError("Failed to create swap from Jupiter")

                skip_preflight = (speed_mode == "ULTRA_FAST")
                signature = await self.wallet.send_transaction(
                    swap_data["transaction"],
                    skip_preflight=skip_preflight
                )

                if not signature:
                    raise RuntimeError("Failed to send transaction")

                # Wait for confirmation with timeout
                await self._wait_for_confirmation(signature)

                # Clear cache for updated balances
                self.wallet.clear_cache()

                result = {
                    "success": True,
                    "signature": signature,
                    "type": "BUY",
                    "mint": mint,
                    "input_amount": amount_sol,
                    "output_amount": swap_data["output_amount"],
                    "price_impact": swap_data["price_impact"],
                    "status": "confirmed",
                    "attempts": attempt
                }

                logger.info(f"✅ BUY confirmed on attempt {attempt}: {signature}")
                return result

            except Exception as e:
                last_error = e
                retryable = self._is_retryable_error(e)
                logger.warning(f"BUY attempt {attempt} failed ({'retryable' if retryable else 'non-retryable'}): {e}")

                if not retryable or attempt >= max_retries:
                    break

                attempt += 1
                continue

        logger.error(f"❌ BUY failed after {attempt} attempts: {last_error}")
        return {
            "success": False,
            "signature": signature,
            "type": "BUY",
            "mint": mint,
            "input_amount": amount_sol,
            "status": "failed",
            "attempts": attempt,
            "error": str(last_error) if last_error else "Unknown error"
        }
    
    async def sell(
        self,
        mint: str,
        amount: int,
        slippage_bps: Optional[int] = None,
        speed_mode: Optional[str] = None,
        max_retries: int = 3
    ) -> Optional[Dict[str, Any]]:
        """
        Sell a specific amount of tokens for SOL.
        
        Args:
            mint: Token mint address to sell
            amount: Amount of tokens to sell (in base units)
            slippage_bps: Slippage tolerance
            speed_mode: Speed mode
        
        Returns:
            Transaction result
        """
        attempt = 1
        last_error: Optional[Exception] = None
        signature: Optional[str] = None

        logger.info(f"💸 SELL: {amount} of {mint} → SOL")

        base_slippage_bps = settings.DEFAULT_SLIPPAGE_BPS
        speed_mode = speed_mode or settings.DEFAULT_SPEED_MODE

        if slippage_bps is None:
            # Estimate trade size in SOL for dynamic slippage calculation
            estimated_sol_amount = None
            try:
                sell_quote = await self.jupiter.get_quote(
                    input_mint=mint,
                    output_mint=SOL_MINT,
                    amount=amount,
                    slippage_bps=base_slippage_bps
                )
                if sell_quote and sell_quote.get("outAmount") is not None:
                    estimated_sol_amount = int(sell_quote.get("outAmount", 0)) / 1e9
            except Exception as e:
                logger.debug(f"Could not estimate sell quote for slippage: {e}")

            slippage_bps = await self.calculate_dynamic_slippage(
                mint=mint,
                trade_amount_sol=estimated_sol_amount or 0,
                base_slippage_bps=base_slippage_bps
            )
        else:
            slippage_bps = slippage_bps

        while attempt <= max_retries:
            wait_time = (2 ** (attempt - 1)) * 0.5 if attempt > 1 else 0
            try:
                logger.info(f"Attempt {attempt}/{max_retries} with slippage {slippage_bps} bps")
                if wait_time:
                    logger.info(f"Waiting {wait_time:.2f}s before retry")
                    await asyncio.sleep(wait_time)

                priority_fee = self._get_priority_fee(speed_mode)

                swap_data = await self.jupiter.execute_swap(
                    input_mint=mint,
                    output_mint=SOL_MINT,
                    amount=amount,
                    user_public_key=self.wallet.get_public_key_str(),
                    slippage_bps=slippage_bps,
                    priority_fee=priority_fee
                )

                if not swap_data:
                    raise RuntimeError("Failed to create swap from Jupiter")

                skip_preflight = (speed_mode == "ULTRA_FAST")
                signature = await self.wallet.send_transaction(
                    swap_data["transaction"],
                    skip_preflight=skip_preflight
                )

                if not signature:
                    raise RuntimeError("Failed to send transaction")

                await self._wait_for_confirmation(signature)

                self.wallet.clear_cache()

                result = {
                    "success": True,
                    "signature": signature,
                    "type": "SELL",
                    "mint": mint,
                    "input_amount": amount,
                    "output_amount_lamports": swap_data["output_amount"],
                    "output_amount_sol": swap_data["output_amount"] / 1e9,
                    "price_impact": swap_data["price_impact"],
                    "status": "confirmed",
                    "attempts": attempt
                }

                logger.info(f"✅ SELL confirmed on attempt {attempt}: {signature}")

                # Trigger profit conversion after successful sell
                asyncio.create_task(self.convert_profit_to_usdc())

                return result

            except Exception as e:
                last_error = e
                retryable = self._is_retryable_error(e)
                logger.warning(f"SELL attempt {attempt} failed ({'retryable' if retryable else 'non-retryable'}): {e}")

                if not retryable or attempt >= max_retries:
                    break

                attempt += 1
                continue

        logger.error(f"❌ SELL failed after {attempt} attempts: {last_error}")
        return {
            "success": False,
            "signature": signature,
            "type": "SELL",
            "mint": mint,
            "input_amount": amount,
            "status": "failed",
            "attempts": attempt,
            "error": str(last_error) if last_error else "Unknown error"
        }
    
    async def sell_all(
        self,
        mint: str,
        slippage_bps: Optional[int] = None,
        speed_mode: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Sell entire token balance.
        
        Args:
            mint: Token mint to sell
            slippage_bps: Slippage tolerance
            speed_mode: Speed mode
        
        Returns:
            Transaction result
        """
        try:
            logger.info(f"💰 SELL ALL: {mint}")
            
            # Get current token balance
            balance_info = await self.wallet.get_token_balance(mint)
            balance = balance_info.get("balance", 0)
            
            if balance == 0:
                logger.warning(f"No balance to sell for {mint}")
                return None
            
            logger.info(f"Selling entire balance: {balance}")
            
            # Execute sell
            return await self.sell(mint, balance, slippage_bps, speed_mode)
            
        except Exception as e:
            logger.error(f"Error in sell_all operation: {e}")
            return None
    
    async def convert_profit_to_usdc(self) -> Optional[Dict[str, Any]]:
        """
        Convert SOL profits to USDC if threshold is met.
        Called automatically after successful sells.
        
        Returns:
            Conversion result or None
        """
        try:
            if not settings.ENABLE_AUTO_PROFIT_CONVERSION:
                return None
            
            # Calculate profit
            current_balance = await self.wallet.get_sol_balance(use_cache=False)
            profit_sol = current_balance - self.initial_sol_balance
            
            if profit_sol < settings.PROFIT_THRESHOLD_SOL:
                logger.debug(f"Profit {profit_sol:.4f} SOL below threshold {settings.PROFIT_THRESHOLD_SOL}")
                return None
            
            logger.info(f"💎 Converting profit: {profit_sol:.4f} SOL → USDC")
            
            # Convert profit to USDC
            result = await self.buy(
                mint=settings.USDC_MINT,
                amount_sol=profit_sol,
                slippage_bps=50,  # Tighter slippage for stablecoin
                speed_mode="BALANCED"  # Don't need ultra fast for profit taking
            )
            
            if result:
                # Reset profit baseline
                self.initial_sol_balance = await self.wallet.get_sol_balance(use_cache=False)
                logger.info("✅ Profit converted to USDC")
                
                # Trigger cold wallet transfer check
                asyncio.create_task(self._check_cold_wallet_transfer())
            
            return result
            
        except Exception as e:
            logger.error(f"Error converting profit to USDC: {e}")
            return None
    
    async def _wait_for_confirmation(self, signature: str, timeout: int = 30) -> bool:
        """Wait for transaction confirmation with timeout"""
        try:
            confirmed = await self.wallet.confirm_transaction(signature, timeout=timeout)
            if confirmed:
                return True
            
            # Treat lack of confirmation as timeout to trigger retry
            raise asyncio.TimeoutError("Transaction not confirmed within timeout")
        except asyncio.TimeoutError as e:
            logger.warning(f"⏱️ Confirmation timeout for {signature}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error waiting for confirmation {signature}: {e}")
            raise
    
    async def calculate_dynamic_slippage(
        self,
        mint: str,
        trade_amount_sol: float,
        base_slippage_bps: int = 100
    ) -> int:
        """
        Calculate slippage based on price impact for a given trade size.
        """
        try:
            amount_lamports = int(Decimal(trade_amount_sol) * Decimal(1e9))
        except Exception:
            amount_lamports = 0
        
        try:
            quote = await self.jupiter.get_quote(
                input_mint=SOL_MINT,
                output_mint=mint,
                amount=amount_lamports,
                slippage_bps=base_slippage_bps
            )
            
            if not quote:
                logger.warning("⚠️ Quote failed for dynamic slippage, using 2x base")
                return min(base_slippage_bps * 2, 500)
            
            price_impact = float(quote.get("priceImpactPct", 0) or 0)
            
            if price_impact <= 1.0:
                adjusted_slippage = base_slippage_bps
            else:
                adjusted_slippage = int(base_slippage_bps * price_impact)
            
            adjusted_slippage = min(adjusted_slippage, 500)
            
            if adjusted_slippage != base_slippage_bps:
                logger.info(f"🧮 Adjusted slippage from {base_slippage_bps} -> {adjusted_slippage} bps (price impact {price_impact:.2f}%)")
            
            if price_impact > 3.0:
                logger.warning(f"⚠️ High price impact detected ({price_impact:.2f}%), consider reducing size")
            
            return adjusted_slippage
        except Exception as e:
            logger.warning(f"⚠️ Dynamic slippage calculation failed, using 2x base: {e}")
            return min(base_slippage_bps * 2, 500)
    
    async def _check_cold_wallet_transfer(self):
        """
        Check if USDC balance exceeds threshold and transfer to cold wallet.
        """
        try:
            if not settings.AUTO_TRANSFER_TO_COLD_WALLET:
                return
            
            if not settings.COLD_WALLET_ADDRESS:
                logger.warning("Cold wallet address not configured")
                return
            
            # Get USDC balance
            usdc_balance_info = await self.wallet.get_token_balance(settings.USDC_MINT)
            usdc_balance = usdc_balance_info.get("ui_amount", 0)
            
            if usdc_balance < settings.TRANSFER_THRESHOLD_USDC:
                logger.debug(f"USDC balance {usdc_balance:.2f} below transfer threshold")
                return
            
            logger.info(f"💎 Transferring {usdc_balance:.2f} USDC to cold wallet")
            
            # TODO: Implement SPL token transfer to cold wallet
            # This requires building a token transfer instruction
            logger.warning("Cold wallet transfer not yet implemented - requires SPL token transfer")
            
        except Exception as e:
            logger.error(f"Error in cold wallet transfer: {e}")
    
    async def _handle_confirmation(self, signature: str, tx_type: str, mint: str):
        """Handle transaction confirmation in background"""
        try:
            confirmed = await self.wallet.confirm_transaction(signature)
            if confirmed:
                logger.info(f"✅ {tx_type} confirmed: {signature}")
            else:
                logger.error(f"❌ {tx_type} failed: {signature}")
        except Exception as e:
            logger.error(f"Error confirming {tx_type}: {e}")
    
    async def _handle_sell_confirmation(self, signature: str, mint: str, swap_data: Dict):
        """Handle sell confirmation and trigger profit conversion"""
        try:
            confirmed = await self.wallet.confirm_transaction(signature)
            if confirmed:
                logger.info(f"✅ SELL confirmed: {signature}")
                
                # Check for profit conversion
                await self.convert_profit_to_usdc()
            else:
                logger.error(f"❌ SELL failed: {signature}")
        except Exception as e:
            logger.error(f"Error confirming SELL: {e}")
    
    def _get_priority_fee(self, speed_mode: str) -> int:
        """Get priority fee based on speed mode"""
        if speed_mode == "ULTRA_FAST":
            return settings.PRIORITY_FEE_ULTRA_FAST
        elif speed_mode == "BALANCED":
            return settings.PRIORITY_FEE_BALANCED
        elif speed_mode == "SAFE":
            return settings.PRIORITY_FEE_SAFE
        else:
            return settings.PRIORITY_FEE_BALANCED
    
    def _is_retryable_error(self, error: Exception) -> bool:
        """Determine if an error should trigger a retry"""
        if isinstance(error, asyncio.TimeoutError):
            return True
        
        message = str(error).lower()
        
        non_retryable_markers = [
            "insufficient",
            "balance",
            "slippage",
            "invalid",
            "parameter",
            "not enough",
            "exceed"
        ]
        if any(marker in message for marker in non_retryable_markers):
            return False
        
        retryable_markers = [
            "timeout",
            "temporar",
            "unavailable",
            "gateway",
            "5xx",
            "500",
            "502",
            "503",
            "504",
            "network"
        ]
        if any(marker in message for marker in retryable_markers):
            return True
        
        # Default to retry if not explicitly non-retryable
        return True
