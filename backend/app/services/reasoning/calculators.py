"""PDE and MACO calculation functions per CFDI guideline formulas."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PDEResult:
    value: float
    parameters: dict[str, float]


@dataclass
class MACOResult:
    value: float
    method: str
    all_methods: dict[str, float]


def calculate_pde(
    pod: float,
    bw: float = 50.0,
    f1: float = 1.0,
    f2: float = 10.0,
    f3: float = 1.0,
    f4: float = 1.0,
    f5: float = 1.0,
    mf: float = 1.0,
) -> PDEResult:
    """PDE (mg/day) = (PoD x BW) / (F1 x F2 x F3 x F4 x F5 x MF)"""
    denominator = f1 * f2 * f3 * f4 * f5 * mf
    if denominator == 0:
        raise ValueError("Adjustment factors product cannot be zero")
    pde = (pod * bw) / denominator
    return PDEResult(
        value=pde,
        parameters={"pod": pod, "bw": bw, "f1": f1, "f2": f2,
                     "f3": f3, "f4": f4, "f5": f5, "mf": mf},
    )


ROUTE_SAFETY_FACTORS = {
    "topical": (10.0, 100.0),
    "oral": (100.0, 1000.0),
    "intravenous": (1000.0, 10000.0),
    "injection": (1000.0, 10000.0),
}


def calculate_maco(
    mbs: float,
    tdd_next: float,
    pde: float | None = None,
    min_therapeutic_dose: float | None = None,
    ld50: float | None = None,
    bw: float = 50.0,
    route: str = "oral",
) -> MACOResult:
    """Calculate MACO using all applicable methods, return the minimum.

    Methods per CFDI guideline:
      1. 1/1000 minimum therapeutic dose
      2. NOEL method (from LD50)
      3. 10 ppm general limit
      4. PDE method (preferred by CFDI)
    """
    if mbs <= 0 or tdd_next <= 0:
        raise ValueError("MBS and TDD_next must be positive")

    methods: dict[str, float] = {}

    # Method 4: PDE-based (CFDI preferred)
    if pde and pde > 0:
        methods["PDE-based"] = (pde * mbs) / tdd_next

    # Method 1: 1/1000 minimum therapeutic dose
    if min_therapeutic_dose and min_therapeutic_dose > 0:
        methods["dose-based"] = (min_therapeutic_dose * mbs) / (1000.0 * tdd_next)

    # Method 3: 10 ppm general limit
    methods["general-limit"] = 10e-6 * mbs * 1e6  # 10 ppm × MBS in mg

    # Method 2: NOEL method
    if ld50 and ld50 > 0:
        noel = (ld50 * bw) / 2000.0
        sf_low, sf_high = ROUTE_SAFETY_FACTORS.get(route, (100.0, 1000.0))
        sf_route = (sf_low + sf_high) / 2.0
        methods["NOEL-based"] = (noel * mbs) / (sf_route * tdd_next)

    if not methods:
        raise ValueError("No calculation method applicable with the given inputs")

    chosen_method = min(methods, key=methods.get)
    return MACOResult(
        value=methods[chosen_method],
        method=chosen_method,
        all_methods=methods,
    )
