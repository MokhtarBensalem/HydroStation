# -*- coding: utf-8 -*-

from typing import Dict

from .support import (
    COEFFICIENTS_CL, MOIS_FR, MOIS_HYDRO, RFU_MAX,
    convertir_nombre_climatique, ordre_mois_hydro_noms,
)

def valider_normales(tp, pr):
    for variable, data in (("temperature", tp), ("precipitation", pr)):
        for mois in ordre_mois_hydro_noms():
            value = convertir_nombre_climatique(data.get(mois), variable)
            if value is None:
                raise ValueError(f"Valeur non numerique, manquante ou non finie : {variable} {mois}: {data.get(mois)!r}")

def calculer_bilan_hydrique_python(tp: Dict[str, float], pr: Dict[str, float]) -> Dict[str, object]:
    """Reproduction numerique du modele Excel de reference, sans Excel.

    Verification effectuee sur les valeurs cachees du modele original : IT, ETP,
    BH, CH, VR, RFU, ETR, Deficit et Excedent donnent les memes valeurs a la
    precision flottante. Les valeurs Tp/Pr restent a leur precision native pour
    reproduire exactement les formules Excel; l'affichage dans le classeur reste
    formate a 2 decimales comme le modele.
    """
    valider_normales(tp, pr)
    tp_vals = [float(tp[m]) for m, _ in MOIS_HYDRO]
    pr_vals = [float(pr[m]) for m, _ in MOIS_HYDRO]
    cl_vals = list(COEFFICIENTS_CL)

    it_vals = [((t / 5.0) ** 1.514) if t > 0 else 0.0 for t in tp_vals]
    i_annuel = sum(it_vals)
    exposant_a = (1.6 * i_annuel / 100.0 + 0.5) if i_annuel > 0 else 0.0
    etpc_vals = [
        16.0 * ((10.0 * t / i_annuel) ** exposant_a) * cl if t > 0 and i_annuel > 0 else 0.0
        for t, cl in zip(tp_vals, cl_vals)
    ]
    bh_vals = [p - e for p, e in zip(pr_vals, etpc_vals)]
    ch_vals = [(bh / e if e else 0.0) for bh, e in zip(bh_vals, etpc_vals)]

    def passer_annee(rfu_initial: float):
        rfu_prev = float(rfu_initial)
        rfu, vr, etr, deficit, excedent = [], [], [], [], []
        for p, e in zip(pr_vals, etpc_vals):
            brut = rfu_prev + p - e
            rfu_actuel = max(0.0, min(float(RFU_MAX), brut))
            variation = rfu_actuel - rfu_prev
            etr_actuel = min(e, p + rfu_prev)
            etr_actuel = max(0.0, etr_actuel)
            def_actuel = max(0.0, e - etr_actuel)
            exc_actuel = max(0.0, brut - float(RFU_MAX))
            rfu.append(rfu_actuel)
            vr.append(variation)
            etr.append(etr_actuel)
            deficit.append(def_actuel)
            excedent.append(exc_actuel)
            rfu_prev = rfu_actuel
        return rfu, vr, etr, deficit, excedent, rfu_prev

    # Le modele Excel ferme le cycle Aout -> Septembre. On cherche le point fixe
    # annuel au lieu de conserver une reference circulaire dans le classeur.
    rfu_initial = float(RFU_MAX)
    resultat = None
    for _ in range(100):
        rfu, vr, etr, deficit, excedent, rfu_fin = passer_annee(rfu_initial)
        resultat = (rfu, vr, etr, deficit, excedent)
        if abs(rfu_fin - rfu_initial) < 1e-10:
            break
        rfu_initial = rfu_fin
    if resultat is None:
        resultat = passer_annee(float(RFU_MAX))[:5]
    rfu, vr, etr, deficit, excedent = resultat

    rows = []
    for i, (mois, _) in enumerate(MOIS_HYDRO):
        rows.append({
            "mois": MOIS_FR[mois], "Tp": tp_vals[i], "IT": it_vals[i], "CL": cl_vals[i],
            "ETPC": etpc_vals[i], "Pr": pr_vals[i], "BH": bh_vals[i], "CH": ch_vals[i],
            "VR": vr[i], "RFU": rfu[i], "ETR": etr[i], "Def": deficit[i], "Exc": excedent[i],
        })

    annuel = {
        "mois": "Annuel", "Tp": sum(tp_vals) / 12.0, "IT": sum(it_vals), "CL": None,
        "ETPC": sum(etpc_vals), "Pr": sum(pr_vals), "BH": sum(bh_vals), "CH": None,
        "VR": None, "RFU": None, "ETR": sum(etr), "Def": sum(deficit), "Exc": sum(excedent),
    }
    return {"rows": rows, "annuel": annuel, "I": i_annuel, "a": exposant_a}
