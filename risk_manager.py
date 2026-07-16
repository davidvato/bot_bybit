"""
=============================================================================
risk_manager.py - Módulo de Gestión de Riesgo y Ejecución de Órdenes
=============================================================================
Implementa:
  - Cálculo dinámico del tamaño de posición (% del balance)
  - Stop Loss dinámico basado en ATR (1.5 * ATR)
  - Take Profit con ratio R/R mínimo de 1:2
  - Validación de posiciones existentes (anti-duplicado)
  - Lógica de cierre anticipado en caso de señal contraria
=============================================================================
"""

import logging
import math
from typing import Optional, Tuple

from config import BotConfig
from exchange import ExchangeClient
from logger import setup_logger
from strategies import ConsensusResult


class RiskManager:
    """
    Gestor de riesgo que controla el tamaño de posición, SL/TP y
    protección contra operaciones duplicadas.
    """

    def __init__(self, config: BotConfig, exchange: ExchangeClient) -> None:
        self.config   = config
        self.exchange = exchange
        self.logger   = setup_logger(__name__, config.log_file, config.get_log_level())
        self._instrument_info: Optional[dict] = None

    def _get_instrument_info(self) -> Optional[dict]:
        """Obtiene y cachea la información del instrumento (precisión, mín qty, etc.)."""
        if self._instrument_info is None:
            self._instrument_info = self.exchange.get_instrument_info()
        return self._instrument_info

    def _round_qty(self, qty: float, qty_step: float) -> float:
        """
        Redondea la cantidad al step permitido por Bybit.
        Bybit no acepta cantidades arbitrarias; deben ser múltiplos del qty_step.

        Ejemplo: qty=0.00372, qty_step=0.001 → resultado=0.003
        """
        if qty_step <= 0:
            return qty
        # Calcular cuántos decimales tiene el step
        decimals = max(0, -int(math.floor(math.log10(qty_step))))
        rounded = math.floor(qty / qty_step) * qty_step
        return round(rounded, decimals)

    def _round_price(self, price: float, tick_size: float) -> float:
        """Redondea el precio al tick size del instrumento."""
        if tick_size <= 0:
            return price
        decimals = max(0, -int(math.floor(math.log10(tick_size))))
        rounded = round(round(price / tick_size) * tick_size, decimals)
        return rounded

    def calculate_position_size(
        self,
        balance: float,
        entry_price: float,
        stop_loss_price: float,
    ) -> Tuple[float, float]:
        """
        Calcula el tamaño de posición basado en el riesgo por operación.

        Fórmula:
          Riesgo_USDT    = balance * risk_per_trade
          Distancia_SL   = |entry_price - stop_loss_price|
          Qty_contratos  = (Riesgo_USDT * leverage) / Distancia_SL

        Args:
            balance: Balance disponible en USDT.
            entry_price: Precio estimado de entrada (precio actual de mercado).
            stop_loss_price: Precio del Stop Loss calculado con ATR.

        Returns:
            Tuple (qty_redondeada, riesgo_usdt)
              qty_redondeada: Cantidad de contratos a operar (ya redondeada al step).
              riesgo_usdt: Monto en USDT que se está arriesgando en esta operación.
        """
        info = self._get_instrument_info()
        if not info:
            self.logger.error("❌ No se pudo obtener info del instrumento para calcular qty.")
            return 0.0, 0.0

        # Distancia al SL en USDT por contrato
        sl_distance = abs(entry_price - stop_loss_price)
        if sl_distance <= 0:
            self.logger.error("❌ Distancia SL es 0 o negativa. No se puede calcular qty.")
            return 0.0, 0.0

        # Monto a arriesgar en USDT
        risk_usdt = balance * self.config.risk_per_trade

        # Cantidad de contratos con apalancamiento:
        # (riesgo_usdt * leverage) / sl_distance da los contratos que
        # permiten perder exactamente risk_usdt si se activa el SL
        qty_raw = (risk_usdt * self.config.leverage) / sl_distance

        # Redondear al step mínimo del instrumento
        qty_rounded = self._round_qty(qty_raw, info["qty_step"])

        # Validar que supera el mínimo de Bybit
        if qty_rounded < info["min_order_qty"]:
            self.logger.warning(
                f"⚠️  Qty calculada ({qty_rounded}) es menor que el mínimo "
                f"({info['min_order_qty']}). Se usará el mínimo."
            )
            qty_rounded = info["min_order_qty"]

        self.logger.info(
            f"📐 Tamaño de posición calculado: "
            f"Balance={balance:.2f} USDT | "
            f"Riesgo={risk_usdt:.2f} USDT ({self.config.risk_per_trade*100:.1f}%) | "
            f"SL Distance={sl_distance:.4f} | "
            f"Qty={qty_rounded}"
        )

        return qty_rounded, risk_usdt

    def calculate_sl_tp(
        self,
        entry_price: float,
        atr: float,
        side: str,
    ) -> Tuple[float, float]:
        """
        Calcula Stop Loss y Take Profit dinámicos basados en ATR.

        Fórmulas:
          SL_distance = ATR * atr_sl_multiplier    (ej: ATR * 1.5)
          TP_distance = SL_distance * risk_reward   (ej: SL * 2.0 = 3 * ATR)

          Para LONG:
            SL = entry_price - SL_distance
            TP = entry_price + TP_distance

          Para SHORT:
            SL = entry_price + SL_distance
            TP = entry_price - TP_distance

        Args:
            entry_price: Precio de entrada a la posición.
            atr: Valor actual del ATR (14 periodos).
            side: 'Buy' para Long, 'Sell' para Short.

        Returns:
            Tuple (stop_loss_price, take_profit_price)
        """
        info = self._get_instrument_info()
        tick_size = info["tick_size"] if info else 0.5

        sl_distance = atr * self.config.atr_sl_multiplier
        tp_distance = sl_distance * self.config.risk_reward_ratio

        if side == "Buy":  # Long
            sl_price = entry_price - sl_distance
            tp_price = entry_price + tp_distance
        else:  # Short
            sl_price = entry_price + sl_distance
            tp_price = entry_price - tp_distance

        # Redondear al tick size del instrumento
        sl_price = self._round_price(sl_price, tick_size)
        tp_price = self._round_price(tp_price, tick_size)

        self.logger.info(
            f"🎯 SL/TP calculados ({side}): "
            f"Entrada={entry_price:.4f} | "
            f"ATR={atr:.4f} | "
            f"SL={sl_price:.4f} (-{sl_distance:.4f}) | "
            f"TP={tp_price:.4f} (+{tp_distance:.4f}) | "
            f"R/R=1:{self.config.risk_reward_ratio}"
        )

        return sl_price, tp_price

    def execute_signal(self, consensus: ConsensusResult) -> bool:
        """
        Punto de entrada principal para la ejecución de señales.
        Coordina la verificación de posiciones, cálculo de riesgo y envío de órdenes.

        Lógica de ejecución:
          1. Verificar si ya hay posición abierta
          2. Si hay posición con señal CONTRARIA → cerrar y abrir nueva (opcional)
          3. Si hay posición con misma dirección → ignorar (anti-duplicado)
          4. Si no hay posición → calcular SL/TP y abrir nueva posición

        Args:
            consensus: Resultado del sistema de consenso con señal validada.

        Returns:
            bool: True si se ejecutó alguna acción, False si no.
        """
        signal = consensus.final_signal

        # Señal de espera → no hacer nada
        if signal == "HOLD":
            self.logger.info("⏸️  Señal HOLD. Sin acción.")
            return False

        # --- Paso 1: Verificar posición existente (ANTI-DUPLICADO) ---
        open_position = self.exchange.get_open_position()

        if open_position:
            existing_side = open_position["side"]  # 'Buy' o 'Sell'
            expected_side = "Buy" if signal == "LONG" else "Sell"

            if existing_side == expected_side:
                # Ya tenemos una posición en la misma dirección → no duplicar
                self.logger.info(
                    f"🚫 POSICIÓN DUPLICADA EVITADA: Ya existe posición {existing_side}. "
                    f"Señal {signal} ignorada."
                )
                return False
            else:
                # Señal contraria a la posición existente → cerrar primero
                self.logger.info(
                    f"🔄 Señal contraria detectada. Cerrando posición {existing_side} "
                    f"antes de abrir {signal}..."
                )
                close_result = self.exchange.close_position(
                    existing_side, open_position["size"]
                )
                if not close_result:
                    self.logger.error(
                        "❌ No se pudo cerrar la posición existente. "
                        "Abortando nueva entrada para evitar sobreexposición."
                    )
                    return False

        # --- Paso 2: Obtener balance disponible ---
        balance = self.exchange.get_wallet_balance()
        if not balance or balance <= 0:
            self.logger.error("❌ Balance no disponible o es 0. No se puede operar.")
            return False

        # --- Paso 3: Determinar lado de la orden ---
        side = "Buy" if signal == "LONG" else "Sell"
        entry_price = consensus.current_price
        atr = consensus.atr

        if atr <= 0:
            self.logger.error(
                f"❌ ATR inválido ({atr}). No se puede calcular SL/TP dinámico."
            )
            return False

        # --- Paso 4: Calcular SL y TP dinámicos con ATR ---
        sl_price, tp_price = self.calculate_sl_tp(entry_price, atr, side)

        # Validación de seguridad: SL y TP deben ser precios positivos
        if sl_price <= 0 or tp_price <= 0:
            self.logger.error(
                f"❌ Precios SL/TP inválidos: SL={sl_price}, TP={tp_price}."
            )
            return False

        # --- Paso 5: Calcular tamaño de posición basado en riesgo ---
        qty, risk_usdt = self.calculate_position_size(balance, entry_price, sl_price)

        if qty <= 0:
            self.logger.error("❌ Cantidad calculada es 0. No se puede enviar la orden.")
            return False

        # --- Paso 6: Ejecutar la orden en el exchange ---
        self.logger.info(
            f"🚀 EJECUTANDO ORDEN {signal}: "
            f"Side={side} | Qty={qty} | Entry~{entry_price:.4f} | "
            f"SL={sl_price:.4f} | TP={tp_price:.4f} | "
            f"Riesgo={risk_usdt:.2f} USDT"
        )

        result = self.exchange.place_order(
            side=side,
            qty=qty,
            stop_loss=sl_price,
            take_profit=tp_price,
        )

        if result:
            self.logger.info(
                f"✅ ¡ORDEN {signal} EJECUTADA EXITOSAMENTE! "
                f"OrderID: {result.get('orderId', 'N/A')}"
            )
            return True
        else:
            self.logger.error(f"❌ FALLÓ la ejecución de la orden {signal}.")
            return False
