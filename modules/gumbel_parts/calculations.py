# -*- coding: utf-8 -*-
from __future__ import annotations

import math
from typing import Dict, Sequence

from .support import INTERVALLE_CONFIANCE, convertir_nombre

def _norm_ppf(p: float) -> float:
    """Inverse de la loi normale standard sans scipy."""
    from statistics import NormalDist
    return NormalDist().inv_cdf(p)

def calculer_gumbel_python(valeurs: Sequence[float], ic: float = INTERVALLE_CONFIANCE) -> Dict[str, object]:
    """Calcule en Python les colonnes du modele Excel de Gumbel."""
    vals = sorted(float(v) for v in valeurs if convertir_nombre(v) is not None)
    n = len(vals)
    if n < 2:
        raise ValueError("Au moins deux valeurs sont necessaires pour le calcul de Gumbel")
    moyenne = sum(vals) / n
    variance = sum((v - moyenne) ** 2 for v in vals) / (n - 1)
    std = math.sqrt(variance)
    gradex = 0.78 * std
    mode = moyenne - 0.577 * gradex
    u_gauss = _norm_ppf(1 - (1 - ic / 100.0) / 2.0)
    rows = []
    for rank, value in enumerate(vals, 1):
        freq = min(0.999999, max(0.000001, (rank - 0.5) / n))
        reduced = -math.log(-math.log(freq))
        theoretical = mode + gradex * reduced
        denom = 1 - 1.1 * (u_gauss ** 2) / n
        common = math.sqrt((0.714 + 0.108 * reduced + 0.671 * reduced ** 2) / n)
        low_term = (u_gauss * common - (u_gauss ** 2) / n * (0.859 * reduced + 0.074))
        up_term = (u_gauss * common + (u_gauss ** 2) / n * (0.859 * reduced + 0.074))
        lower = theoretical - (low_term / denom) * gradex / 0.78
        upper = theoretical + (up_term / denom) * gradex / 0.78
        rows.append((rank, freq, reduced, value, theoretical, lower, upper))
    return {"n": n, "moyenne": moyenne, "ecart_type": std, "gradex": gradex,
            "mode": mode, "u_gauss": u_gauss, "rows": rows}
