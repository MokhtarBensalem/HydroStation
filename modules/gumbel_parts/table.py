# -*- coding: utf-8 -*-
from __future__ import annotations

import math
import os
import shutil
import tempfile
from copy import copy
from pathlib import Path
from typing import List, Optional, Sequence

import pandas as pd
from openpyxl import Workbook
from openpyxl.formula.translate import Translator
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .support import INTERVALLE_CONFIANCE, NOMBRE_DECIMALES
from .calculations import calculer_gumbel_python
from .curve import creer_courbe_gumbel
from modules.shared import add_station_metadata_sheet















def formule_contient_reference_externe_ancienne(formule: str) -> bool:
    """Detecte les references parasites creees parfois lors de la conversion .xls -> .xlsx."""
    f = str(formule)
    return ("[1]" in f) or ("Feuil1!" in f) or ("'Feuil1'!" in f)

def cellule_copier(
    src,
    dst,
    origin: Optional[str] = None,
    target: Optional[str] = None,
    main_sheet: Optional[str] = None,
    valeur_cache=None,
) -> None:
    if isinstance(src.value, str) and src.value.startswith("=") and origin and target:
        # Les anciennes feuilles .xls converties peuvent contenir des formules du type
        # =[1]Feuil1!$A$36 pour de simples libelles. Comme on supprime Feuil1/2/3
        # dans le fichier final, ces formules sont remplacees par leur valeur cachee.
        if formule_contient_reference_externe_ancienne(src.value) and valeur_cache is not None:
            dst.value = valeur_cache
        else:
            try:
                formule = Translator(src.value, origin=origin).translate_formula(target)
            except Exception:
                formule = src.value
            if main_sheet:
                # References absolues du modele vers les parametres de la feuille principale.
                prefix = f"'{main_sheet}'!"
                for ref in ["$C$2", "$C$3", "$G$2", "$I$2", "$I$3", "$B$3", "$B$4", "$E$2", "$E$4"]:
                    formule = formule.replace(ref, prefix + ref)
            dst.value = formule
    else:
        dst.value = src.value
    if src.has_style:
        dst._style = copy(src._style)
    dst.number_format = src.number_format
    dst.alignment = copy(src.alignment)
    dst.border = copy(src.border)
    dst.fill = copy(src.fill)
    dst.font = copy(src.font)

def vider_ou_creer_feuille(wb, nom: str):
    if nom in wb.sheetnames:
        ws = wb[nom]
        ws.delete_rows(1, ws.max_row)
        ws._charts = []
        return ws
    return wb.create_sheet(nom)

def copier_range(
    src_ws,
    dst_ws,
    src_range: str,
    dst_row: int,
    dst_col: int,
    main_sheet: Optional[str] = None,
    cache_ws=None,
) -> None:
    cells = list(src_ws[src_range])
    if not cells:
        return
    for r_offset, row in enumerate(cells):
        for c_offset, src_cell in enumerate(row):
            dr = dst_row + r_offset
            dc = dst_col + c_offset
            dst_cell = dst_ws.cell(row=dr, column=dc)
            origin = src_cell.coordinate
            target = f"{get_column_letter(dc)}{dr}"
            valeur_cache = None
            if cache_ws is not None:
                try:
                    valeur_cache = cache_ws[src_cell.coordinate].value
                except Exception:
                    valeur_cache = None
            cellule_copier(src_cell, dst_cell, origin, target, main_sheet=main_sheet, valeur_cache=valeur_cache)
    # Largeurs approximatives
    min_col = cells[0][0].column
    max_col = cells[0][-1].column
    for c_offset, col_idx in enumerate(range(min_col, max_col + 1)):
        src_letter = get_column_letter(col_idx)
        dst_letter = get_column_letter(dst_col + c_offset)
        dst_ws.column_dimensions[dst_letter].width = src_ws.column_dimensions[src_letter].width

def supprimer_feuilles_sources_inutiles(wb, feuilles_a_garder: Sequence[str]) -> None:
    """
    Supprime les feuilles du modele source apres creation des 3 feuilles finales.
    Objectif : ne plus garder Feuil1, Feuil2, Feuil3 ou toute feuille originale du modele.
    """
    garder = set(feuilles_a_garder)
    for nom in list(wb.sheetnames):
        if nom not in garder and len(wb.sheetnames) > len(garder):
            try:
                del wb[nom]
            except Exception:
                pass

def supprimer_feuilles_vides_defaut(wb) -> None:
    """Supprime seulement les feuilles par defaut vides : Feuil1/2/3, Sheet1/2/3."""
    noms_defaut = {
        "Feuil1", "Feuil2", "Feuil3",
        "Feuille1", "Feuille2", "Feuille3",
        "Sheet1", "Sheet2", "Sheet3",
    }
    for nom in list(wb.sheetnames):
        if nom in noms_defaut and len(wb.sheetnames) > 1:
            ws = wb[nom]
            est_vide = True
            for row in ws.iter_rows():
                for cell in row:
                    if cell.value is not None:
                        est_vide = False
                        break
                if not est_vide:
                    break
            if est_vide:
                try:
                    del wb[nom]
                except Exception:
                    pass

def creer_feuilles_separees(wb, feuille_source: str, ligne_debut: int, ligne_fin: int, cache_source_ws=None) -> None:
    src = wb[feuille_source]
    ws1 = vider_ou_creer_feuille(wb, "01_Ajustement_Gumbel")
    ws2 = vider_ou_creer_feuille(wb, "02_Quantiles_Probabilites")
    ws3 = vider_ou_creer_feuille(wb, "03_Temps_Retour")

    # Table principale A:I.
    # On copie seulement jusqu'a la derniere valeur classee reelle :
    # cela supprime le tableau vide restant sous les donnees.
    copier_range(src, ws1, f"A1:I{ligne_fin}", 1, 1, cache_ws=cache_source_ws)
    creer_courbe_gumbel(ws1, ligne_debut, ligne_fin)

    # D'apres la structure du modele Tab Gumbel 2 :
    # table des quantiles/probabilites : S:W ; table des temps de retour : Y:AA.
    try:
        copier_range(src, ws2, "S1:W9", 1, 1, main_sheet="01_Ajustement_Gumbel", cache_ws=cache_source_ws)
        # Titre dynamique de la table des quantiles, sans reference a Feuil1.
        ws2["A1"] = "classées"
        ws2["B1"] = "=\"(\"&'01_Ajustement_Gumbel'!$D$2&FIXED('01_Ajustement_Gumbel'!$E$2)&\", \"&'01_Ajustement_Gumbel'!$D$3&FIXED('01_Ajustement_Gumbel'!$E$3)&\" ,\"&'01_Ajustement_Gumbel'!$F$2&FIXED('01_Ajustement_Gumbel'!$G$2,0)&\" , \"&'01_Ajustement_Gumbel'!$H$2&FIXED('01_Ajustement_Gumbel'!$I$2,0)&\")\""
    except Exception:
        pass
    try:
        copier_range(src, ws3, "Y3:AA8", 1, 1, main_sheet="01_Ajustement_Gumbel", cache_ws=cache_source_ws)
    except Exception:
        pass

    # Garder uniquement les 3 feuilles finales, et supprimer Feuil1/Feuil2/Feuil3
    # ou toute feuille source du modele.
    supprimer_feuilles_sources_inutiles(
        wb,
        ["01_Ajustement_Gumbel", "02_Quantiles_Probabilites", "03_Temps_Retour"],
    )



def creer_classeur_gumbel_python(
    table_pjmax: pd.DataFrame, inclure_graphiques: bool = True,
    station_name: str = "", station_code: str = "", years_used=None,
):
    """Construit le classeur Gumbel en Python, sans ouvrir Gumbel.xlsx."""
    if table_pjmax is None or table_pjmax.empty:
        raise ValueError("Aucune valeur PJmax a ecrire")
    table = table_pjmax.copy()
    table["valeur"] = pd.to_numeric(table["valeur"], errors="coerce")
    table = table.dropna(subset=["valeur"]).sort_values(["valeur", "annee"], kind="mergesort").reset_index(drop=True)
    stats = calculer_gumbel_python(table["valeur"].tolist())

    wb = Workbook()
    ws = wb.active
    ws.title = "Feuil1"
    thin = Side(style="thin", color="808080")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Parametres identiques au modele de reference.
    ws["D1"] = "Ajustement à une loi de Gumbel"
    ws["D2"] = "Mode="; ws["E2"] = float(stats["mode"])
    ws["F2"] = "Taille n="; ws["G2"] = int(stats["n"])
    ws["H2"] = "I.C. à (en%)="; ws["I2"] = INTERVALLE_CONFIANCE
    ws["D3"] = "Gradex="; ws["E3"] = float(stats["gradex"])
    ws["F3"] = "Nb au départ"; ws["G3"] = int(stats["n"])
    ws["H3"] = "U Gauss="; ws["I3"] = float(stats["u_gauss"])

    headers1 = ["Observations", "Valeurs", "Ordre de", "Fréquence", "Variable", "Valeur", "Valeur", "Borne", "Borne"]
    headers2 = ["classées", "classées", "classement", "expérimentale", "réduite", "expérimentale", "théorique", "inférieure", "supérieure"]
    for c, text in enumerate(headers1, 1): ws.cell(4, c, text)
    for c, text in enumerate(headers2, 1): ws.cell(5, c, text)

    rows = stats["rows"]
    for i, (_, source) in enumerate(table.iterrows()):
        r = 6 + i
        rank, freq, reduced, expv, theoretical, lower, upper = rows[i]
        ws.cell(r, 1, int(source["annee"]))
        ws.cell(r, 2, round(float(source["valeur"]), NOMBRE_DECIMALES))
        ws.cell(r, 3, rank)
        ws.cell(r, 4, freq)
        ws.cell(r, 5, reduced)
        ws.cell(r, 6, expv)
        ws.cell(r, 7, theoretical)
        ws.cell(r, 8, lower)
        ws.cell(r, 9, upper)

    ligne_debut = 6
    ligne_fin = 5 + len(table)

    # Tables auxiliaires du modele : quantiles et periodes de retour.
    ws["S1"] = "classées"
    ws["T1"] = f"(Mode={stats['mode']:.2f}, Gradex={stats['gradex']:.2f} ,Taille n={stats['n']} , I.C. à (en%)={INTERVALLE_CONFIANCE})"
    for c, text in zip(range(19, 24), ["fréquence", "variable réduite", "valeur théorique", "Borne inférieure", "Borne supérieure"]):
        ws.cell(2, c, text)
    for j, freq in enumerate([0.50, 0.80, 0.90, 0.95, 0.98], start=4):
        red = -math.log(-math.log(freq))
        theo = stats["mode"] + stats["gradex"] * red
        n = stats["n"]; ug = stats["u_gauss"]; gr = stats["gradex"]
        den = 1 - 1.1 * ug**2 / n
        common = math.sqrt((0.714 + 0.108*red + 0.671*red**2)/n)
        low = theo - ((ug*common - ug**2/n*(0.859*red+0.074))/den)*gr/0.78
        high = theo + ((ug*common + ug**2/n*(0.859*red+0.074))/den)*gr/0.78
        ws.cell(j, 19, freq); ws.cell(j, 20, red); ws.cell(j, 21, theo); ws.cell(j, 22, low); ws.cell(j, 23, high)

    ws["Y3"] = "Valeur"; ws["Z3"] = "Fréquence"; ws["AA3"] = "Période de retour"
    for j, period in enumerate([2, 5, 10, 20, 50, 100], start=4):
        freq = 1 - 1 / period
        red = -math.log(-math.log(freq))
        value = stats["mode"] + stats["gradex"] * red
        ws.cell(j, 25, value); ws.cell(j, 26, freq); ws.cell(j, 27, period)

    # Titres reproduits hors graphique comme dans le fichier original.
    ws.merge_cells("K5:R5"); ws["K5"] = "Ajustement à une loi de Gumbel"
    ws.merge_cells("K6:R6"); ws["K6"] = ws["T1"].value
    ws.merge_cells("K28:R28"); ws["K28"] = "Fréquences selon une échelle gumbelienne"
    for ref in ("K5", "K6", "K28"):
        ws[ref].alignment = Alignment(horizontal="center")
    ws["K5"].font = Font(name="Times New Roman", bold=True, size=12)
    ws["K6"].font = Font(name="Times New Roman", size=9)

    # Style tableau proche du classeur d'origine.
    for r in range(4, ligne_fin + 1):
        for c in range(1, 10):
            cell = ws.cell(r, c)
            cell.border = border
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.font = Font(name="Times New Roman", size=9, bold=(r in (4,5)))
            if r in (4,5):
                cell.fill = PatternFill("solid", fgColor="F2F2F2")
    for c, w in {1:13,2:12,3:12,4:14,5:12,6:14,7:14,8:14,9:14}.items():
        ws.column_dimensions[get_column_letter(c)].width = w
    for c in range(11, 19): ws.column_dimensions[get_column_letter(c)].width = 12
    for c in range(19, 28): ws.column_dimensions[get_column_letter(c)].width = 15

    add_station_metadata_sheet(
        wb, station_name=station_name, station_code=station_code, years_used=years_used,
        worksheet=ws,
    )

    if inclure_graphiques:
        creer_courbe_gumbel(ws, ligne_debut, ligne_fin)

    try:
        wb.calculation.calcMode = "auto"
        wb.calculation.fullCalcOnLoad = True
        wb.calculation.forceFullCalc = True
    except Exception:
        pass
    return wb, ws.title, ligne_debut, ligne_fin

def creer_fichier_gumbel_depuis_modele(
    table_pjmax: pd.DataFrame, diagnostics: List[str], template_path: Optional[Path],
    fichier_sortie: Path, split_tables: bool = True, inclure_graphiques: bool = True,
    station_name: str = "", station_code: str = "", years_used=None,
) -> Path:
    """Cree le fichier final entierement en Python.

    ``template_path`` est conserve uniquement pour compatibilite avec les
    anciennes integrations; le fichier Gumbel.xlsx n'est plus lu.
    """
    wb, feuille_source, ligne_debut, ligne_fin = creer_classeur_gumbel_python(
        table_pjmax, inclure_graphiques=inclure_graphiques,
        station_name=station_name, station_code=station_code, years_used=years_used,
    )
    if split_tables:
        creer_feuilles_separees(wb, feuille_source, ligne_debut, ligne_fin, cache_source_ws=None)
        if "01_Ajustement_Gumbel" in wb.sheetnames and inclure_graphiques:
            creer_courbe_gumbel(wb["01_Ajustement_Gumbel"], ligne_debut, ligne_fin)
    else:
        supprimer_feuilles_vides_defaut(wb)
    if not inclure_graphiques:
        for ws in wb.worksheets:
            ws._charts = []
    fichier_sortie.parent.mkdir(parents=True, exist_ok=True)
    if fichier_sortie.exists():
        raise FileExistsError(f"Refus d'ecraser un fichier existant : {fichier_sortie}")
    with tempfile.NamedTemporaryFile(dir=fichier_sortie.parent, suffix=".xlsx", prefix=".hydro_", delete=False) as fp:
        tmp = Path(fp.name)
    try:
        wb.save(tmp)
        try:
            os.link(tmp, fichier_sortie)
        except OSError as exc:
            if fichier_sortie.exists():
                raise FileExistsError(str(fichier_sortie)) from exc
            with fichier_sortie.open("xb") as dest, tmp.open("rb") as src:
                shutil.copyfileobj(src, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return fichier_sortie


