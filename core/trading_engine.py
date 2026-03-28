from core.wallet_client import WalletClient, SOL_MINT
from core.dex.jupiter_client import JupiterClient
from loguru import logger
from typing import Optional, Dict, Any
from config import settings
import asyncio
from decimal import Decimal
from solders.pubkey import Pubkey

USDC_DECIMALS = 6

class TradingEngine:
    """
    High-performance trading engine with buy/sell operations.
    Includes automatic profit conversion to USDC and cold wallet transfer.
    """
    
    def __init__(self, wallet: WalletClient):
        self.wallet = wallet
        self.jupiter = JupiterClient()
        self.initial_sol_balance: Optional[float] = None
        self._background_tasks = set()
        
    async def initialize(self):
        """Initialize trading engine"""
        await self.jupiter.initialize()
        
        # Record initial balance for profit tracking
        self.initial_sol_balance = await self.wallet.get_sol_balance()
        logger.info(f"💰 Initial SOL balance: {self.initial_sol_balance:.4f} SOL")
    
    async def close(self):
        """Close trading engine"""
        for task in list(self._background_tasks):
            task.cancel()
        self._background_tasks.clear()
        await self.jupiter.close()
    
    async def buy(
        self,
        mint: str,
        amount_sol: Optional[float] = None,
        slippage_bps: Optional[int] = None,
        speed_mode: Optional[str] = None,
        max_retries: int = 3,
        spend_denom: str = "SOL",
        amount: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Buy a token with the selected spend denomination (SOL or USDC).
        
        Args:
            mint: Token mint address to buy
            amount_sol: Deprecated alias for spend amount (kept for compatibility)
            slippage_bps: Slippage tolerance (uses default if None)
            speed_mode: ULTRA_FAST, BALANCED, or SAFE
            spend_denom: Spend denomination (SOL or USDC)
            amount: Spend amount in spend_denom units
        
        Returns:
            Transaction result with signature and amounts
        """
        attempt = 1
        last_error: Optional[Exception] = None
        signature: Optional[str] = None
        before_token_balance: Optional[int] = None
        before_sol_balance: Optional[float] = None
        spend_amount: float = 0.0
        normalized_spend_denom = "SOL"
        input_mint = SOL_MINT

        logger.info(f"🛒 BUY request: mint={mint} amount={amount if amount is not None else amount_sol} denom={spend_denom}")
        try:
            mint = self._validate_mint(mint)
            self._ensure_not_sol_mint(mint, operation="buy")
            if amount is not None and amount_sol is not None and abs(float(amount) - float(amount_sol)) > 1e-12:
                raise ValueError("Provide either amount or amount_sol (or ensure they match)")
            raw_amount = amount if amount is not None else amount_sol
            if raw_amount is None:
                raise ValueError("Buy amount is required")
            spend_amount = float(raw_amount)
            if spend_amount <= 0:
                raise ValueError("Buy amount must be > 0")
            normalized_spend_denom = self._normalize_spend_denom(spend_denom)
            input_mint = self._input_mint_for_spend_denom(normalized_spend_denom)
            if mint == input_mint:
                raise ValueError(f"Cannot buy {mint} using the same spend asset ({normalized_spend_denom})")
            speed_mode = self._normalize_speed_mode(speed_mode)
            amount_in_base_units = self._spend_amount_to_base_units(spend_amount, normalized_spend_denom)
            await self._ensure_buy_balance(spend_amount, normalized_spend_denom)
            before_sol_balance, before_token_balance = await self._get_fresh_route_balances(mint)

            # Use defaults if not specified
            base_slippage_bps = settings.DEFAULT_SLIPPAGE_BPS
            if slippage_bps is None:
                slippage_bps = await self.calculate_dynamic_slippage_for_route(
                    input_mint=input_mint,
                    output_mint=mint,
                    amount=amount_in_base_units,
                    base_slippage_bps=base_slippage_bps,
                )
            else:
                slippage_bps = self._normalize_slippage_bps(slippage_bps)
        except Exception as e:
            logger.error(f"❌ BUY validation failed: {e}")
            return {
                "success": False,
                "signature": None,
                "type": "BUY",
                "mint": mint,
                "input_amount": spend_amount,
                "input_denom": normalized_spend_denom,
                "status": "failed",
                "attempts": 0,
                "error": str(e),
            }

        while attempt <= max_retries:
            wait_time = (2 ** (attempt - 1)) * 0.5 if attempt > 1 else 0
            try:
                logger.info(f"Attempt {attempt}/{max_retries} with slippage {slippage_bps} bps")
                if wait_time:
                    logger.info(f"Waiting {wait_time:.2f}s before retry")
                    await asyncio.sleep(wait_time)

                priority_fee = self._get_priority_fee(speed_mode)

                swap_data = await self.jupiter.execute_swap(
                    input_mint=input_mint,
                    output_mint=mint,
                    amount=amount_in_base_units,
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
                    "input_amount": spend_amount,
                    "input_denom": normalized_spend_denom,
                    "output_amount": swap_data["output_amount"],
                    "price_impact": swap_data["price_impact"],
                    "status": "confirmed",
                    "attempts": attempt
                }

                logger.info(f"✅ BUY confirmed on attempt {attempt}: {signature}")
                return result

            except Exception as e:
                last_error = e
                reconciled = await self._reconcile_buy_success(
                    mint=mint,
                    amount_spend=spend_amount,
                    spend_denom=normalized_spend_denom,
                    before_sol_balance=before_sol_balance,
                    before_token_balance=before_token_balance,
                    signature=signature,
                    attempt=attempt,
                    error=e,
                )
                if reconciled:
                    return reconciled
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
            "input_amount": spend_amount,
            "input_denom": normalized_spend_denom,
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
        before_token_balance: Optional[int] = None
        before_sol_balance: Optional[float] = None

        logger.info(f"💸 SELL: {amount} of {mint} → SOL")
        try:
            mint = self._validate_mint(mint)
            self._ensure_not_sol_mint(mint, operation="sell")
            if amount <= 0:
                raise ValueError("Sell amount must be > 0")
            speed_mode = self._normalize_speed_mode(speed_mode)
            await self._ensure_sell_balance(mint=mint, amount=amount)
            before_sol_balance, before_token_balance = await self._get_fresh_route_balances(mint)

            base_slippage_bps = settings.DEFAULT_SLIPPAGE_BPS
            if slippage_bps is None:
                slippage_bps = await self.calculate_dynamic_slippage_for_route(
                    input_mint=mint,
                    output_mint=SOL_MINT,
                    amount=amount,
                    base_slippage_bps=base_slippage_bps,
                )
            else:
                slippage_bps = self._normalize_slippage_bps(slippage_bps)
        except Exception as e:
            logger.error(f"❌ SELL validation failed: {e}")
            return {
                "success": False,
                "signature": None,
                "type": "SELL",
                "mint": mint,
                "input_amount": amount,
                "status": "failed",
                "attempts": 0,
                "error": str(e),
            }

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
                self._schedule_background_task(
                    self.convert_profit_to_usdc(),
                    "convert_profit_to_usdc",
                )

                return result

            except Exception as e:
                last_error = e
                reconciled = await self._reconcile_sell_success(
                    mint=mint,
                    amount=amount,
                    before_sol_balance=before_sol_balance,
                    before_token_balance=before_token_balance,
                    signature=signature,
                    attempt=attempt,
                    error=e,
                )
                if reconciled:
                    self._schedule_background_task(
                        self.convert_profit_to_usdc(),
                        "convert_profit_to_usdc",
                    )
                    return reconciled
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
            if self.initial_sol_balance is None:
                self.initial_sol_balance = current_balance
                return None

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
            
            if result and result.get("success", False):
                # Reset profit baseline
                self.initial_sol_balance = await self.wallet.get_sol_balance(use_cache=False)
                logger.info("✅ Profit converted to USDC")
                
                # Trigger cold wallet transfer check
                self._schedule_background_task(
                    self._check_cold_wallet_transfer(),
                    "check_cold_wallet_transfer",
                )
            
            return result
            
        except Exception as e:
            logger.error(f"Error converting profit to USDC: {e}")
            return None

    async def convert_sol_to_usdc(
        self,
        amount_sol: Optional[float] = None,
        min_reserve_sol: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Convert SOL balance to USDC.

        If amount_sol is omitted, converts all SOL above min_reserve_sol.
        """
        try:
            reserve_sol = settings.SOL_TO_USDC_MIN_RESERVE_SOL if min_reserve_sol is None else float(min_reserve_sol)
            reserve_sol = max(0.0, reserve_sol)

            current_balance = await self.wallet.get_sol_balance(use_cache=False)
            if amount_sol is None:
                convert_amount_sol = max(0.0, current_balance - reserve_sol)
            else:
                convert_amount_sol = float(amount_sol)
                if convert_amount_sol <= 0:
                    return {"success": False, "error": "amount_sol must be > 0"}
                if current_balance - convert_amount_sol < reserve_sol:
                    return {
                        "success": False,
                        "error": "insufficient SOL after reserve",
                        "balance_sol": current_balance,
                        "requested_amount_sol": convert_amount_sol,
                        "min_reserve_sol": reserve_sol,
                    }

            if convert_amount_sol <= 0:
                return {
                    "success": False,
                    "error": "no SOL available above reserve",
                    "balance_sol": current_balance,
                    "min_reserve_sol": reserve_sol,
                }

            logger.info(
                f"💱 Converting SOL balance to USDC: {convert_amount_sol:.6f} SOL (reserve {reserve_sol:.6f})"
            )
            result = await self.buy(
                mint=settings.USDC_MINT,
                amount_sol=convert_amount_sol,
                slippage_bps=50,
                speed_mode="BALANCED",
            )

            if result and result.get("success", False):
                self.initial_sol_balance = await self.wallet.get_sol_balance(use_cache=False)
                self._schedule_background_task(
                    self._check_cold_wallet_transfer(),
                    "check_cold_wallet_transfer",
                )
                result["converted_amount_sol"] = convert_amount_sol
                result["min_reserve_sol"] = reserve_sol
                return result

            return result or {"success": False, "error": "conversion failed"}
        except Exception as e:
            logger.error(f"Error converting SOL to USDC: {e}")
            return {"success": False, "error": str(e)}

    def _schedule_background_task(self, coro, name: str) -> None:
        """Create tracked background tasks with exception logging."""
        task = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)

        def _done_callback(done_task):
            self._background_tasks.discard(done_task)
            if done_task.cancelled():
                return
            exc = done_task.exception()
            if exc is not None:
                logger.error(f"Background task '{name}' failed: {exc}")

        task.add_done_callback(_done_callback)
    
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

    async def _get_fresh_route_balances(self, mint: str) -> tuple[float, int]:
        """Read uncached SOL + token balances for reconciliation checks."""
        self.wallet.clear_cache()
        sol_balance = await self.wallet.get_sol_balance(use_cache=False)
        token_info = await self.wallet.get_token_balance(mint)
        token_balance = int(token_info.get("balance", 0) or 0)
        return sol_balance, token_balance

    async def _reconcile_buy_success(
        self,
        mint: str,
        amount_spend: float,
        spend_denom: str,
        before_sol_balance: Optional[float],
        before_token_balance: Optional[int],
        signature: Optional[str],
        attempt: int,
        error: Exception,
    ) -> Optional[Dict[str, Any]]:
        """
        Reconcile buy outcome after uncertain failures (timeouts/send failures).
        Marks success if token balance increased despite API-level failure.
        """
        if before_token_balance is None:
            return None
        if not self._is_reconciliation_candidate(error):
            return None

        await asyncio.sleep(1.0)
        after_sol_balance, after_token_balance = await self._get_fresh_route_balances(mint)
        token_delta = after_token_balance - before_token_balance

        if token_delta <= 0:
            return None

        sol_spent = 0.0
        if before_sol_balance is not None:
            sol_spent = max(0.0, before_sol_balance - after_sol_balance)

        logger.warning(
            f"⚠️ BUY reconciliation marked success after error '{error}': "
            f"token balance increased by {token_delta}"
        )
        return {
            "success": True,
            "signature": signature,
            "type": "BUY",
            "mint": mint,
            "input_amount": amount_spend,
            "input_denom": spend_denom,
            "output_amount": token_delta,
            "price_impact": None,
            "status": "confirmed_via_reconciliation",
            "attempts": attempt,
            "reconciliation": {
                "token_delta": token_delta,
                "sol_spent": sol_spent,
            },
        }

    async def _reconcile_sell_success(
        self,
        mint: str,
        amount: int,
        before_sol_balance: Optional[float],
        before_token_balance: Optional[int],
        signature: Optional[str],
        attempt: int,
        error: Exception,
    ) -> Optional[Dict[str, Any]]:
        """
        Reconcile sell outcome after uncertain failures (timeouts/send failures).
        Marks success if token balance decreased despite API-level failure.
        """
        if before_token_balance is None:
            return None
        if not self._is_reconciliation_candidate(error):
            return None

        await asyncio.sleep(1.0)
        after_sol_balance, after_token_balance = await self._get_fresh_route_balances(mint)
        token_delta = max(0, before_token_balance - after_token_balance)
        if token_delta <= 0:
            return None

        sol_gained = 0.0
        if before_sol_balance is not None:
            sol_gained = max(0.0, after_sol_balance - before_sol_balance)

        logger.warning(
            f"⚠️ SELL reconciliation marked success after error '{error}': "
            f"token balance decreased by {token_delta}"
        )
        return {
            "success": True,
            "signature": signature,
            "type": "SELL",
            "mint": mint,
            "input_amount": amount,
            "output_amount_lamports": int(sol_gained * 1e9),
            "output_amount_sol": sol_gained,
            "price_impact": None,
            "status": "confirmed_via_reconciliation",
            "attempts": attempt,
            "reconciliation": {
                "token_delta": token_delta,
                "sold_amount_requested": amount,
            },
        }
    
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

        return await self.calculate_dynamic_slippage_for_route(
            input_mint=SOL_MINT,
            output_mint=mint,
            amount=amount_lamports,
            base_slippage_bps=base_slippage_bps,
        )

    async def calculate_dynamic_slippage_for_route(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        base_slippage_bps: int = 100,
    ) -> int:
        """Calculate slippage based on quote price impact for a specific route."""
        try:
            quote = await self.jupiter.get_quote(
                input_mint=input_mint,
                output_mint=output_mint,
                amount=max(0, int(amount)),
                slippage_bps=base_slippage_bps
            )

            if not quote:
                logger.warning("⚠️ Quote failed for dynamic slippage, using 2x base")
                return min(base_slippage_bps * 2, settings.MAX_SLIPPAGE_BPS)

            price_impact = float(quote.get("priceImpactPct", 0) or 0)

            if price_impact <= 1.0:
                adjusted_slippage = base_slippage_bps
            else:
                adjusted_slippage = int(base_slippage_bps * price_impact)

            adjusted_slippage = min(adjusted_slippage, settings.MAX_SLIPPAGE_BPS)

            if adjusted_slippage != base_slippage_bps:
                logger.info(
                    f"🧮 Adjusted slippage from {base_slippage_bps} -> {adjusted_slippage} bps "
                    f"(price impact {price_impact:.2f}%)"
                )

            if price_impact > 3.0:
                logger.warning(f"⚠️ High price impact detected ({price_impact:.2f}%), consider reducing size")

            return adjusted_slippage
        except Exception as e:
            logger.warning(f"⚠️ Dynamic slippage calculation failed, using 2x base: {e}")
            return min(base_slippage_bps * 2, settings.MAX_SLIPPAGE_BPS)
    
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

    def _is_reconciliation_candidate(self, error: Exception) -> bool:
        """Errors where on-chain state may have changed despite API failure."""
        if isinstance(error, asyncio.TimeoutError):
            return True
        message = str(error).lower()
        return "timeout" in message or "failed to send transaction" in message

    def _validate_mint(self, mint: str) -> str:
        mint = (mint or "").strip()
        if not mint:
            raise ValueError("Mint is required")
        try:
            Pubkey.from_string(mint)
        except Exception as exc:
            raise ValueError(f"Invalid mint address: {mint}") from exc
        return mint

    def _ensure_not_sol_mint(self, mint: str, operation: str) -> None:
        if mint == SOL_MINT:
            raise ValueError(f"Cannot {operation} SOL via token swap route")

    def _normalize_speed_mode(self, speed_mode: Optional[str]) -> str:
        mode = (speed_mode or settings.DEFAULT_SPEED_MODE or "BALANCED").strip().upper()
        allowed = {"ULTRA_FAST", "BALANCED", "SAFE"}
        if mode not in allowed:
            raise ValueError(f"Invalid speed_mode '{mode}'. Allowed: ULTRA_FAST, BALANCED, SAFE")
        return mode

    def _normalize_slippage_bps(self, slippage_bps: int) -> int:
        value = int(slippage_bps)
        if value <= 0:
            raise ValueError("slippage_bps must be > 0")
        if value > settings.MAX_SLIPPAGE_BPS:
            raise ValueError(f"slippage_bps exceeds max allowed ({settings.MAX_SLIPPAGE_BPS})")
        return value

    def _normalize_spend_denom(self, spend_denom: str) -> str:
        denom = (spend_denom or "SOL").strip().upper()
        allowed = {"SOL", "USDC"}
        if denom not in allowed:
            raise ValueError(f"Invalid spend_denom '{denom}'. Allowed: SOL, USDC")
        return denom

    def _input_mint_for_spend_denom(self, spend_denom: str) -> str:
        if spend_denom == "SOL":
            return SOL_MINT
        usdc_mint = self._validate_mint(settings.USDC_MINT)
        return usdc_mint

    def _spend_amount_to_base_units(self, amount: float, spend_denom: str) -> int:
        if spend_denom == "SOL":
            return self._sol_to_lamports(amount)
        try:
            usdc_amount = Decimal(str(amount))
        except Exception as exc:
            raise ValueError("amount must be a valid number") from exc
        if usdc_amount <= 0:
            raise ValueError("amount must be > 0")
        units = int(usdc_amount * Decimal(10 ** USDC_DECIMALS))
        if units <= 0:
            raise ValueError("amount is too small for USDC precision")
        return units

    def _sol_to_lamports(self, amount_sol: float) -> int:
        try:
            amount = Decimal(str(amount_sol))
        except Exception as exc:
            raise ValueError("amount_sol must be a valid number") from exc
        if amount <= 0:
            raise ValueError("amount_sol must be > 0")
        lamports = int(amount * Decimal(1e9))
        if lamports <= 0:
            raise ValueError("amount_sol is too small (must be >= 1 lamport)")
        return lamports

    async def _ensure_buy_balance(self, amount: float, spend_denom: str) -> None:
        balance_sol = await self.wallet.get_sol_balance(use_cache=False)
        if spend_denom == "SOL":
            required_total = float(amount) + float(settings.MIN_FEE_RESERVE_SOL)
            if balance_sol < required_total:
                raise ValueError(
                    f"Insufficient SOL balance: need {required_total:.6f} SOL "
                    f"(including reserve {settings.MIN_FEE_RESERVE_SOL:.6f}), have {balance_sol:.6f}"
                )
            return

        if balance_sol < float(settings.MIN_FEE_RESERVE_SOL):
            raise ValueError(
                f"Insufficient SOL for transaction fees: need at least {settings.MIN_FEE_RESERVE_SOL:.6f} SOL, "
                f"have {balance_sol:.6f}"
            )
        usdc_mint = self._input_mint_for_spend_denom("USDC")
        balance_info = await self.wallet.get_token_balance(usdc_mint)
        current_ui = float(balance_info.get("ui_amount", 0) or 0.0)
        if current_ui <= 0:
            raw_balance = int(balance_info.get("balance", 0) or 0)
            decimals = int(balance_info.get("decimals", USDC_DECIMALS) or USDC_DECIMALS)
            if decimals < 0:
                decimals = USDC_DECIMALS
            current_ui = raw_balance / float(10 ** decimals)
        if current_ui + 1e-9 < float(amount):
            raise ValueError(
                f"Insufficient USDC balance: need {float(amount):.6f} USDC, have {current_ui:.6f}"
            )

    async def _ensure_sell_balance(self, mint: str, amount: int) -> None:
        balance_info = await self.wallet.get_token_balance(mint)
        current_balance = int(balance_info.get("balance", 0) or 0)
        if current_balance < int(amount):
            raise ValueError(
                f"Insufficient token balance for {mint}: need {amount}, have {current_balance}"
            )
