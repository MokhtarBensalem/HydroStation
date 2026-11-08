# -*- coding: utf-8 -*-

from pathlib import Path

from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter

from .support import MOIS_FR, MOIS_HYDRO

def _forcer_ligne_droite_et_marqueur(serie, couleur: str, largeur: int, marker: str, taille: int = 6) -> None:
    """
    Style de série proche du modèle original :
    ligne droite, marqueur visible, couleur forcée.
    """
    try:
        serie.smooth = False
    except Exception:
        pass

    try:
        serie.graphicalProperties.line.solidFill = couleur
        serie.graphicalProperties.line.width = largeur
    except Exception:
        pass

    try:
        serie.marker.symbol = marker
        serie.marker.size = taille
    except Exception:
        pass

    try:
        serie.marker.graphicalProperties.solidFill = couleur
    except Exception:
        pass

    try:
        serie.marker.graphicalProperties.line.solidFill = couleur
    except Exception:
        pass

def _ajouter_table_donnees(chart) -> None:
    """Ajoute le tableau de données sous le graphique quand la version openpyxl le permet."""
    try:
        # API disponible dans quelques versions seulement.
        from openpyxl.chart.table import DataTable  # type: ignore
        chart.dataTable = DataTable()
        chart.dataTable.showHorzBorder = True
        chart.dataTable.showVertBorder = True
        chart.dataTable.showOutline = True
        chart.dataTable.showKeys = True
        chart.legend = None
        return
    except Exception:
        pass

    try:
        # Alternative interne selon versions openpyxl.
        from openpyxl.chart.chartspace import DataTable  # type: ignore
        chart.dataTable = DataTable()
        chart.dataTable.showHorzBorder = True
        chart.dataTable.showVertBorder = True
        chart.dataTable.showOutline = True
        chart.dataTable.showKeys = True
        chart.legend = None
        return
    except Exception:
        pass

    # Dernier recours : ne pas bloquer le traitement.
    try:
        chart.legend.position = "b"
    except Exception:
        pass

def _mois_libelle_court_table(nom_mois: str) -> str:
    libelle = MOIS_FR.get(nom_mois, nom_mois)
    if libelle == "Septembre":
        return "Septem\nbre"
    if libelle == "Novembre":
        return "Novemb\nre"
    if libelle == "Décembre":
        return "Décemb\nre"
    if libelle == "Janvier":
        return "Janvi\ner"
    if libelle == "Juillet":
        return "Juil\nlet"
    return libelle

def _ecrire_tableau_graphique(ws, start_row: int, start_col: int, titre_lignes, colonnes_mois, formules):
    """
    Ecrit un tableau visible sous le graphique, comme dans le modèle d'origine.
    """
    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Ligne des mois
    ws.cell(row=start_row, column=start_col).value = ""
    for j, mois in enumerate(colonnes_mois, start=1):
        c = ws.cell(row=start_row, column=start_col + j)
        c.value = mois
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = border
        c.font = Font(name="Times New Roman", size=9)

    # Lignes des séries
    for i, titre in enumerate(titre_lignes, start=1):
        c0 = ws.cell(row=start_row + i, column=start_col)
        c0.value = titre
        c0.alignment = Alignment(horizontal="left", vertical="center")
        c0.border = border
        c0.font = Font(name="Times New Roman", size=9)

        for j in range(12):
            c = ws.cell(row=start_row + i, column=start_col + 1 + j)
            c.value = formules[i - 1][j]
            c.number_format = "0.0#"
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.border = border
            c.font = Font(name="Times New Roman", size=9)

    # Dimensions
    ws.column_dimensions[get_column_letter(start_col)].width = 17
    for j in range(1, 13):
        ws.column_dimensions[get_column_letter(start_col + j)].width = 6.8

    for r in range(start_row, start_row + 1 + len(titre_lignes)):
        ws.row_dimensions[r].height = 23 if r == start_row else 20

def _configurer_axes_gauche_droite(chart_gauche, chart_droit, axe_x_id: int, axe_gauche_id: int, axe_droit_id: int) -> None:
    """Force Excel à placer :
    - axe Y principal à gauche
    - axe Y secondaire à droite

    Sans crossAx explicite, Excel peut garder l'axe secondaire à l'intérieur du graphe
    ou ignorer axPos selon les versions."""
    try:
        chart_gauche.x_axis.axId = axe_x_id
        chart_gauche.y_axis.axId = axe_gauche_id
        chart_gauche.x_axis.crossAx = axe_gauche_id
        chart_gauche.y_axis.crossAx = axe_x_id
        chart_gauche.y_axis.axPos = "l"
        chart_gauche.x_axis.axPos = "b"
        chart_gauche.y_axis.delete = False
        chart_gauche.x_axis.delete = False
    except Exception:
        pass

    try:
        chart_droit.x_axis.axId = axe_x_id
        chart_droit.y_axis.axId = axe_droit_id
        chart_droit.x_axis.crossAx = axe_droit_id
        chart_droit.y_axis.crossAx = axe_x_id
        chart_droit.y_axis.axPos = "r"
        chart_droit.y_axis.crosses = "max"
        chart_droit.y_axis.delete = False
        chart_droit.x_axis.delete = True
    except Exception:
        pass

def creer_deux_graphes_sur_feuil1(ws) -> None:
    """Cree les deux graphes sur Feuil1 avec de vrais axes Y aux deux bords :
    - graphe 1 : Pr/ETP/ETR à gauche, Températures à droite
    - graphe 2 : Excédent/Déficit à gauche, RFU à droite
    + tableaux visibles sous les graphes."""
    try:
        ws._charts = []
    except Exception:
        pass

    base_row = 60
    base_col = 27  # AA

    headers = [
        "Mois", "Précipitations", "ETP", "ETR", "Températures",
        "Excedent", "Déficit", "RFU",
    ]
    for c, h in enumerate(headers, start=base_col):
        ws.cell(row=base_row, column=c).value = h

    mois_visibles = []
    for i, (nom_mois, _) in enumerate(MOIS_HYDRO, start=1):
        source_row = 5 + i
        r = base_row + i

        libelle = _mois_libelle_court_table(nom_mois)
        mois_visibles.append(libelle)

        ws.cell(row=r, column=base_col).value = libelle
        ws.cell(row=r, column=base_col + 1).value = ws.cell(source_row, 6).value   # Pr
        ws.cell(row=r, column=base_col + 2).value = ws.cell(source_row, 5).value   # ETP
        ws.cell(row=r, column=base_col + 3).value = ws.cell(source_row, 11).value  # ETR
        ws.cell(row=r, column=base_col + 4).value = ws.cell(source_row, 2).value   # Températures
        ws.cell(row=r, column=base_col + 5).value = ws.cell(source_row, 13).value  # Exc
        ws.cell(row=r, column=base_col + 6).value = ws.cell(source_row, 12).value  # Def
        ws.cell(row=r, column=base_col + 7).value = ws.cell(source_row, 10).value  # RFU

    for r in range(base_row + 1, base_row + 13):
        for c in range(base_col + 1, base_col + 8):
            ws.cell(row=r, column=c).number_format = "0.0#"

    cat = Reference(ws, min_col=base_col, min_row=base_row + 1, max_row=base_row + 12)

    
    # Graphique 1 : axe gauche + axe droit réel
    
    c1 = LineChart()
    c1.title = None
    c1.y_axis.title = "Pr, ETP et ETR en mm"
    c1.x_axis.title = None
    c1.height = 8.8
    c1.width = 14.8
    c1.style = 2
    c1.legend = None

    for col in [base_col + 1, base_col + 2, base_col + 3]:
        data = Reference(ws, min_col=col, min_row=base_row, max_row=base_row + 12)
        c1.add_data(data, titles_from_data=True)
    c1.set_categories(cat)

    if len(c1.ser) >= 1:
        _forcer_ligne_droite_et_marqueur(c1.ser[0], "0000FF", 17000, "triangle", 5)
    if len(c1.ser) >= 2:
        _forcer_ligne_droite_et_marqueur(c1.ser[1], "FF00FF", 17000, "square", 5)
    if len(c1.ser) >= 3:
        _forcer_ligne_droite_et_marqueur(c1.ser[2], "FFC000", 17000, "square", 5)

    c1_droit = LineChart()
    c1_droit.y_axis.title = "Températures en °C"
    data_temp = Reference(ws, min_col=base_col + 4, min_row=base_row, max_row=base_row + 12)
    c1_droit.add_data(data_temp, titles_from_data=True)
    c1_droit.set_categories(cat)
    if len(c1_droit.ser) >= 1:
        _forcer_ligne_droite_et_marqueur(c1_droit.ser[0], "FF0000", 17000, "diamond", 5)

    _configurer_axes_gauche_droite(
        chart_gauche=c1,
        chart_droit=c1_droit,
        axe_x_id=100,
        axe_gauche_id=101,
        axe_droit_id=102,
    )
    c1 += c1_droit
    _ajouter_table_donnees(c1)

    
    # Graphique 2 : axe gauche + axe droit réel
    
    c2 = LineChart()
    c2.title = None
    c2.y_axis.title = "Excédent et Déficit en mm"
    c2.x_axis.title = None
    c2.height = 8.8
    c2.width = 14.8
    c2.style = 2
    c2.legend = None

    # Gauche : Excédent + Déficit
    for col in [base_col + 5, base_col + 6]:
        data = Reference(ws, min_col=col, min_row=base_row, max_row=base_row + 12)
        c2.add_data(data, titles_from_data=True)
    c2.set_categories(cat)

    if len(c2.ser) >= 1:
        _forcer_ligne_droite_et_marqueur(c2.ser[0], "FF00FF", 17000, "square", 5)
    if len(c2.ser) >= 2:
        _forcer_ligne_droite_et_marqueur(c2.ser[1], "FF6600", 17000, "triangle", 5)

    # Droite : RFU
    c2_droit = LineChart()
    c2_droit.y_axis.title = "RFU en mm"
    data_rfu = Reference(ws, min_col=base_col + 7, min_row=base_row, max_row=base_row + 12)
    c2_droit.add_data(data_rfu, titles_from_data=True)
    c2_droit.set_categories(cat)
    if len(c2_droit.ser) >= 1:
        _forcer_ligne_droite_et_marqueur(c2_droit.ser[0], "0000FF", 17000, "x", 5)

    _configurer_axes_gauche_droite(
        chart_gauche=c2,
        chart_droit=c2_droit,
        axe_x_id=200,
        axe_gauche_id=201,
        axe_droit_id=202,
    )
    c2 += c2_droit
    _ajouter_table_donnees(c2)

    # Placement identique au modele de reference.
    ws.add_chart(c1, "A20")
    ws.add_chart(c2, "O20")

    # Tableaux visibles sous graphiques
    table1_formules = [
        [ws.cell(r, 6).value for r in range(6, 18)],
        [ws.cell(r, 5).value for r in range(6, 18)],
        [ws.cell(r, 11).value for r in range(6, 18)],
        [ws.cell(r, 2).value for r in range(6, 18)],
    ]
    _ecrire_tableau_graphique(
        ws,
        start_row=33,
        start_col=1,
        titre_lignes=["Précipitations", "ETP", "ETR", "Températures"],
        colonnes_mois=mois_visibles,
        formules=table1_formules,
    )

    table2_formules = [
        [ws.cell(r, 13).value for r in range(6, 18)],
        [ws.cell(r, 12).value for r in range(6, 18)],
        [ws.cell(r, 10).value for r in range(6, 18)],
    ]
    _ecrire_tableau_graphique(
        ws,
        start_row=33,
        start_col=15,
        titre_lignes=["Excedent", "Déficit", "RFU"],
        colonnes_mois=mois_visibles,
        formules=table2_formules,
    )

    try:
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.merge_cells("A40:M40")
        ws.merge_cells("O40:AA40")
        ws["A40"] = "Moyenne pluriannuelle des éléments climatiques"
        ws["O40"] = "RFU, Excédent et Déficit"
        for ref in ("A40", "O40"):
            ws[ref].alignment = Alignment(horizontal="center", vertical="center")
            ws[ref].font = Font(name="Times New Roman", size=10, bold=True)
        ws.row_dimensions[40].height = 20
        for row in range(60, 73):
            ws.row_dimensions[row].hidden = True
        for col in range(27, 35):
            ws.column_dimensions[get_column_letter(col)].hidden = True
        ws.print_area = "A1:AA41"
    except Exception:
        pass

def obtenir_excel_com():
    """Ouvre Microsoft Excel une seule fois pour tout le traitement."""
    global _EXCEL_COM_APP

    if _EXCEL_COM_APP is not None:
        return _EXCEL_COM_APP

    import win32com.client as win32

    _EXCEL_COM_APP = win32.DispatchEx("Excel.Application")
    _EXCEL_COM_APP.Visible = False
    _EXCEL_COM_APP.DisplayAlerts = False
    return _EXCEL_COM_APP

def fermer_excel_com_si_ouvert():
    """
    Ferme Excel ouvert par le script à la fin du traitement.
    """
    global _EXCEL_COM_APP

    if _EXCEL_COM_APP is not None:
        try:
            _EXCEL_COM_APP.Quit()
        except Exception:
            pass
        _EXCEL_COM_APP = None

def ajouter_graphes_excel_originaux_si_possible(fichier_sortie: Path) -> None:
    """Post-traitement Windows/Excel :
    recrée les deux graphes avec Microsoft Excel lui-même."""
    try:
        import win32com.client as win32
    except Exception:
        print("  Graphes Excel natifs non appliques : pywin32 indisponible.")
        return

    def rgb(r, g, b):
        return int(r) + int(g) * 256 + int(b) * 65536

    # Constantes Excel utiles
    xlLineMarkers = 65
    xlPrimary = 1
    xlSecondary = 2
    xlCategory = 1
    xlValue = 2

    xlMarkerStyleSquare = 1
    xlMarkerStyleDiamond = 2
    xlMarkerStyleTriangle = 3
    xlMarkerStyleX = -4168

    excel = None
    wb = None

    try:
        # Excel est ouvert une seule fois pour tout le lot de stations.
        excel = obtenir_excel_com()

        wb = excel.Workbooks.Open(str(fichier_sortie))
        ws = wb.Worksheets("Feuil1")

        # Recalcul rapide des formules du classeur courant.
        # CalculateFullRebuild est volontairement évité car il ralentit beaucoup
        # quand il y a plusieurs stations.
        try:
            wb.Application.Calculate()
        except Exception:
            try:
                excel.Calculate()
            except Exception:
                pass

        # Supprimer tous les graphiques existants produits par openpyxl.
        try:
            for i in range(ws.ChartObjects().Count, 0, -1):
                ws.ChartObjects(i).Delete()
        except Exception:
            pass

        def styliser_serie(serie, couleur, marker_style, marker_size=5, weight=1.25):
            try:
                serie.ChartType = xlLineMarkers
            except Exception:
                pass

            try:
                serie.Format.Line.ForeColor.RGB = couleur
                serie.Format.Line.Weight = weight
            except Exception:
                pass

            try:
                serie.MarkerStyle = marker_style
                serie.MarkerSize = marker_size
                serie.MarkerForegroundColor = couleur
                serie.MarkerBackgroundColor = couleur
            except Exception:
                pass

            try:
                serie.Smooth = False
            except Exception:
                pass

        def ajouter_serie(chart, nom, x_range, y_range, couleur, marker_style, axis_group=xlPrimary):
            serie = chart.SeriesCollection().NewSeries()
            serie.Name = nom
            serie.XValues = x_range
            serie.Values = y_range
            serie.AxisGroup = axis_group
            styliser_serie(serie, couleur, marker_style)
            return serie

        def configurer_tableau_donnees(chart):
            # Data Table Excel natif : c'est cela qui fusionne visuellement l'axe X et le tableau.
            try:
                chart.HasDataTable = True
                chart.DataTable.ShowLegendKey = True
                chart.DataTable.HasBorderHorizontal = True
                chart.DataTable.HasBorderVertical = True
                chart.DataTable.HasBorderOutline = True
                chart.HasLegend = False
            except Exception:
                pass

            try:
                chart.Axes(xlCategory).TickLabels.Orientation = 0
                chart.Axes(xlCategory).TickLabels.Font.Size = 8
            except Exception:
                pass

            try:
                chart.ChartArea.Font.Name = "Times New Roman"
                chart.ChartArea.Font.Size = 9
            except Exception:
                pass

        def configurer_axes(chart, titre_gauche, titre_droite=None):
            try:
                ax = chart.Axes(xlValue, xlPrimary)
                ax.HasTitle = True
                ax.AxisTitle.Text = titre_gauche
                ax.AxisTitle.Font.Bold = True
                ax.TickLabels.Font.Size = 9
                ax.MajorGridlines.Format.Line.ForeColor.RGB = rgb(0, 0, 0)
                ax.MajorGridlines.Format.Line.Weight = 0.75
            except Exception:
                pass

            if titre_droite:
                try:
                    ax2 = chart.Axes(xlValue, xlSecondary)
                    ax2.HasTitle = True
                    ax2.AxisTitle.Text = titre_droite
                    ax2.AxisTitle.Font.Bold = True
                    ax2.TickLabels.Font.Size = 9
                except Exception:
                    pass

            try:
                chart.Axes(xlCategory).HasTitle = False
            except Exception:
                pass

        def creer_graphe_1():
            left = ws.Range("A20").Left
            top = ws.Range("A20").Top
            width = ws.Range("A20:M39").Width
            height = ws.Range("A20:M39").Height

            obj = ws.ChartObjects().Add(left, top, width, height)
            chart = obj.Chart
            chart.ChartType = xlLineMarkers
            chart.HasTitle = False

            x = ws.Range("A6:A17")

            ajouter_serie(chart, "Précipitations", x, ws.Range("F6:F17"), rgb(0, 0, 255), xlMarkerStyleTriangle, xlPrimary)
            ajouter_serie(chart, "ETP", x, ws.Range("E6:E17"), rgb(255, 0, 255), xlMarkerStyleSquare, xlPrimary)
            ajouter_serie(chart, "ETR", x, ws.Range("K6:K17"), rgb(255, 192, 0), xlMarkerStyleSquare, xlPrimary)
            ajouter_serie(chart, "Températures", x, ws.Range("B6:B17"), rgb(255, 0, 0), xlMarkerStyleDiamond, xlSecondary)

            configurer_axes(chart, "Pr, ETP et ETR en mm", "Températures en °C")
            configurer_tableau_donnees(chart)

        def creer_graphe_2():
            left = ws.Range("O20").Left
            top = ws.Range("O20").Top
            width = ws.Range("O20:AA39").Width
            height = ws.Range("O20:AA39").Height

            obj = ws.ChartObjects().Add(left, top, width, height)
            chart = obj.Chart
            chart.ChartType = xlLineMarkers
            chart.HasTitle = False

            x = ws.Range("A6:A17")

            ajouter_serie(chart, "Excedent", x, ws.Range("M6:M17"), rgb(255, 0, 255), xlMarkerStyleSquare, xlPrimary)
            ajouter_serie(chart, "Déficit", x, ws.Range("L6:L17"), rgb(255, 102, 0), xlMarkerStyleTriangle, xlPrimary)
            ajouter_serie(chart, "RFU", x, ws.Range("J6:J17"), rgb(0, 0, 255), xlMarkerStyleX, xlSecondary)

            configurer_axes(chart, "Excédent et Déficit en mm", "RFU en mm")
            configurer_tableau_donnees(chart)


        creer_graphe_1()
        creer_graphe_2()

        # Supprimer uniquement les tableaux auxiliaires visibles en arriere-plan
        # (ceux ecrits par openpyxl sous les graphiques).
        # On conserve bien les tableaux integres aux graphiques Excel natifs.
        try:
            ws.Range("A33:M37").ClearContents()
            ws.Range("A33:M37").Borders.LineStyle = 0
        except Exception:
            pass

        try:
            ws.Range("O33:AA36").ClearContents()
            ws.Range("O33:AA36").Borders.LineStyle = 0
        except Exception:
            pass

        # Masquer aussi la zone technique de travail servant a openpyxl si besoin.
        try:
            ws.Range("AA60:AH72").EntireRow.Hidden = True
            ws.Range("AA60:AH72").EntireColumn.Hidden = True
        except Exception:
            pass

        # Titres sous les graphes, dans des cellules distinctes.
        try:
            ws.Range("A40:M40").Merge()
        except Exception:
            pass
        try:
            ws.Range("O40:AA40").Merge()
        except Exception:
            pass

        try:
            ws.Range("A40").Value = "Moyenne pluriannuelle des éléments climatiques"
            ws.Range("A40").HorizontalAlignment = -4108  # center
            ws.Range("A40").VerticalAlignment = -4108
            ws.Range("A40").Font.Bold = True
            ws.Range("A40").Font.Name = "Times New Roman"
            ws.Range("A40").Font.Size = 10
        except Exception:
            pass

        try:
            ws.Range("O40").Value = "RFU, Excédent et Déficit"
            ws.Range("O40").HorizontalAlignment = -4108  # center
            ws.Range("O40").VerticalAlignment = -4108
            ws.Range("O40").Font.Bold = True
            ws.Range("O40").Font.Name = "Times New Roman"
            ws.Range("O40").Font.Size = 10
        except Exception:
            pass

        try:
            ws.Rows("40:40").RowHeight = 20
        except Exception:
            pass

        # Mise en page sur une seule page.
        try:
            ws.PageSetup.Orientation = 2  # paysage
            ws.PageSetup.Zoom = False
            ws.PageSetup.FitToPagesWide = 1
            ws.PageSetup.FitToPagesTall = 1
            ws.PageSetup.PrintArea = "$A$1:$AA$41"
        except Exception:
            pass

        wb.Save()
        wb.Close(SaveChanges=True)
        wb = None

        print("  Graphes Excel natifs appliques avec tableaux de donnees.")

    except Exception as e:
        print("  Graphes Excel natifs non appliques : " + str(e))
        try:
            if wb is not None:
                wb.Close(SaveChanges=False)
        except Exception:
            pass


_EXCEL_COM_APP = None
