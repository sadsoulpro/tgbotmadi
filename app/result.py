from __future__ import annotations

from dataclasses import dataclass

from .content import SPHERES, SPHERE_LABELS


@dataclass(frozen=True)
class Result:
    variant: str
    average: float
    safe: bool
    paragraph: str
    state: str


def classify(scores: dict[str, int], rules: dict[str, float], texts: dict[str, str]) -> Result:
    values = [scores[key] for key in SPHERES]
    if len(values) != 4 or any(not isinstance(v, int) or not 0 <= v <= 10 for v in values):
        raise ValueError("Нужны четыре целых оценки от 0 до 10")
    avg = sum(values) / 4
    safe = avg <= rules["safety_avg"] or sum(v <= rules["safety_score"] for v in values) >= rules["safety_count"]
    if safe:
        return Result("C", avg, True, texts["C"], texts["C"])

    spread = max(values) - min(values)
    if spread <= rules["even_gap"]:
        variant = "B1" if avg <= rules["low_max"] else "B2" if avg <= rules["mid_max"] else "B3"
        paragraph = texts[variant]
    else:
        ordered = sorted(range(4), key=lambda i: (values[i], i))
        low = ordered[0]
        variant = f"A{low + 1}"
        paragraph = texts[variant]
        second = ordered[1]
        if (values[second] - values[low] <= rules["two_tolerance"]
                and min(values[i] for i in ordered[2:]) - values[second] >= rules["two_gap"]):
            paragraph += "\n\n" + texts["two_low"]
        high = max(range(4), key=lambda i: values[i])
        if values[high] - values[low] >= rules["compensation_gap"]:
            instrumental = {
                "spiritual": "духовной", "emotional": "эмоциональной",
                "mental": "ментальной", "physical": "физической",
            }
            paragraph += "\n\n" + texts["compensation"].format(
                sphere=instrumental[SPHERES[high]], value=values[high]
            )
    state = texts["state_low"] if avg <= rules["low_max"] else texts["state_mid"] if avg <= rules["mid_max"] else texts["state_high"]
    return Result(variant, avg, False, paragraph, state)
