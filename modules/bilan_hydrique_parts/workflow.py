# -*- coding: utf-8 -*-

from pathlib import Path
import json

from .support import (
    _ASSOCIATION_TRACE, annulation_demandee, chemin_sortie_unique,
    creer_dossier_unique, extraire_identifiants_station,
    extraire_tp_pr_avec_annees_communes, nettoyer_chemin, nom_station_sortie,
    obtenir_fichiers_depuis_chemin, planifier_associations,
    planifier_diffusion_fichier_unique, planifier_fichier_avec_dossier,
    associer_fichiers_temperature_precipitation_intelligent,
    extraire_series_annuelles_depuis_fichier, ordre_mois_hydro_noms,
    _source_correspondance_identites,
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
    identites = [dict(identite_temperature or {}), dict(identite_precipitation or {})]
    source_match = _source_correspondance_identites(*identites)
    if source_match in {
        "nom_fichier_vers_contenu", "contenu_vers_nom_fichier", "nom_fichier"
    }:
        # Ignore a multi-station false positive only when that file has no
        # internal station identifiers and the other side supplies the exact
        # matching station identity.
        for identity in identites:
            internal = bool(
                identity.get("codes_logo") or identity.get("names_logo")
                or identity.get("codes_top10") or identity.get("names_top10")
            )
            if identity.get("multi_station") and not internal:
                identity["multi_station"] = False
    return resolve_station_metadata(
        identites, fallback_name=str(station or "")
    )


def _nouvelle_ligne(station, t_file, p_file):
    row = {
        "station": station, "nom_station": "", "code_station": "",
        "temperature": str(t_file), "precipitation": str(p_file),
        "annees_utilisees": "", "nb_annees": 0, "type_periode": "", "fichier_sortie": "",
        "resultat": "", "erreur": "", "identifiants_temperature": "",
        "identifiants_precipitation": "", "etape_correspondance": "", "identifiant_commun": "",
        "type_temperature_source": "inconnu", "couverture_precipitation": "",
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
            "manual=associations manuelles multiples fichier/fichier"
        ),
    )
    parser.add_argument("--manual-pairs-file", default="", help="JSON des associations manuelles multiples Temperature/Precipitation")
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

    emit_hydro_marker("PHASE", "Recherche et association des stations...")
    if pairing_mode == "manual":
        if args.manual_pairs_file:
            fichier_paires = Path(args.manual_pairs_file)
            if not fichier_paires.is_file():
                raise ValueError("Le fichier des associations manuelles est introuvable.")
            try:
                donnees_paires = json.loads(fichier_paires.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Impossible de lire les associations manuelles : {exc}") from exc
            if not isinstance(donnees_paires, list) or not donnees_paires:
                raise ValueError("Aucune association manuelle n'a été définie.")

            autorises_temp = {Path(p).resolve() for p in fichiers_temp}
            autorises_pr = {Path(p).resolve() for p in fichiers_pr}
            paires_lues = []
            utilises_temp, utilises_pr = set(), set()
            paires_uniques = set()
            for index, element in enumerate(donnees_paires, 1):
                if not isinstance(element, dict):
                    raise ValueError(f"Association manuelle n°{index} invalide.")
                t_file = Path(nettoyer_chemin(element.get("temperature", ""))).resolve()
                p_file = Path(nettoyer_chemin(element.get("precipitation", ""))).resolve()
                if t_file not in autorises_temp or p_file not in autorises_pr:
                    raise ValueError(f"Association manuelle n°{index} : fichier hors de la sélection.")
                cle_paire = (t_file, p_file)
                if cle_paire in paires_uniques:
                    raise ValueError(f"Association manuelle n°{index} : cette liaison existe déjà.")
                paires_uniques.add(cle_paire)
                # A file may participate in several different pairs. It is only
                # counted as linked once when the ignored-file report is built.
                utilises_temp.add(t_file)
                utilises_pr.add(p_file)
                station = str(element.get("station") or nom_station_sortie(t_file, p_file))
                paires_lues.append((station, t_file, p_file))
            pairs = paires_lues
            skips = []
            for fichier in sorted(autorises_temp - utilises_temp, key=lambda p: p.name.casefold()):
                skips.append({
                    "station": fichier.stem, "temperature": str(fichier), "precipitation": "",
                    "erreur": "Fichier température non relié dans le panneau manuel.",
                    "identifiants_temperature": "", "identifiants_precipitation": "",
                    "etape_correspondance": "liaison_manuelle", "identifiant_commun": "",
                    "type_temperature_source": "inconnu",
                })
            for fichier in sorted(autorises_pr - utilises_pr, key=lambda p: p.name.casefold()):
                skips.append({
                    "station": fichier.stem, "temperature": "", "precipitation": str(fichier),
                    "erreur": "Fichier précipitation non relié dans le panneau manuel.",
                    "identifiants_temperature": "", "identifiants_precipitation": "",
                    "etape_correspondance": "liaison_manuelle", "identifiant_commun": "",
                    "type_temperature_source": "inconnu",
                })
        else:
            if len(fichiers_temp) != 1 or len(fichiers_pr) != 1:
                raise ValueError(
                    "Le mode manual exige un fichier d'associations ou exactement un fichier de chaque côté."
                )
            pairs = [(nom_station_sortie(fichiers_temp[0], fichiers_pr[0]), fichiers_temp[0], fichiers_pr[0])]
            skips = []
    elif pairing_mode == "broadcast":
        pairs, skips = planifier_diffusion_fichier_unique(fichiers_temp, fichiers_pr)
    elif pairing_mode == "auto" and temp_is_file and rain_is_file:
        pairs = associer_fichiers_temperature_precipitation_intelligent(fichiers_temp, fichiers_pr)
        skips = []
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

            series_temperature = extraire_series_annuelles_depuis_fichier(t_file, "temperature")
            series_precipitation = extraire_series_annuelles_depuis_fichier(p_file, "precipitation")
            mois_attendus = ordre_mois_hydro_noms()
            couverture = {
                mois: sum(1 for annee in years if mois in series_precipitation.get(int(annee), {}))
                for mois in mois_attendus
            }
            row["couverture_precipitation"] = " | ".join(
                f"{mois}:{couverture[mois]}/{len(years)}" for mois in mois_attendus
            ) if years else "Non vérifiable"
            annees_completes = sum(
                1 for annee in years
                if all(mois in series_temperature.get(int(annee), {})
                       and mois in series_precipitation.get(int(annee), {})
                       for mois in mois_attendus)
            )
            if not years:
                row["type_periode"] = "normales_periode_non_verifiable"
            elif annees_completes == len(years):
                row["type_periode"] = "annees_communes_completes"
            else:
                row["type_periode"] = f"annees_communes_avec_donnees_manquantes ({annees_completes}/{len(years)} annees completes)"
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
        "couverture_precipitation",
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
