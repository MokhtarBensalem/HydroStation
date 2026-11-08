# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import time
from pathlib import Path

from .support import (
    EXTENSIONS_TABLEAUX, creer_dossier_unique, extraire_pjmax_depuis_fichier,
    nettoyer_chemin, obtenir_fichiers_depuis_chemin, sortie_unique_gumbel,
)
from modules.shared import (
    emit_hydro_marker, emit_station_report, clear_file_read_cache,
    cancel_requested, resolve_station_metadata,
    format_years_used,
)
from .table import creer_fichier_gumbel_depuis_modele


def _metadata_station_gumbel(fichier: Path):
    """Use the same station-name/code recognition as Bilan Hydrique."""
    try:
        from modules.bilan_hydrique_parts.support import extraire_identifiants_station
        identity = extraire_identifiants_station(Path(fichier), inclure_contenu=True)
    except Exception:
        identity = {}
    return resolve_station_metadata([identity], fallback_name="")


def traiter_un_fichier(
    fichier: Path, template_path: Path, dossier_resultat: Path, utiliser_hydro: bool,
    split_tables: bool, inclure_graphiques: bool = True, reserved=None, result_path=None,
    prepared_data=None,
) -> bool:
    debut_fichier = time.perf_counter()
    print("Lecture du fichier : " + fichier.name)

    if prepared_data is None:
        metadata_station = _metadata_station_gumbel(fichier)
        table_pjmax, diagnostics = extraire_pjmax_depuis_fichier(fichier, utiliser_hydro=utiliser_hydro)
        years_used = sorted({int(value) for value in table_pjmax["annee"].tolist()}) if len(table_pjmax) else []
    else:
        metadata_station = prepared_data["metadata"]
        table_pjmax = prepared_data["table_pjmax"]
        diagnostics = prepared_data["diagnostics"]
        years_used = prepared_data["years_used"]

    if result_path is not None:
        result_path.update(metadata_station)
        result_path["years_used"] = years_used

    if len(table_pjmax) < 3:
        print("Fichier ignore : nombre de valeurs insuffisant pour Gumbel.")
        return False

    fichier_sortie = sortie_unique_gumbel(dossier_resultat, "gumbel_" + fichier.stem, reserved)
    excel_path = creer_fichier_gumbel_depuis_modele(
        table_pjmax=table_pjmax, diagnostics=diagnostics, template_path=template_path,
        fichier_sortie=fichier_sortie, split_tables=split_tables,
        inclure_graphiques=inclure_graphiques,
        station_name=metadata_station["station_name"], station_code=metadata_station["station_code"],
        years_used=years_used,
    )
    if result_path is not None:
        result_path["path"] = str(excel_path)
    print("Fichier Excel Gumbel cree : " + str(excel_path))
    print("Station :", metadata_station["station_name"], "Code :", metadata_station["station_code"])
    print("Annees utilisees :", format_years_used(years_used))
    print("Nombre de valeurs utilisees : " + str(len(table_pjmax)))
    print("Temps station : %.2f secondes" % (time.perf_counter() - debut_fichier))
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extraction automatique PJmax et remplissage du modele Tab Gumbel 2.")
    parser.add_argument("--data", help="Fichier ou dossier contenant les donnees pluviometriques.")
    parser.add_argument("--outdir", help="Dossier parent pour enregistrer les resultats.")
    parser.add_argument(
        "--annee-civile",
        action="store_true",
        help="Utiliser l'annee civile Jan-Dec au lieu de l'annee hydrologique Sep-Aout.",
    )
    parser.add_argument("--no-split", action="store_true", help="Ne pas creer les 3 feuilles separees.")
    parser.add_argument("--split", action="store_true", help="Creer les feuilles separees (option plus lente).")
    parser.add_argument("--no-charts", action="store_true", help="Supprimer les graphiques Excel du résultat.")
    parser.add_argument("--charts", action="store_true", help="Conserver les graphiques Excel du résultat.")
    parser.add_argument("--cancel-file", default="", help="Fichier temoin pour annulation entre stations")
    return parser


def main() -> None:
    print(
        "Traitement Gumbel automatique des PJmax annuels - "
        "v18 correction finale formules - v17 correction #NUM et Taille n dynamique"
    )
    print("------------------------------------------------")
    print("Formats acceptes pour les donnees : " + ", ".join(EXTENSIONS_TABLEAUX))
    print("Calcul Gumbel et graphique generes directement en Python (sans modele Excel externe)")
    args = build_parser().parse_args()

    chemin_source = nettoyer_chemin(args.data or input("Chemin du fichier ou dossier contenant les donnees : "))
    chemin_sortie_parent = nettoyer_chemin(
        args.outdir or input("Chemin du dossier parent pour enregistrer les resultats : ")
    )
    fichiers = obtenir_fichiers_depuis_chemin(chemin_source)

    dossier_sortie_parent = Path(chemin_sortie_parent)
    dossier_sortie_parent.mkdir(parents=True, exist_ok=True)
    dossier_resultat = creer_dossier_unique(dossier_sortie_parent, "Gumbel résultat")
    print("Dossier de sortie cree : " + str(dossier_resultat))

    template_path = None
    utiliser_hydro = not args.annee_civile
    split_tables = bool(args.split) and not args.no_split
    inclure_graphiques = not args.no_charts

    erreurs = 0
    ignores = 0
    reussis = 0
    cancelled = False
    reserved = set()
    rows = []
    prepared = []

    # Preflight identique au principe du Bilan: le total affiche uniquement les
    # fichiers ayant assez de PJmax pour entrer réellement dans le calcul.
    for index_check, fichier in enumerate(fichiers, 1):
        emit_hydro_marker("PREFLIGHT", f"{index_check - 1}/{len(fichiers)}")
        row = {"fichier": str(fichier), "nom_station": "", "code_station": "",
               "annees_utilisees": "", "resultat": "", "motif": "", "sortie": ""}
        if cancel_requested(args.cancel_file):
            cancelled = True
            row.update(resultat="ANNULE", motif="Annulation demandee")
            ignores += 1
            rows.append(row)
            continue
        try:
            metadata = _metadata_station_gumbel(fichier)
            table_pjmax, diagnostics = extraire_pjmax_depuis_fichier(fichier, utiliser_hydro=utiliser_hydro)
            years_used = sorted({int(value) for value in table_pjmax["annee"].tolist()}) if len(table_pjmax) else []
            row["nom_station"] = metadata["station_name"]
            row["code_station"] = metadata["station_code"]
            row["annees_utilisees"] = format_years_used(years_used)
            if len(table_pjmax) < 3:
                ignores += 1
                row.update(resultat="IGNORE", motif="Moins de 3 valeurs PJmax valides")
                rows.append(row)
                continue
            prepared.append({
                "fichier": fichier, "row": row, "metadata": metadata,
                "table_pjmax": table_pjmax, "diagnostics": diagnostics, "years_used": years_used,
            })
        except Exception as exc:
            erreurs += 1
            row.update(resultat="ERREUR", motif=str(exc))
            rows.append(row)
            print("Fichier Gumbel non valide :", fichier.name, "-", exc)

    emit_hydro_marker("PREFLIGHT", f"{len(fichiers)}/{len(fichiers)}")
    total_valid = len(prepared)
    print(f"Stations valides pour le calcul : {total_valid}")
    emit_hydro_marker("TOTAL", total_valid)

    for index_station, item in enumerate(prepared, 1):
        fichier = item["fichier"]
        row = item["row"]
        try:
            if cancel_requested(args.cancel_file):
                cancelled = True
                row.update(resultat="ANNULE", motif="Annulation demandee")
                ignores += 1
            else:
                created = {}
                cree = traiter_un_fichier(
                    fichier=fichier, template_path=template_path, dossier_resultat=dossier_resultat,
                    utiliser_hydro=utiliser_hydro, split_tables=split_tables,
                    inclure_graphiques=inclure_graphiques, reserved=reserved, result_path=created,
                    prepared_data=item,
                )
                if cree:
                    reussis += 1
                    row["resultat"] = "OK"
                    row["sortie"] = created.get("path", "")
                else:
                    ignores += 1
                    row.update(resultat="IGNORE", motif="Moins de 3 valeurs PJmax valides")
        except Exception as exc:
            erreurs += 1
            row.update(resultat="ERREUR", motif=str(exc))
            print("Erreur dans le fichier : " + fichier.name)
            print(str(exc))
        finally:
            rows.append(row)
            emit_hydro_marker("PROGRESS", f"{index_station}/{total_valid}")

    fields = [
        "fichier",
        "nom_station",
        "code_station",
        "annees_utilisees",
        "resultat",
        "motif",
        "sortie",
    ]
    emit_station_report(fields, rows)
    print("Bilan traitement : %d succes, %d ignores/annules, %d erreurs" % (reussis, ignores, erreurs))
    emit_hydro_marker("ERRORS", erreurs + ignores)
    emit_hydro_marker("SKIPPED", ignores)
    if cancelled:
        emit_hydro_marker("CANCELLED")
    clear_file_read_cache()
    emit_hydro_marker("DONE")
    if cancelled:
        raise SystemExit(3)
    if erreurs or ignores:
        raise SystemExit(2)
