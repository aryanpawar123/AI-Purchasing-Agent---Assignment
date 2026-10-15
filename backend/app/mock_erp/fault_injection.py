"""
Fault injection configuration for the mock ERP.

Faults are toggled per-scenario or per-request to exercise the agent's
verification and recovery loop. Each fault type simulates a realistic
ERP misbehaviour that the agent must detect and handle.

Usage:
    from app.mock_erp.fault_injection import faults
    faults.enable("PARTIAL_ACCEPT", {"accepted_fraction": 0.5})
    # ... later ...
    faults.disable("PARTIAL_ACCEPT")
    faults.reset()  # clear all
"""

from typing import Any


class FaultConfig:
    """
    Thread-safe(ish) fault injection state.

    In a real system you'd use contextvars for per-request state,
    but for a single-threaded demo/test setup, module-level state is fine.
    """

    # All supported fault types and their default params
    SUPPORTED = {
        "PARTIAL_ACCEPT",       # PO created with lower qty than requested
        "ROUND_TO_CASEPACK",    # qty silently rounded up to case-pack multiple
        "ETA_SLIP",             # eta later than requested
        "PRICE_CHANGE",         # unit price differs from quoted price
        "REJECT_BUDGET",        # ERP rejects due to concurrent budget consumption
    }

    def __init__(self):
        self._active: dict[str, dict[str, Any]] = {}

    def enable(self, fault: str, params: dict[str, Any] | None = None) -> None:
        """Enable a fault with optional parameters."""
        if fault not in self.SUPPORTED:
            raise ValueError(f"Unknown fault: {fault}. Supported: {self.SUPPORTED}")
        self._active[fault] = params or {}

    def disable(self, fault: str) -> None:
        """Disable a specific fault."""
        self._active.pop(fault, None)

    def reset(self) -> None:
        """Clear all active faults."""
        self._active.clear()

    def is_active(self, fault: str) -> bool:
        return fault in self._active

    def get_params(self, fault: str) -> dict[str, Any]:
        return self._active.get(fault, {})

    def list_active(self) -> list[str]:
        return list(self._active.keys())


# Module-level singleton
faults = FaultConfig()
