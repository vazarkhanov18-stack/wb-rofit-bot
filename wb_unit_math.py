from __future__ import annotations


def manual_warehouse_costs(
    length_cm: float,
    width_cm: float,
    height_cm: float,
    first_liter_cost: float,
    additional_liter_cost: float,
    *,
    storage_first_liter: float = 0.0,
    storage_additional_liter: float = 0.0,
    storage_days: float = 0.0,
) -> tuple[float, float, float]:
    """Return exact volume, outbound logistics, and storage without liter rounding."""
    volume_liters = max(0.0, float(length_cm) * float(width_cm) * float(height_cm) / 1000.0)
    extra_liters = max(0.0, volume_liters - 1.0)
    logistics = float(first_liter_cost) + extra_liters * float(additional_liter_cost)
    storage_per_day = float(storage_first_liter) + extra_liters * float(storage_additional_liter)
    storage_total = storage_per_day * max(0.0, float(storage_days))
    return volume_liters, logistics, storage_total
