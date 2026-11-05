# -*- coding: utf-8 -*-

import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from .calculations import calculer_bilan_hydrique_python
from .curve import ajouter_graphes_excel_originaux_si_possible, creer_deux_graphes_sur_feuil1
from modules.shared import add_station_metadata_sheet

def creer_classeur_bilan_python(
    tp: Dict[str, float], pr: Dict[str, float], inclure_graphes: bool = True,
    station_name: str = "", station_code: str = "", years_used=None,
):
    """Construit integralement le classeur Bilan hydrique depuis Python."""
    calc = calculer_bilan_hydrique_python(tp, pr)
    wb = Workbook()
    ws = wb.active
    ws.title = "Feuil1"

    headers = ["Mois", "Tp", "IT", "CL", "ETPC", "Pr", "BH", "CH", "VR", "RFU", "ETR", "Def", "Exc"]
    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill(fill_type="solid", fgColor="E7ECF3")

    for c, title in enumerate(headers, start=1):
        cell = ws.cell(5, c, title)
        cell.font = Font(name="Times New Roman", size=10, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border
        cell.fill = header_fill

    keys = ["mois", "Tp", "IT", "CL", "ETPC", "Pr", "BH", "CH", "VR", "RFU", "ETR", "Def", "Exc"]
    all_rows = list(calc["rows"]) + [calc["annuel"]]
    for r, rowdata in enumerate(all_rows, start=6):
        for c, key in enumerate(keys, start=1):
            value = rowdata[key]
            cell = ws.cell(r, c, value)
            cell.font = Font(name="Times New Roman", size=10, bold=(r == 18))
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = border
            if c in (2, 6) and r <= 17:
                cell.number_format = "0.00"
            elif c == 4 and r <= 17:
                cell.number_format = "0.00"
            elif c > 1 and value is not None:
                cell.number_format = "0.0"

    ws.column_dimensions["A"].width = 14
    for col in range(2, 14):
        ws.column_dimensions[get_column_letter(col)].width = 11
    ws.row_dimensions[5].height = 22
    for r in range(6, 19):
        ws.row_dimensions[r].height = 20

    # Metadonnees du calcul Python dans une zone hors impression.
    ws["AC2"] = "Moteur"
    ws["AD2"] = "Python - Thornthwaite"
    ws["AC3"] = "I annuel"
    ws["AD3"] = float(calc["I"])
    ws["AC4"] = "a"
    ws["AD4"] = float(calc["a"])
    ws.column_dimensions["AC"].hidden = True
    ws.column_dimensions["AD"].hidden = True

    if inclure_graphes:
        creer_deux_graphes_sur_feuil1(ws)

    add_station_metadata_sheet(
        wb, station_name=station_name, station_code=station_code, years_used=years_used,
        worksheet=ws,
    )

    try:
        wb.calculation.calcMode = "manual"
        wb.calculation.fullCalcOnLoad = False
        wb.calculation.forceFullCalc = False
    except Exception:
        pass
    return wb

def _sauver_classeur_sans_ecraser(wb, fichier_sortie: Path) -> None:
    fichier_sortie = Path(fichier_sortie)
    fichier_sortie.parent.mkdir(parents=True, exist_ok=True)
    if fichier_sortie.exists():
        raise FileExistsError(f"Fichier deja existant, ecriture refusee : {fichier_sortie}")
    with tempfile.NamedTemporaryFile(suffix=fichier_sortie.suffix, prefix=".hydro_",
                                     dir=fichier_sortie.parent, delete=False) as handle:
        tmp = Path(handle.name)
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

def ecrire_bilan_hydrique_python(
    fichier_sortie: Path,
    tp: Dict[str, float],
    pr: Dict[str, float],
    inclure_graphes: bool = True,
    station_name: str = "", station_code: str = "", years_used=None,
) -> None:
    wb = creer_classeur_bilan_python(
        tp, pr, inclure_graphes=inclure_graphes, station_name=station_name,
        station_code=station_code, years_used=years_used,
    )
    _sauver_classeur_sans_ecraser(wb, fichier_sortie)
    if inclure_graphes:
        # Sous Windows avec Microsoft Excel, ce passage reproduit le tableau de
        # donnees integre et les axes secondaires du modele original au pixel pres.
        ajouter_graphes_excel_originaux_si_possible(fichier_sortie)

