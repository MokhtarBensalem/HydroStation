# -*- coding: utf-8 -*-

from pathlib import Path

from .support import (
    _ASSOCIATION_TRACE, annulation_demandee, chemin_sortie_unique,
    creer_dossier_unique, extraire_identifiants_station,
    extraire_tp_pr_avec_annees_communes, nettoyer_chemin, nom_station_sortie,
    obtenir_fichiers_depuis_chemin, planifier_associations,
    planifier_diffusion_fichier_unique, planifier_fichier_avec_dossier,
)
from modules.shared import (
    emit_hydro_marker, emit_station_report, clear_file_read_cache,
    resolve_station_metadata, format_years_used,
)
from .calculations import valider_normales
from .curve import fermer_excel_com_si_ouvert
from .table import ecrire_bilan_hydrique_python


def _metadata_station_bilan(station, identite_temperature, identite_precipitation):
    """Build a readable station identity shared by both source files."""
    return resolve_station_metadata(
        [identite_temperature, identite_precipitation], fallback_name=str(station or "")
    )


def _nouvelle_ligne(station, t_file, p_file):
    row = {
        "station": station, "nom_station": "", "code_station": "",
        "temperature": str(t_file), "precipitation": str(p_file),
        "annees_utilisees": "", "nb_annees": 0, "type_periode": "", "fichier_sortie": "",
        "resultat": "", "erreur": "", "identifiants_temperature": "",
        "identifiants_precipitation": "", "etape_correspondance": "", "identifiant_commun": "",
        "type_temperature_source": "inconnu",
    }
    trace_key = (str(Path(t_file).resolve()), str(Path(p_file).resolve()))
    row.update(_ASSOCIATION_TRACE.get(trace_key, {}))
    return row


def main() -> None:
    """Batch processing with valid-station preflight, traceability and cancel."""
    import argparse
    parser = argparse.ArgumentParser(description="Bilan hydrique climatique moyen par station")
    parser.add_argument("temperature", nargs="?")
    parser.add_argument("precipitation", nargs="?")
    parser.add_argument("modele", nargs="?")
    parser.add_argument("sortie", nargs="?")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--charts", action="store_true")
    parser.add_argument(
        "--pairing-mode",
        choices=["auto", "sync", "broadcast", "manual"],
        default="auto",
        help=(
            "sync=associer par station; "
            "broadcast=diffuser le fichier unique; "
            "manual=paire fichier/fichier"
        ),
    )
    parser.add_argument("--cancel-file", default="", help="Chemin du signal d'annulation")
    args = parser.parse_args()

    temp_path = nettoyer_chemin(args.temperature or input("Chemin TEMPERATURE : "))
    rain_path = nettoyer_chemin(args.precipitation or input("Chemin PRECIPITATION : "))
    output_path = nettoyer_chemin(args.sortie or input("Dossier de sortie : "))
    fichiers_temp = obtenir_fichiers_depuis_chemin(temp_path)
    fichiers_pr = obtenir_fichiers_depuis_chemin(rain_path)

    temp_is_file = Path(temp_path).is_file()
    rain_is_file = Path(rain_path).is_file()
    pairing_mode = args.pairing_mode
    if pairing_mode == "auto":
        pairing_mode = "manual" if temp_is_file and rain_is_file else "sync"

    emit_hydro_marker("PHASE", "Recherche et association des stations...")
    if pairing_mode == "manual":
        if len(fichiers_temp) != 1 or len(fichiers_pr) != 1:
            raise ValueError("Le mode manual exige un fichier Temperature et un fichier Precipitation.")
        pairs = [(nom_station_sortie(fichiers_temp[0], fichiers_pr[0]), fichiers_temp[0], fichiers_pr[0])]
        skips = []
    elif pairing_mode == "broadcast":
        pairs, skips = planifier_diffusion_fichier_unique(fichiers_temp, fichiers_pr)
    elif temp_is_file != rain_is_file:
        pairs, skips = planifier_fichier_avec_dossier(
            fichiers_temp, fichiers_pr, fichier_est_temperature=temp_is_file
        )
    else:
        pairs, skips = planifier_associations(fichiers_temp, fichiers_pr)

    if not pairs and not skips:
        raise ValueError("Aucun fichier a traiter")

    output_root = Path(output_path)
    dossier_resultat = creer_dossier_unique(output_root, "bilan hydrique Tp Pr")
    print(f"Dossier de sortie: {dossier_resultat}")

    journal = []
    ignored = 0
    failed = 0
    success = 0
    cancelled = False
    reserved = set()

    # Les fichiers sans association restent dans le rapport, mais ne sont pas
    # comptes comme stations valides dans la barre de progression.
    for skip in skips:
        journal.append({
            "station": skip["station"],
            "nom_station": "", "code_station": "",
            "temperature": skip["temperature"],
            "precipitation": skip["precipitation"],
            "annees_utilisees": "", "nb_annees": 0, "type_periode": "",
            "fichier_sortie": "", "resultat": "IGNORE", "erreur": skip["erreur"],
            "identifiants_temperature": skip.get("identifiants_temperature", ""),
            "identifiants_precipitation": skip.get("identifiants_precipitation", ""),
            "etape_correspondance": skip.get("etape_correspondance", ""),
            "identifiant_commun": skip.get("identifiant_commun", ""),
            "type_temperature_source": skip.get("type_temperature_source", "inconnu"),
        })
        ignored += 1
        print(f"Fichier ignore: {skip['temperature'] or skip['precipitation']} : {skip['erreur']}")

    # Preflight: une station entre dans le total seulement si son couple est
    # associe, sa temperature est exploitable et Tp/Pr passent la validation.
    prepared = []
    for index_check, (station, t_file, p_file) in enumerate(pairs, 1):
        emit_hydro_marker("PREFLIGHT", f"{index_check - 1}/{len(pairs)}")
        row = _nouvelle_ligne(station, t_file, p_file)
        if annulation_demandee(args.cancel_file):
            cancelled = True
            row.update(resultat="ANNULE", erreur="Annulation demandee avant le traitement")
            ignored += 1
            journal.append(row)
            continue
        try:
            identite_temperature = extraire_identifiants_station(t_file, inclure_contenu=True)
            identite_precipitation = extraire_identifiants_station(p_file, inclure_contenu=True)
            metadata_station = _metadata_station_bilan(station, identite_temperature, identite_precipitation)
            row["nom_station"] = metadata_station["station_name"]
            row["code_station"] = metadata_station["station_code"]
            row["type_temperature_source"] = identite_temperature.get("type_temperature", "inconnu")

            if row["type_temperature_source"] == "minmax":
                ignored += 1
                row.update(
                    resultat="IGNORE",
                    erreur=(
                        "Fichier contenant uniquement des temperatures MIN/MAX; "
                        "Thornthwaite exige la temperature moyenne."
                    ),
                )
                journal.append(row)
                continue

            t, r, years = extraire_tp_pr_avec_annees_communes(t_file, p_file)
            valider_normales(t, r)
            row["annees_utilisees"] = format_years_used(years)
            row["nb_annees"] = len(years)
            row["type_periode"] = "annees_communes_completes" if years else "normales_periode_non_verifiable"
            prepared.append({
                "station": station, "t_file": t_file, "p_file": p_file,
                "row": row, "metadata": metadata_station, "tp": t, "pr": r, "years": years,
            })
        except Exception as exc:
            failed += 1
            row.update(resultat="ERREUR", erreur=str(exc))
            journal.append(row)
            print("Station non valide:", station, "-", exc)

    emit_hydro_marker("PREFLIGHT", f"{len(pairs)}/{len(pairs)}")
    total_valid = len(prepared)
    print(f"Stations valides pour le calcul : {total_valid}")
    emit_hydro_marker("TOTAL", total_valid)

    for index, item in enumerate(prepared, 1):
        row = item["row"]
        station = item["station"]
        if annulation_demandee(args.cancel_file):
            cancelled = True
            row.update(resultat="ANNULE", erreur="Annulation demandee avant l'export")
            ignored += 1
        else:
            try:
                output = chemin_sortie_unique(dossier_resultat, "bilan_hydrique_" + station, reserved)
                ecrire_bilan_hydrique_python(
                    output, item["tp"], item["pr"], inclure_graphes=args.charts and not args.fast,
                    station_name=item["metadata"]["station_name"],
                    station_code=item["metadata"]["station_code"], years_used=item["years"],
                )
                row.update(resultat="OK", fichier_sortie=str(output))
                success += 1
                print("Station OK:", station, "Annees:", row["annees_utilisees"], "Sortie:", output)
            except Exception as exc:
                failed += 1
                row.update(resultat="ERREUR", erreur=str(exc))
                print("Station ERREUR:", station, "-", exc)
        journal.append(row)
        emit_hydro_marker("PROGRESS", f"{index}/{total_valid}")

    fields = [
        "station",
        "nom_station",
        "code_station",
        "temperature",
        "precipitation",
        "identifiants_temperature",
        "identifiants_precipitation",
        "etape_correspondance",
        "identifiant_commun",
        "type_temperature_source",
        "annees_utilisees",
        "nb_annees",
        "type_periode",
        "fichier_sortie",
        "resultat",
        "erreur",
    ]
    emit_station_report(fields, journal)

    fermer_excel_com_si_ouvert()
    print(f"Bilan traitement: {success} succes, {ignored} ignores/annules, {failed} erreurs")
    emit_hydro_marker("ERRORS", ignored + failed)
    emit_hydro_marker("SKIPPED", ignored)
    if cancelled:
        emit_hydro_marker("CANCELLED")
    clear_file_read_cache()
    emit_hydro_marker("DONE")
    if cancelled:
        raise SystemExit(3)
    if failed or ignored:
        raise SystemExit(2)
