"""按路段开始时刻选取小时预报；不将缺测样本视为晴天。"""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class TravelWeather:
    forecast_minutes: tuple[float, ...]
    effects: tuple[dict, ...]
    peak_effects: tuple[dict, ...]
    reference_minute: float
    base_weekday: int
    peak_hours: tuple[tuple[float, float], ...]

    def modifiers(self, departures: np.ndarray, mode: str):
        times = np.asarray(self.forecast_minutes)
        indices = np.searchsorted(times, departures, side="left")
        selected = np.minimum(indices, len(times) - 1)
        covered = ((indices < len(times)) & (departures >= self.reference_minute)
                   & (times[selected] - departures <= 60))
        clock = departures % 1440
        weekday = (self.base_weekday + np.floor(departures / 1440).astype(int)) % 7
        peak = np.zeros(len(departures), dtype=bool)
        for start, end in self.peak_hours:
            peak |= (clock >= start) & (clock <= end)
        peak &= weekday < 5

        def values(key, default):
            normal = np.array([entry.get(key, default) for entry in self.effects])
            rush = np.array([entry.get(key, default) for entry in self.peak_effects])
            return np.where(peak, rush[selected], normal[selected])

        factor = np.where(covered, values(f"{mode}_factor", 1.0), 1.0)
        wait = np.where(covered, values(f"{mode}_wait_add_min", 0.0), 0.0)
        blocked = covered & ~values("bike_available", True) if mode in ("bike", "ebike") else np.zeros_like(covered)
        return factor, wait, blocked, covered
