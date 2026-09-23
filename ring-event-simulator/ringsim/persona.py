"""Daily routines for a simulated resident."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

WEEKDAY = "weekday"
WEEKEND = "weekend"

ASLEEP = "asleep"


@dataclass(frozen=True)
class RoutineStep:
    """One stretch of the day spent in a single zone.

    Times are minutes after local midnight. Jitter is the standard deviation
    applied when a concrete day is generated.
    """

    name: str
    zone: str
    start_min: int
    start_jitter: float
    duration_min: int
    duration_jitter: float


@dataclass(frozen=True)
class Persona:
    name: str
    weekday: tuple[RoutineStep, ...]
    weekend: tuple[RoutineStep, ...]
    outing_chance: dict[str, float]
    outing_window: tuple[int, int]
    outing_minutes: tuple[int, int]
    night_trip_chance: float
    visitor_chance: float
    back_door_share: float = 0.15

    def routine_for(self, day: date) -> tuple[RoutineStep, ...]:
        return self.weekend if day.weekday() >= 5 else self.weekday

    def daytype(self, day: date) -> str:
        return WEEKEND if day.weekday() >= 5 else WEEKDAY


MARGARET = Persona(
    name="Margaret",
    weekday=(
        RoutineStep("wake", "bedroom", 410, 18, 15, 5),
        RoutineStep("wash", "bathroom", 425, 12, 15, 5),
        RoutineStep("breakfast", "kitchen", 440, 15, 40, 10),
        RoutineStep("morning_sit", "living_room", 480, 15, 85, 20),
        RoutineStep("tidy", "bedroom", 565, 25, 30, 12),
        RoutineStep("late_morning", "living_room", 595, 20, 120, 30),
        RoutineStep("lunch", "kitchen", 715, 20, 45, 12),
        RoutineStep("afternoon", "living_room", 760, 20, 145, 35),
        RoutineStep("tea", "kitchen", 905, 20, 25, 8),
        RoutineStep("evening_sit", "living_room", 930, 20, 185, 35),
        RoutineStep("dinner", "kitchen", 1115, 22, 50, 12),
        RoutineStep("evening", "living_room", 1165, 22, 150, 30),
        RoutineStep("bedtime_wash", "bathroom", 1315, 20, 15, 5),
        RoutineStep("settle", "bedroom", 1330, 20, 20, 8),
        RoutineStep("sleep", ASLEEP, 1350, 20, 500, 30),
    ),
    weekend=(
        RoutineStep("wake", "bedroom", 455, 25, 20, 8),
        RoutineStep("wash", "bathroom", 475, 18, 15, 5),
        RoutineStep("breakfast", "kitchen", 490, 20, 50, 15),
        RoutineStep("morning_sit", "living_room", 540, 20, 120, 30),
        RoutineStep("lunch", "kitchen", 660, 25, 40, 12),
        RoutineStep("afternoon", "living_room", 700, 25, 170, 40),
        RoutineStep("tea", "kitchen", 870, 25, 25, 8),
        RoutineStep("evening_sit", "living_room", 895, 25, 220, 40),
        RoutineStep("dinner", "kitchen", 1115, 25, 50, 15),
        RoutineStep("evening", "living_room", 1165, 25, 165, 35),
        RoutineStep("bedtime_wash", "bathroom", 1330, 25, 15, 5),
        RoutineStep("settle", "bedroom", 1345, 25, 20, 8),
        RoutineStep("sleep", ASLEEP, 1365, 25, 490, 35),
    ),
    outing_chance={WEEKDAY: 0.40, WEEKEND: 0.75},
    outing_window=(570, 900),
    outing_minutes=(55, 190),
    night_trip_chance=0.55,
    visitor_chance=0.30,
)

PERSONAS = {"margaret": MARGARET}
