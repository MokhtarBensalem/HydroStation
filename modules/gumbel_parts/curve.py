# -*- coding: utf-8 -*-
from __future__ import annotations

import math
from typing import Tuple

from openpyxl.chart import Reference, ScatterChart, Series
from openpyxl.chart.layout import Layout, ManualLayout
from openpyxl.chart.marker import Marker
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor

from .support import convertir_nombre

def _arrondir_au_pas_superieur(valeur: float, pas: int = 20) -> int:
    """Arrondit une valeur au multiple superieur du pas choisi."""
    if valeur <= 0:
        return pas
    return int(math.ceil(valeur / pas) * pas)

def _choisir_pas_graduation_y(ymax: float) -> int:
    """Choisit un pas de graduation lisible pour eviter une grille trop dense."""
    if ymax <= 100:
        return 10
    if ymax <= 200:
        return 20
    if ymax <= 500:
        return 50
    if ymax <= 1000:
        return 100
    if ymax <= 2000:
        return 200
    if ymax <= 5000:
        return 500
    return int(math.ceil((ymax / 10.0) / 1000.0) * 1000)

def _format_fr_nombre(valeur: float, decimales: int = 2) -> str:
    """Formate un nombre avec virgule decimale pour les titres du graphique."""
    return f"{valeur:.{decimales}f}".replace(".", ",")

def _statistiques_gumbel_depuis_colonne_b(ws, ligne_debut: int, ligne_fin: int) -> Tuple[float, float, int, int]:
    """Calcule Mode, Gradex, n et une borne Y max proche du modele original."""
    valeurs = []
    for row in range(ligne_debut, ligne_fin + 1):
        val = convertir_nombre(ws.cell(row=row, column=2).value)
        if val is not None:
            valeurs.append(float(val))

    n = len(valeurs)
    if n == 0:
        return 0.0, 0.0, 0, 100

    moyenne = sum(valeurs) / n
    if n >= 2:
        variance = sum((v - moyenne) ** 2 for v in valeurs) / (n - 1)
        ecart_type = math.sqrt(variance)
    else:
        ecart_type = 0.0

    gradex = 0.78 * ecart_type
    mode = moyenne - 0.577 * gradex

    # Le modele original laisse une marge verticale importante.
    ymax = _arrondir_au_pas_superieur(max(valeurs) * 1.65, 20)
    ymax = max(ymax, _arrondir_au_pas_superieur(max(valeurs) + 20, 20))
    return mode, gradex, n, ymax

def creer_courbe_gumbel(ws, ligne_debut: int, ligne_fin: int) -> None:
    """Recree le graphique du classeur Gumbel original sans modele externe."""
    ws._charts = []
    if ligne_fin < ligne_debut + 2:
        return

    chart = ScatterChart()
    chart.style = 2
    chart.scatterStyle = "lineMarker"
    chart.title = None
    chart.legend = None
    chart.width = 17.8
    chart.height = 10.6

    try:
        chart.layout = Layout(ManualLayout(
            x=0.0513257687, y=0.0562164684, w=0.946734082, h=0.910776767,
            xMode="edge", yMode="edge", wMode="factor", hMode="factor",
        ))
    except Exception:
        pass

    xvalues = Reference(ws, min_col=5, min_row=ligne_debut, max_row=ligne_fin)

    # 1. Valeurs experimentales : losanges noirs, sans ligne de liaison.
    y_exp = Reference(ws, min_col=6, min_row=ligne_debut, max_row=ligne_fin)
    serie_exp = Series(y_exp, xvalues, title="Valeur expérimentale")
    serie_exp.marker = Marker("diamond")
    serie_exp.marker.size = 5
    serie_exp.marker.graphicalProperties.solidFill = "FF0000"
    serie_exp.marker.graphicalProperties.line.solidFill = "FF0000"
    serie_exp.graphicalProperties.line.solidFill = "FF0000"
    serie_exp.graphicalProperties.line.width = 0
    chart.series.append(serie_exp)

    # 2. Droite theorique : noire, epaisseur du graphique original (25200 EMU).
    y_theo = Reference(ws, min_col=7, min_row=ligne_debut, max_row=ligne_fin)
    serie_theo = Series(y_theo, xvalues, title="Valeur théorique")
    serie_theo.graphicalProperties.line.solidFill = "000000"
    serie_theo.graphicalProperties.line.width = 25200
    serie_theo.marker.symbol = "none"
    chart.series.append(serie_theo)

    # 3-4. Bornes de confiance : traits noirs fins, sans marqueurs.
    for col, titre in [(8, "Borne inférieure"), (9, "Borne supérieure")]:
        y = Reference(ws, min_col=col, min_row=ligne_debut, max_row=ligne_fin)
        serie = Series(y, xvalues, title=titre)
        serie.graphicalProperties.line.solidFill = "000000"
        serie.graphicalProperties.line.width = 0
        serie.marker.symbol = "none"
        chart.series.append(serie)

    chart.x_axis.scaling.min = -2
    chart.x_axis.scaling.max = 5
    chart.x_axis.numFmt = "0.000"
    chart.x_axis.numFmt.sourceLinked = True
    chart.x_axis.majorTickMark = "cross"
    chart.x_axis.minorTickMark = "none"
    chart.x_axis.axPos = "b"
    chart.x_axis.title = None
    try:
        chart.x_axis.crossesAt = -5000
    except Exception:
        pass

    chart.y_axis.numFmt = "0.00"
    chart.y_axis.numFmt.sourceLinked = True
    chart.y_axis.majorTickMark = "cross"
    chart.y_axis.minorTickMark = "none"
    chart.y_axis.axPos = "l"
    chart.y_axis.title = "Valeurs naturelles"
    try:
        chart.y_axis.crossesAt = -2
    except Exception:
        pass

    # Le modele original n'affiche pas de grille principale.
    try:
        chart.x_axis.majorGridlines = None
        chart.y_axis.majorGridlines = None
    except Exception:
        pass

    # Identifiants/position repris du graphique de reference.
    chart.x_axis.axId = 84245934
    chart.y_axis.axId = 449913
    chart.x_axis.crossAx = 449913
    chart.y_axis.crossAx = 84245934
    chart.x_axis.delete = False
    chart.y_axis.delete = False
    chart.x_axis.tickLblPos = "nextTo"
    chart.y_axis.tickLblPos = "nextTo"
    chart.x_axis.crossBetween = "midCat"
    chart.y_axis.crossBetween = "midCat"

    # Ancrage deux cellules identique au fichier Excel de reference.
    chart.anchor = TwoCellAnchor(
        _from=AnchorMarker(col=10, colOff=83160, row=6, rowOff=133560),
        to=AnchorMarker(col=17, colOff=423000, row=26, rowOff=44640),
    )
    ws.add_chart(chart)
