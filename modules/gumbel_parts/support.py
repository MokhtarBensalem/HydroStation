#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Traitement automatique Gumbel - PJmax annuel.

Extraction des PJmax, ajustement de Gumbel, intervalles de confiance, tableaux
et graphique sont calcules directement en Python. Aucun fichier Gumbel.xlsx
n'est requis au fonctionnement.
"""

from __future__ import annotations

import importlib.util
import re
import warnings
from datetime import datetime
from pathlib import Path
from typing import (
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
)

warnings.filterwarnings("ignore")

from modules.shared import EXTENSIONS_TABLEAUX, MOIS_HYDRO, installer_paquet


# ============================================================
# Verification des dependances importantes (aucune installation pendant le calcul)
# ============================================================

PAQUETS_OBLIGATOIRES = {
    "pandas": "pandas",
    "openpyxl": "openpyxl",
}




def paquet_existe(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def verifier_paquets_de_base() -> None:
    for module_name, package_name in PAQUETS_OBLIGATOIRES.items():
        if not paquet_existe(module_name):
            print(f"Le paquet {package_name} est manquant.")
            installer_paquet(package_name)
            raise RuntimeError(
                f"{package_name} est necessaire. Lancez install_requirements.bat puis relancez le programme."
            )


def verifier_xlrd() -> bool:
    if paquet_existe("xlrd"):
        return True
    print("Le paquet xlrd est manquant pour lire les fichiers .xls.")
    installer_paquet("xlrd")
    return False


def verifier_odf() -> bool:
    if paquet_existe("odf"):
        return True
    print("Le paquet odfpy est manquant pour lire les fichiers .ods.")
    installer_paquet("odfpy")
    return False


def verifier_pyxlsb() -> bool:
    if paquet_existe("pyxlsb"):
        return True
    print("Le paquet pyxlsb est manquant pour lire les fichiers .xlsb.")
    installer_paquet("pyxlsb")
    return False


verifier_paquets_de_base()

import pandas as pd  # noqa: E402


# ============================================================
# Parametres generaux
# ============================================================


# Ordre hydrologique courant en Algerie du Nord : septembre -> aout

NOMBRE_DECIMALES = 2
INTERVALLE_CONFIANCE = 90
PERIODES_RETOUR = [2, 5, 10, 20, 50, 100]
VALEURS_MANQUANTES = [-9999, -9999.0, -9999.9, -999, -999.0, -99, -99.0]

ANNEE_MIN = 1800
ANNEE_MAX = 2200


# ============================================================
# Fonctions de nettoyage et reconnaissance
# ============================================================



# Shared helper adapters and bounded file-read cache
from modules.shared import (
    clean_path as _shared_clean_path,
    normalize_text as _shared_normalize_text,
    compact_text as _shared_compact_text,
    parse_number as _shared_parse_number,
    integer_in_range as _shared_integer_in_range,
    create_unique_directory as _shared_create_unique_directory,
    list_directory_files as _shared_list_directory_files,
    dispatch_file_sheets as _shared_dispatch_file_sheets,
    filter_excel_sheets as _shared_filter_excel_sheets,
    cached_file_read as _shared_cached_file_read,
)


# ---------------------------------------------------------------------------
# Shared helpers: same implementation and interface as the Bilan support.
# Options preserve Gumbel-specific numeric and year validation rules.
# ---------------------------------------------------------------------------
def nettoyer_chemin(chemin: str) -> str:
    return _shared_clean_path(chemin)


def normaliser_texte(texte) -> str:
    return _shared_normalize_text(texte, none_as_empty=True, remove_braces=False)


def normaliser_compact(texte) -> str:
    return _shared_compact_text(texte, normaliser_texte)


def convertir_nombre(valeur) -> Optional[float]:
    return _shared_parse_number(
        valeur, missing_values=VALEURS_MANQUANTES, allow_negative=False,
        strip_outer_pipes=False, comma_mode="gumbel", reject_bool=True,
    )


def est_annee(valeur) -> bool:
    return _shared_integer_in_range(valeur, convertir_nombre, ANNEE_MIN, ANNEE_MAX)


def est_jour(valeur) -> bool:
    return _shared_integer_in_range(valeur, convertir_nombre, 1, 31)


def creer_dossier_unique(dossier_parent: Path, nom_base: str) -> Path:
    return _shared_create_unique_directory(dossier_parent, nom_base, create_parent=False)


def obtenir_fichiers_depuis_chemin(chemin_source: str | Path) -> List[Path]:
    chemin = Path(chemin_source)
    extensions = set(EXTENSIONS_TABLEAUX)
    if chemin.is_file():
        if chemin.suffix.lower() in extensions:
            if chemin.stat().st_size <= 0:
                raise Exception("Fichier vide ignore : " + str(chemin))
            return [chemin]
        raise Exception(
            "Format non supporte : " + str(chemin) +
            "\nFormats acceptes pour les donnees : " + ", ".join(EXTENSIONS_TABLEAUX)
        )
    if chemin.is_dir():
        fichiers = _shared_list_directory_files(
            chemin, extensions, skip_lock_files=False, skip_empty=True
        )
        if not fichiers:
            raise Exception(
                "Aucun fichier tableau trouve dans : " + str(chemin) +
                "\nFormats acceptes pour les donnees : " + ", ".join(EXTENSIONS_TABLEAUX)
            )
        return fichiers
    raise Exception("Chemin introuvable : " + str(chemin))


def _lire_excel_toutes_feuilles_uncached(fichier: Path) -> Dict[str, pd.DataFrame]:
    extension = fichier.suffix.lower()
    if extension == ".xls":
        if not verifier_xlrd():
            raise Exception(
                "Impossible de lire le fichier .xls car xlrd n'est pas installe. "
                "Executez: python -m pip install xlrd"
            )
        try:
            tables = pd.read_excel(fichier, sheet_name=None, header=None, engine="xlrd")
        except Exception as e:
            raise Exception(
                "Echec de lecture du fichier .xls avec xlrd. "
                "Ouvrez ce fichier dans Excel puis Enregistrer sous .xlsx, "
                f"ou installez xlrd a jour. Detail: {e}"
            )
    elif extension in [".xlsx", ".xlsm"]:
        tables = pd.read_excel(fichier, sheet_name=None, header=None, engine="openpyxl")
    elif extension == ".xlsb":
        if not verifier_pyxlsb():
            raise Exception("Impossible d'utiliser pyxlsb pour lire le fichier .xlsb.")
        tables = pd.read_excel(fichier, sheet_name=None, header=None, engine="pyxlsb")
    elif extension == ".ods":
        if not verifier_odf():
            raise Exception("Impossible d'utiliser odfpy pour lire le fichier .ods.")
        tables = pd.read_excel(fichier, sheet_name=None, header=None, engine="odf")
    else:
        raise Exception("Ce fichier n'est pas un fichier Excel.")
    return _shared_filter_excel_sheets(tables, normaliser_compact, keep_original=False)

def _lire_fichier_texte_uncached(fichier: Path) -> pd.DataFrame:
    if fichier.suffix.lower() == ".tsv":
        separateurs = ["\t", None]
    else:
        separateurs = [None, ";", ",", "\t"]
    erreurs = []
    for sep in separateurs:
        for enc in ["utf-8-sig", "utf-8", "latin1"]:
            try:
                return pd.read_csv(fichier, sep=sep, engine="python", header=None, encoding=enc)
            except Exception as e:
                erreurs.append(str(e))
    raise Exception("Impossible de lire le fichier texte/CSV : " + fichier.name)

def lire_excel_toutes_feuilles(fichier: Path) -> Dict[str, pd.DataFrame]:
    return _shared_cached_file_read(
        fichier, "gumbel_excel_sheets_v1",
        lambda: _lire_excel_toutes_feuilles_uncached(Path(fichier)),
    )


def lire_fichier_texte(fichier: Path) -> pd.DataFrame:
    return _shared_cached_file_read(
        fichier, "gumbel_text_table_v1",
        lambda: _lire_fichier_texte_uncached(Path(fichier)),
    )


def lire_fichier_en_feuilles(fichier: Path) -> Dict[str, pd.DataFrame]:
    return _shared_dispatch_file_sheets(
        fichier, lire_excel_toutes_feuilles, lire_fichier_texte
    )

def obtenir_annee(valeur) -> int:
    if isinstance(valeur, pd.Timestamp):
        return int(valeur.year)
    return int(float(valeur))
def colonne_ressemble_aux_annees(serie: pd.Series) -> bool:
    valeurs = [convertir_nombre(v) for v in serie]
    valeurs = [v for v in valeurs if v is not None]
    if not valeurs:
        return False
    compteur = 0
    for valeur in valeurs:
        if float(valeur).is_integer() and ANNEE_MIN <= int(valeur) <= ANNEE_MAX:
            compteur += 1
    return compteur / float(len(valeurs)) >= 0.70
def reconnaitre_mois(valeur) -> Optional[str]:
    texte = normaliser_compact(valeur)
    if texte.startswith("sep"):
        return "sept"
    if texte.startswith("oct"):
        return "oct"
    if texte.startswith("nov"):
        return "nov"
    if texte.startswith("dec") or texte.startswith("dez"):
        return "dec"
    if texte.startswith("jan"):
        return "janv"
    if texte.startswith("fev") or texte.startswith("feb"):
        return "fev"
    if texte.startswith("mar"):
        return "mars"
    if texte.startswith("avr") or texte.startswith("apr"):
        return "avril"
    if texte.startswith("mai") or texte.startswith("may"):
        return "mai"
    if texte.startswith("juin") or texte.startswith("jun"):
        return "juin"
    if texte.startswith("juil") or texte.startswith("jul"):
        return "juil"
    if texte.startswith("aout") or texte.startswith("aou") or texte.startswith("aug"):
        return "aout"
    return None
def mois_numero_depuis_nom(nom_mois: str) -> Optional[int]:
    for nom, numero in MOIS_HYDRO:
        if nom == nom_mois:
            return numero
    return None
def annee_hydrologique_depuis_date(date: datetime, utiliser_hydro: bool = True) -> int:
    if not utiliser_hydro:
        return int(date.year)
    if date.month >= 9:
        return int(date.year)
    return int(date.year) - 1
def texte_contient_pjmax(texte) -> bool:
    t = normaliser_texte(texte)
    c = normaliser_compact(texte)
    positif = (
        "pj max" in t or "pjmax" in c or "p max" in t or "pmax" in c or
        "pluie max" in t or "precipitation max" in t or "precipitations max" in t or
        "maximum journalier" in t or "max journalier" in t or "daily max" in t or
        ("max" in c and ("pj" in c or "precip" in c or "pluie" in c or "rain" in c))
    )
    interdit = (
        "totaux" in t or "total" in t or "apport" in t or
        "moy" in t or "moyenne" in t or "mean" in t or
        "min" in t or "minimum" in t or
        "jour du max" in t or "nbre" in t or "nombre" in t
    )
    return positif and not interdit
def texte_contient_precip_journaliere(texte) -> bool:
    t = normaliser_texte(texte)
    c = normaliser_compact(texte)
    interdit = ["total", "totaux", "apport", "moy", "moyenne", "mean", "min", "max", "nbre", "nombre"]
    if any(mot in c for mot in interdit):
        return False
    return any(mot in c for mot in ["pluie", "precip", "pj", "rain", "rr"])
def convertir_colonne_dates(serie: pd.Series) -> pd.Series:
    try:
        return pd.to_datetime(serie, errors="coerce", dayfirst=True)
    except Exception:
        return pd.Series([pd.NaT] * len(serie))
def detecter_colonne_date(df: pd.DataFrame) -> Optional[int]:
    meilleure_colonne = None
    meilleur_score = 0
    for col in range(df.shape[1]):
        texte_entete = " ".join(str(df.iloc[r, col]) for r in range(min(15, df.shape[0])) if pd.notna(df.iloc[r, col]))
        texte_norm = normaliser_texte(texte_entete)
        dates = convertir_colonne_dates(df.iloc[:, col])
        score = int(dates.notna().sum())
        # Eviter de confondre une colonne numerique de pluies avec des dates Excel.
        valeurs_numeriques = [convertir_nombre(v) for v in df.iloc[:, col].dropna().head(50)]
        valeurs_numeriques = [v for v in valeurs_numeriques if v is not None]
        if len(valeurs_numeriques) >= 5 and "date" not in texte_norm:
            if sum(v > 0 and v < 500 for v in valeurs_numeriques) / len(valeurs_numeriques) > 0.80:
                score = 0
        if "date" in texte_norm or "time" in texte_norm:
            score += 20
        if score > meilleur_score:
            meilleur_score = score
            meilleure_colonne = col
    return meilleure_colonne if meilleur_score >= 5 else None
def detecter_colonne_valeur_journaliere(df: pd.DataFrame, colonne_date: int) -> Optional[int]:
    meilleure_colonne = None
    meilleur_score = 0
    for col in range(df.shape[1]):
        if col == colonne_date:
            continue
        if colonne_ressemble_aux_annees(df.iloc[:, col]):
            continue
        texte_entete = " ".join(str(df.iloc[r, col]) for r in range(min(25, df.shape[0])) if pd.notna(df.iloc[r, col]))
        texte_norm = normaliser_texte(texte_entete)
        texte_compact = normaliser_compact(texte_entete)
        if any(mot in texte_norm for mot in ["total", "totaux", "apport", "moy", "moyenne", "min", "nbre", "nombre"]):
            continue
        serie = pd.to_numeric(df.iloc[:, col].astype(str).str.replace(",", ".", regex=False), errors="coerce")
        score = int(serie.notna().sum())
        if "pluie" in texte_norm or "precip" in texte_norm or "rain" in texte_norm:
            score += 30
        if "pj" in texte_compact or "rr" == texte_compact:
            score += 20
        # Une colonne de valeurs journalieres ne doit pas etre une colonne annuelle deja max si une vraie date existe.
        if "max" in texte_norm:
            score -= 5
        if score > meilleur_score:
            meilleur_score = score
            meilleure_colonne = col
    return meilleure_colonne if meilleur_score >= 5 else None
def extraire_depuis_table_longue(df: pd.DataFrame, nom_source: str, utiliser_hydro: bool) -> List[Dict]:
    colonne_date = detecter_colonne_date(df)
    if colonne_date is None:
        return []
    # The detector could mistake a table 'Annee;PJmax' for daily dates.
    # A year-only value is not an observation date, and assigning it to
    # January 1 falsely shifts the hydrological year back by one.
    raw_dates = df.iloc[:, colonne_date].dropna()
    if len(raw_dates) >= 3 and sum(est_annee(v) for v in raw_dates) / len(raw_dates) > 0.70:
        return []
    colonne_valeur = detecter_colonne_valeur_journaliere(df, colonne_date)
    if colonne_valeur is None:
        return []
    dates = convertir_colonne_dates(df.iloc[:, colonne_date])
    valeurs = pd.to_numeric(df.iloc[:, colonne_valeur].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    donnees = []
    for i in range(len(df)):
        date = dates.iloc[i]
        if pd.isna(date):
            continue
        valeur = convertir_nombre(valeurs.iloc[i])
        if valeur is None:
            continue
        if float(valeur).is_integer() and ANNEE_MIN <= int(valeur) <= ANNEE_MAX:
            continue
        annee = annee_hydrologique_depuis_date(pd.to_datetime(date), utiliser_hydro)
        donnees.append({
            "annee": annee,
            "valeur": valeur,
            "date": pd.to_datetime(date),
            "source": nom_source,
            "type_detection": "donnees journalieres date-valeur",
        })
    if not donnees:
        return []
    table = pd.DataFrame(donnees)
    idx = table.groupby("annee")["valeur"].idxmax()
    return table.loc[idx].sort_values("annee").to_dict("records")
def identifier_colonnes_mois(ligne: Sequence) -> Dict[str, int]:
    colonnes: Dict[str, int] = {}
    for col in range(len(ligne)):
        mois = reconnaitre_mois(ligne[col])
        if mois is not None:
            colonnes[mois] = col
    return colonnes
def detecter_colonne_jours(df: pd.DataFrame, ligne_mois: int) -> Optional[int]:
    meilleure_colonne = None
    meilleur_score = 0
    for col in range(min(8, df.shape[1])):
        score = 0
        for r in range(ligne_mois + 1, min(df.shape[0], ligne_mois + 45)):
            if est_jour(df.iloc[r, col]):
                score += 1
        if score > meilleur_score:
            meilleur_score = score
            meilleure_colonne = col
    return meilleure_colonne if meilleur_score >= 5 else None
def detecter_annee_autour(df: pd.DataFrame, ligne_mois: int) -> Optional[int]:
    debut = max(0, ligne_mois - 8)
    fin = min(df.shape[0], ligne_mois + 3)
    for i in range(debut, fin):
        for j in range(min(10, df.shape[1])):
            if est_annee(df.iloc[i, j]):
                return obtenir_annee(df.iloc[i, j])
    return None
def mois_sont_en_ordre_hydrologique(colonnes_mois: Dict[str, int]) -> bool:
    if "sept" in colonnes_mois and "janv" in colonnes_mois:
        return colonnes_mois["sept"] < colonnes_mois["janv"]
    return False
def extraire_depuis_table_journaliere_matrice(df: pd.DataFrame, nom_source: str, utiliser_hydro: bool) -> List[Dict]:
    donnees = []
    for i in range(df.shape[0]):
        ligne = df.iloc[i, :].tolist()
        colonnes_mois = identifier_colonnes_mois(ligne)
        if len(colonnes_mois) < 3:
            continue
        col_jour = detecter_colonne_jours(df, i)
        if col_jour is None:
            continue
        annee_bloc = detecter_annee_autour(df, i)
        if annee_bloc is None:
            continue
        ordre_hydro = mois_sont_en_ordre_hydrologique(colonnes_mois)
        lignes_jours = []
        for r in range(i + 1, min(df.shape[0], i + 45)):
            if est_jour(df.iloc[r, col_jour]):
                lignes_jours.append(r)
                continue
            if len(lignes_jours) > 0:
                break
        if not lignes_jours:
            continue
        for r in lignes_jours:
            jour = int(float(df.iloc[r, col_jour]))
            for nom_mois, col_mois in colonnes_mois.items():
                numero_mois = mois_numero_depuis_nom(nom_mois)
                if numero_mois is None:
                    continue
                valeur = convertir_nombre(df.iloc[r, col_mois])
                if valeur is None:
                    continue
                if float(valeur).is_integer() and ANNEE_MIN <= int(valeur) <= ANNEE_MAX:
                    continue
                if ordre_hydro and utiliser_hydro:
                    annee_hydro = annee_bloc
                    annee_reelle = annee_bloc if numero_mois >= 9 else annee_bloc + 1
                else:
                    annee_reelle = annee_bloc
                    try:
                        date_temp = datetime(annee_reelle, numero_mois, 1)
                        annee_hydro = annee_hydrologique_depuis_date(date_temp, utiliser_hydro)
                    except Exception:
                        annee_hydro = annee_bloc
                try:
                    date = datetime(annee_reelle, numero_mois, jour)
                except Exception:
                    date = None
                donnees.append({
                    "annee": annee_hydro,
                    "valeur": valeur,
                    "date": date,
                    "source": nom_source,
                    "type_detection": "donnees journalieres jours-mois",
                })
    if not donnees:
        return []
    table = pd.DataFrame(donnees)
    idx = table.groupby("annee")["valeur"].idxmax()
    return table.loc[idx].sort_values("annee").to_dict("records")
def extraire_depuis_table_journaliere_matrice_entete_global(df: pd.DataFrame, nom_source: str, utiliser_hydro: bool) -> List[Dict]:
    """
    Extrait les PJmax depuis les anciens fichiers .xls de type ANRH/Pluie :
    une seule ligne de mois en haut (sept, oct, ..., aout), puis plusieurs blocs
    annuels successifs : annee / jours 1..31 / PJ max / Totaux.

    Cette fonction evite la fausse detection date-valeur qui ne recuperait qu'une
    seule annee dans certains .xls. Les lignes PJ max, Totaux, Nbre jour pluv.
    ne sont pas utilisees si les valeurs journalieres 1..31 existent.
    """
    if df.empty:
        return []

    meilleure_ligne_mois = None
    colonnes_mois = {}
    meilleur_score = 0

    for i in range(min(df.shape[0], 80)):
        ligne = df.iloc[i, :].tolist()
        cols = identifier_colonnes_mois(ligne)
        score = len(cols)
        if score > meilleur_score:
            meilleur_score = score
            meilleure_ligne_mois = i
            colonnes_mois = cols

    if meilleure_ligne_mois is None or len(colonnes_mois) < 6:
        return []

    col_jour = None
    meilleur_score_jour = 0
    for col in range(min(5, df.shape[1])):
        score = 0
        for r in range(meilleure_ligne_mois + 1, df.shape[0]):
            if est_jour(df.iloc[r, col]):
                score += 1
        if score > meilleur_score_jour:
            meilleur_score_jour = score
            col_jour = col

    if col_jour is None or meilleur_score_jour < 20:
        return []

    ordre_hydro = mois_sont_en_ordre_hydrologique(colonnes_mois)
    donnees = []
    r = meilleure_ligne_mois + 1

    while r < df.shape[0]:
        if not est_annee(df.iloc[r, col_jour]):
            r += 1
            continue

        annee_bloc = obtenir_annee(df.iloc[r, col_jour])

        debut_jours = None
        for rr in range(r + 1, min(df.shape[0], r + 8)):
            if est_jour(df.iloc[rr, col_jour]):
                debut_jours = rr
                break

        if debut_jours is None:
            r += 1
            continue

        lignes_jours = []
        rr = debut_jours
        while rr < df.shape[0]:
            if est_jour(df.iloc[rr, col_jour]):
                lignes_jours.append(rr)
                rr += 1
                continue
            if lignes_jours:
                break
            rr += 1

        if len(lignes_jours) < 20:
            r = max(r + 1, rr)
            continue

        for ligne_jour in lignes_jours:
            jour = int(float(df.iloc[ligne_jour, col_jour]))
            for nom_mois, col_mois in colonnes_mois.items():
                numero_mois = mois_numero_depuis_nom(nom_mois)
                if numero_mois is None:
                    continue
                valeur = convertir_nombre(df.iloc[ligne_jour, col_mois])
                if valeur is None:
                    continue
                if float(valeur).is_integer() and ANNEE_MIN <= int(valeur) <= ANNEE_MAX:
                    continue

                if ordre_hydro and utiliser_hydro:
                    annee_hydro = annee_bloc
                    annee_reelle = annee_bloc if numero_mois >= 9 else annee_bloc + 1
                else:
                    annee_reelle = annee_bloc
                    try:
                        date_temp = datetime(annee_reelle, numero_mois, 1)
                        annee_hydro = annee_hydrologique_depuis_date(date_temp, utiliser_hydro)
                    except Exception:
                        annee_hydro = annee_bloc

                try:
                    date = datetime(annee_reelle, numero_mois, jour)
                except Exception:
                    date = None

                donnees.append({
                    "annee": annee_hydro,
                    "valeur": valeur,
                    "date": date,
                    "source": nom_source,
                    "type_detection": "donnees journalieres jours-mois entete globale xls",
                })

        r = max(rr, r + 1)

    if not donnees:
        return []

    table = pd.DataFrame(donnees)
    idx = table.groupby("annee")["valeur"].idxmax()
    return table.loc[idx].sort_values("annee").to_dict("records")
def ligne_est_pjmax(valeurs_ligne: Sequence) -> bool:
    texte = " ".join([str(v) for v in valeurs_ligne if pd.notna(v)])
    return texte_contient_pjmax(texte)
def extraire_depuis_ligne_pjmax(df: pd.DataFrame, nom_source: str, utiliser_hydro: bool) -> List[Dict]:
    donnees = []
    for i in range(df.shape[0]):
        ligne = df.iloc[i, :].tolist()
        colonnes_mois = identifier_colonnes_mois(ligne)
        if len(colonnes_mois) < 3:
            continue
        annee_bloc = detecter_annee_autour(df, i)
        if annee_bloc is None:
            continue
        ordre_hydro = mois_sont_en_ordre_hydrologique(colonnes_mois)
        for r in range(i + 1, min(df.shape[0], i + 70)):
            ligne_test = df.iloc[r, :].tolist()
            if not ligne_est_pjmax(ligne_test):
                continue
            valeurs_mensuelles = []
            for nom_mois, col_mois in colonnes_mois.items():
                numero_mois = mois_numero_depuis_nom(nom_mois)
                if numero_mois is None:
                    continue
                valeur = convertir_nombre(df.iloc[r, col_mois])
                if valeur is None:
                    continue
                if float(valeur).is_integer() and ANNEE_MIN <= int(valeur) <= ANNEE_MAX:
                    continue
                if ordre_hydro and utiliser_hydro:
                    annee_hydro = annee_bloc
                    annee_reelle = annee_bloc if numero_mois >= 9 else annee_bloc + 1
                else:
                    annee_reelle = annee_bloc
                    date_temp = datetime(annee_reelle, numero_mois, 1)
                    annee_hydro = annee_hydrologique_depuis_date(date_temp, utiliser_hydro)
                valeurs_mensuelles.append({
                    "annee": annee_hydro,
                    "annee_reelle": annee_reelle,
                    "mois": numero_mois,
                    "valeur": valeur,
                })
            if not valeurs_mensuelles:
                continue
            table = pd.DataFrame(valeurs_mensuelles)
            idx = table["valeur"].idxmax()
            ligne_max = table.loc[idx]
            donnees.append({
                "annee": int(ligne_max["annee"]),
                "valeur": float(ligne_max["valeur"]),
                "date": None,
                "source": nom_source,
                "type_detection": "ligne PJ max mensuelle",
            })
            break
    if not donnees:
        return []
    table = pd.DataFrame(donnees)
    idx = table.groupby("annee")["valeur"].idxmax()
    return table.loc[idx].sort_values("annee").to_dict("records")
def chercher_ligne_entete_max(df: pd.DataFrame) -> Optional[int]:
    for i in range(min(df.shape[0], 150)):
        textes = [str(df.iloc[i, j]) for j in range(df.shape[1])]
        ligne = " ".join(textes)
        if texte_contient_pjmax(ligne):
            return i
    return None
def detecter_colonnes_annee_et_max(df: pd.DataFrame, ligne_entete: int) -> Tuple[Optional[int], Optional[int]]:
    col_annee = None
    col_max = None
    for j in range(df.shape[1]):
        texte = str(df.iloc[ligne_entete, j])
        texte_norm = normaliser_texte(texte)
        if col_annee is None and ("annee" in texte_norm or "year" in texte_norm or texte_norm == "an"):
            col_annee = j
        if col_max is None and texte_contient_pjmax(texte):
            col_max = j

    if col_annee is None:
        meilleur_col, meilleur_score = None, 0
        for j in range(df.shape[1]):
            score = 0
            for r in range(ligne_entete + 1, min(df.shape[0], ligne_entete + 220)):
                if est_annee(df.iloc[r, j]):
                    score += 1
            if score > meilleur_score:
                meilleur_score = score
                meilleur_col = j
        if meilleur_score >= 2:
            col_annee = meilleur_col

    if col_max is None:
        meilleur_col, meilleur_score = None, 0
        for j in range(df.shape[1]):
            if j == col_annee:
                continue
            if colonne_ressemble_aux_annees(df.iloc[:, j]):
                continue
            texte_col = " ".join(str(df.iloc[r, j]) for r in range(max(0, ligne_entete - 2), min(df.shape[0], ligne_entete + 4)))
            texte_norm = normaliser_texte(texte_col)
            if any(mot in texte_norm for mot in ["total", "totaux", "apport", "moy", "moyenne", "min", "nbre", "nombre", "jour du max"]):
                continue
            score = 0
            for r in range(ligne_entete + 1, min(df.shape[0], ligne_entete + 220)):
                valeur = convertir_nombre(df.iloc[r, j])
                if valeur is not None:
                    if not (float(valeur).is_integer() and ANNEE_MIN <= int(valeur) <= ANNEE_MAX):
                        score += 1
            if score > meilleur_score:
                meilleur_score = score
                meilleur_col = j
        if meilleur_score >= 2:
            col_max = meilleur_col
    return col_annee, col_max
def extraire_depuis_table_max_direct(df: pd.DataFrame, nom_source: str) -> List[Dict]:
    ligne_entete = chercher_ligne_entete_max(df)
    if ligne_entete is None:
        return []
    col_annee, col_max = detecter_colonnes_annee_et_max(df, ligne_entete)
    if col_annee is None or col_max is None or col_annee == col_max:
        return []
    donnees = []
    for r in range(ligne_entete + 1, df.shape[0]):
        if not est_annee(df.iloc[r, col_annee]):
            continue
        annee = obtenir_annee(df.iloc[r, col_annee])
        valeur = convertir_nombre(df.iloc[r, col_max])
        if valeur is None:
            continue
        if float(valeur).is_integer() and ANNEE_MIN <= int(valeur) <= ANNEE_MAX:
            continue
        donnees.append({
            "annee": annee,
            "valeur": valeur,
            "date": None,
            "source": nom_source,
            "type_detection": "tableau annee PJ max",
        })
    if not donnees:
        return []
    table = pd.DataFrame(donnees)
    idx = table.groupby("annee")["valeur"].idxmax()
    return table.loc[idx].sort_values("annee").to_dict("records")
def header_from_row(raw: pd.DataFrame, header_row: int) -> List[str]:
    row = raw.iloc[header_row].tolist()
    headers = []
    used = {}
    for idx, val in enumerate(row):
        base = normaliser_texte(val)
        if not base:
            base = f"col_{idx + 1}"
        if base in used:
            used[base] += 1
            base = f"{base}_{used[base]}"
        else:
            used[base] = 1
        headers.append(base)
    return headers
def extraire_depuis_table_entete(raw: pd.DataFrame, nom_source: str, utiliser_hydro: bool) -> List[Dict]:
    resultats = []
    max_scan = min(25, len(raw))
    for h in range(max_scan):
        headers = header_from_row(raw, h)
        signal = 0
        for entete in headers:
            c = normaliser_compact(entete)
            if texte_contient_pjmax(entete) or texte_contient_precip_journaliere(entete):
                signal += 1
            if "annee" in c or "year" in c or "date" in c:
                signal += 1
        if signal < 2:
            continue
        df = raw.iloc[h + 1:].copy()
        df.columns = headers
        df = df.dropna(how="all")
        if df.empty:
            continue

        colonnes = list(df.columns)
        cols_pjmax = [c for c in colonnes if texte_contient_pjmax(c)]
        cols_pluie = [c for c in colonnes if texte_contient_precip_journaliere(c)]
        cols_annee = [c for c in colonnes if "annee" in normaliser_compact(c) or "year" in normaliser_compact(c) or normaliser_compact(c) == "an"]
        cols_date = [c for c in colonnes if "date" in normaliser_compact(c)]

        if not cols_annee:
            for c in colonnes:
                sample = df[c].dropna().head(80)
                if len(sample) >= 3 and sum(est_annee(v) for v in sample) / len(sample) >= 0.60:
                    cols_annee.append(c)
                    break

        if not cols_date:
            for c in colonnes:
                sample = df[c].dropna().head(80)
                if len(sample) >= 5:
                    dates = convertir_colonne_dates(sample)
                    if dates.notna().mean() >= 0.70 and not colonne_ressemble_aux_annees(sample):
                        # Eviter les colonnes de valeurs pluviometriques numeriques.
                        nums = [convertir_nombre(v) for v in sample]
                        nums = [v for v in nums if v is not None]
                        if len(nums) == 0 or sum(0 <= v <= 500 for v in nums) / len(nums) < 0.80:
                            cols_date.append(c)
                            break

        # Si table a colonne PJmax, on la privilegie. Sinon colonne pluie journaliere.
        value_cols = cols_pjmax if cols_pjmax else cols_pluie
        for value_col in value_cols:
            values = df[value_col].map(convertir_nombre)
            if values.notna().sum() == 0:
                continue
            if cols_annee:
                ycol = cols_annee[0]
                years = df[ycol].map(lambda v: obtenir_annee(v) if est_annee(v) else None)
                tmp = pd.DataFrame({"annee": years, "valeur": values}).dropna(subset=["annee", "valeur"])
                if tmp.empty:
                    continue
                grouped = tmp.groupby("annee", as_index=False)["valeur"].max()
                for _, r in grouped.iterrows():
                    resultats.append({
                        "annee": int(r["annee"]),
                        "valeur": float(r["valeur"]),
                        "date": None,
                        "source": nom_source,
                        "type_detection": f"table avec entete: {value_col} par {ycol}",
                    })
                if resultats:
                    return resultats
            if cols_date:
                dcol = cols_date[0]
                dates = convertir_colonne_dates(df[dcol])
                years = dates.map(lambda d: annee_hydrologique_depuis_date(d, utiliser_hydro) if pd.notna(d) else None)
                tmp = pd.DataFrame({"annee": years, "valeur": values, "date": dates}).dropna(subset=["annee", "valeur"])
                if tmp.empty:
                    continue
                idx = tmp.groupby("annee")["valeur"].idxmax()
                grouped = tmp.loc[idx]
                for _, r in grouped.iterrows():
                    resultats.append({
                        "annee": int(r["annee"]),
                        "valeur": float(r["valeur"]),
                        "date": r.get("date"),
                        "source": nom_source,
                        "type_detection": f"table avec entete: {value_col} par date {dcol}",
                    })
                if resultats:
                    return resultats
    return resultats
def extraire_pjmax_depuis_fichier(fichier: Path, utiliser_hydro: bool = True) -> Tuple[pd.DataFrame, List[str]]:
    feuilles = lire_fichier_en_feuilles(fichier)
    resultats: List[Dict] = []
    meilleure_priorite = 999
    methode = ""
    diagnostics: List[str] = []

    for nom_feuille, df in feuilles.items():
        df = df.dropna(how="all").dropna(axis=1, how="all")
        if df.empty:
            continue
        nom_source = fichier.name + " | " + str(nom_feuille)

        # Stop at the first detector that succeeds for this sheet. The former
        # code executed all six detectors and kept their intermediate tables,
        # even though the priority order already determines the answer.
        detecteurs = [
            (0, "donnees journalieres jours x mois avec entete globale .xls",
             extraire_depuis_table_journaliere_matrice_entete_global),
            (1, "donnees journalieres jours x mois",
             extraire_depuis_table_journaliere_matrice),
            (2, "donnees journalieres date-valeur", extraire_depuis_table_longue),
            (3, "ligne PJmax mensuelle", extraire_depuis_ligne_pjmax),
            (4, "tableau annee + PJmax", extraire_depuis_table_max_direct),
            (5, "table avec entetes", extraire_depuis_table_entete),
        ]
        for priorite, nom_methode, detecteur in detecteurs:
            if priorite > meilleure_priorite:
                continue
            try:
                if detecteur is extraire_depuis_table_max_direct:
                    resultat_feuille = detecteur(df, nom_source)
                else:
                    resultat_feuille = detecteur(df, nom_source, utiliser_hydro)
            except Exception:
                resultat_feuille = []
            if not resultat_feuille:
                continue
            if priorite < meilleure_priorite:
                resultats = []
                meilleure_priorite = priorite
                methode = nom_methode
            if priorite == meilleure_priorite:
                resultats.extend(resultat_feuille)
                diagnostics.append(f"{nom_source}: {nom_methode} detectes.")
            break

    if not resultats:
        raise Exception("Aucune donnee PJmax exploitable trouvee. Les totaux/moyennes/minima ne sont pas acceptes pour Gumbel.")

    table = pd.DataFrame(resultats)
    table = table.dropna(subset=["annee", "valeur"])
    table["annee"] = table["annee"].astype(int)
    table["valeur"] = table["valeur"].astype(float)
    table = table[~((table["valeur"] >= ANNEE_MIN) & (table["valeur"] <= ANNEE_MAX) & (table["valeur"] % 1 == 0))]
    if table.empty:
        raise Exception("Les valeurs detectees ressemblent a des annees. Verifiez la colonne PJmax.")

    idx = table.groupby("annee")["valeur"].idxmax()
    table = table.loc[idx].copy()
    table = table.sort_values(["valeur", "annee"], ascending=[True, True]).reset_index(drop=True)
    table["rang_croissant"] = range(1, len(table) + 1)
    diagnostics.append(f"Methode retenue: {methode}.")
    diagnostics.append(f"Resultat final: {len(table)} annees PJmax, triees du plus petit au plus grand.")
    return table, diagnostics
def sortie_unique_gumbel(dossier: Path, stem: str, reserved=None) -> Path:
    reserved = reserved if reserved is not None else set()
    stem = re.sub(r"[^\w-]", "_", stem).strip("_.") or "station"
    existing = {p.name.casefold() for p in dossier.iterdir()} if dossier.is_dir() else set()
    for n in range(1, 100000):
        candidate = dossier / (stem + (f"_{n}" if n > 1 else "") + ".xlsx")
        if not candidate.exists() and candidate.name.casefold() not in reserved and candidate.name.casefold() not in existing:
            reserved.add(candidate.name.casefold())
            return candidate
    raise FileExistsError("Aucun nom de sortie disponible")
