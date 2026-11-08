# -*- coding: utf-8 -*-
"""Traitement automatique du bilan hydrique Thornthwaite
-----------------------------------------------------
Objectif : calculer le bilan hydrique Thornthwaite directement en Python,
sans dependance a un modele Excel externe."""

import re
import warnings
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Sequence

warnings.filterwarnings("ignore")

from modules.shared import EXTENSIONS_TABLEAUX, MOIS_HYDRO, installer_paquet


# Parametres generaux






MOIS_FR = {
    "sept": "Septembre",
    "oct": "Octobre",
    "nov": "Novembre",
    "dec": "Décembre",
    "janv": "Janvier",
    "fev": "Février",
    "mars": "Mars",
    "avril": "Avril",
    "mai": "Mai",
    "juin": "Juin",
    "juil": "Juillet",
    "aout": "Août",
}

NOMBRE_DECIMALES_TP = 2
NOMBRE_DECIMALES_PR = 2
RFU_MAX = 100

# Coefficients CL du modele Excel de reference, ordre hydrologique Septembre -> Aout.
COEFFICIENTS_CL = [1.03, 0.97, 0.86, 0.81, 0.87, 0.85, 1.03, 1.10, 1.21, 1.22, 1.24, 1.16]

VALEURS_MANQUANTES = [
    -9999, -9999.0, -9999.9,
    -999, -999.0, -999.9,
    9999, 9999.0,
]


# Installation automatique des dependances


def verifier_import(nom_module: str, paquet_pip: Optional[str] = None) -> bool:
    paquet_pip = paquet_pip or nom_module
    try:
        __import__(nom_module)
        return True
    except Exception:
        print(f"Le module {nom_module} est manquant (paquet {paquet_pip}).")
        installer_paquet(paquet_pip)
        return False


def verifier_dependances() -> None:
    if not verifier_import("pandas") or not verifier_import("openpyxl"):
        raise RuntimeError(
            "pandas/openpyxl sont necessaires. Lancez install_requirements.bat "
            "avant de relancer HydroStation Analyzer."
        )
    # Dependances optionnelles selon les formats trouves.
    # xlrd pour .xls, odfpy pour .ods, pyxlsb pour .xlsb, pywin32 pour conversion .xls du m...


# Importer apres verification basique.
verifier_dependances()
from datetime import datetime
import pandas as pd  # noqa: E402


# Nettoyage et reconnaissance



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
    cancel_requested as _shared_cancel_requested,
)



# Shared helpers: same implementation and interface as the Gumbel support.
# The options below preserve the original Bilan-specific parsing rules.

def nettoyer_chemin(chemin: str) -> str:
    return _shared_clean_path(chemin)


def normaliser_texte(texte) -> str:
    return _shared_normalize_text(texte, none_as_empty=False, remove_braces=True)


def normaliser_compact(texte) -> str:
    return _shared_compact_text(texte, normaliser_texte)


def convertir_nombre(valeur) -> Optional[float]:
    return _shared_parse_number(
        valeur, missing_values=VALEURS_MANQUANTES, allow_negative=True,
        strip_outer_pipes=True, comma_mode="decimal", reject_bool=False,
    )


def est_annee(valeur) -> bool:
    return _shared_integer_in_range(valeur, convertir_nombre, 1800, 2100)


def est_jour(valeur) -> bool:
    return _shared_integer_in_range(valeur, convertir_nombre, 1, 31)


def creer_dossier_unique(dossier_parent: Path, nom_base: str) -> Path:
    return _shared_create_unique_directory(dossier_parent, nom_base, create_parent=True)


def _est_fichier_texte_sans_extension(fichier: Path) -> bool:
    """Recognise legacy text climate files that have no filename extension."""
    fichier = Path(fichier)
    if fichier.suffix:
        return False
    try:
        if fichier.stat().st_size <= 0:
            return False
        with fichier.open("rb") as stream:
            raw = stream.read(8192)
        if not raw or b"\x00" in raw:
            return False
        texte = raw.decode("utf-8-sig", errors="replace")
        if "\ufffd" in texte[:1000] and texte.count("\ufffd") > 5:
            texte = raw.decode("cp1252", errors="replace")
        normalise = normaliser_texte(texte[:8192])
        marqueurs = (
            "station", "poste", "annee", "anne", "janv", "fevr", "mars",
            "temperature", "precipitation", "pluie", "moyenne", "|",
        )
        return len(texte.strip()) >= 12 and any(m in normalise for m in marqueurs)
    except (OSError, UnicodeError):
        return False


def obtenir_fichiers_depuis_chemin(chemin_source: str) -> List[Path]:
    """List climate tables without filtering legitimate names such as Temperature401."""
    chemin = Path(nettoyer_chemin(chemin_source))
    extensions = set(EXTENSIONS_TABLEAUX)

    if chemin.is_file():
        if chemin.suffix.lower() in extensions:
            if chemin.stat().st_size <= 0:
                raise Exception("Fichier vide ignore : " + str(chemin))
            return [chemin]
        if _est_fichier_texte_sans_extension(chemin):
            return [chemin]
        raise Exception("Format non supporte : " + str(chemin))

    if chemin.is_dir():
        # IMPORTANT: MOTS_STATION_A_SUPPRIMER sert uniquement a nettoyer les
        # identifiants de station. Ne jamais l'utiliser pour filtrer les noms
        # de fichiers: cela eliminait Temperature401, precipitation_150101, etc.
        fichiers = _shared_list_directory_files(
            chemin, extensions,
            excluded_tokens=(),
            normalizer=None,
            skip_lock_files=True,
            skip_empty=True,
        )
        # Plusieurs anciens fichiers ONM sont des tableaux texte sans extension.
        fichiers_sans_extension = []
        for candidat in chemin.iterdir():
            if (candidat.is_file() and not candidat.name.startswith("~$")
                    and not candidat.suffix and _est_fichier_texte_sans_extension(candidat)):
                fichiers_sans_extension.append(candidat)

        fichiers = sorted(
            set(fichiers).union(fichiers_sans_extension),
            key=lambda p: p.name.casefold()
        )
        if not fichiers:
            raise Exception("Aucun fichier tableau trouve dans : " + str(chemin))
        return fichiers

    raise Exception("Chemin introuvable : " + str(chemin))



def _lire_excel_toutes_feuilles_uncached(fichier: Path) -> Dict[str, pd.DataFrame]:
    ext = fichier.suffix.lower()
    if ext == ".xls":
        if not verifier_import("xlrd"):
            raise Exception("Impossible d'utiliser xlrd pour lire le fichier .xls")
        tables = pd.read_excel(fichier, sheet_name=None, header=None, engine="xlrd")
    elif ext in [".xlsx", ".xlsm"]:
        tables = pd.read_excel(fichier, sheet_name=None, header=None, engine="openpyxl")
    elif ext == ".xlsb":
        if not verifier_import("pyxlsb"):
            raise Exception("Impossible d'utiliser pyxlsb pour lire le fichier .xlsb")
        tables = pd.read_excel(fichier, sheet_name=None, header=None, engine="pyxlsb")
    elif ext == ".ods":
        if not verifier_import("odf", "odfpy"):
            raise Exception("Impossible d'utiliser odfpy pour lire le fichier .ods")
        tables = pd.read_excel(fichier, sheet_name=None, header=None, engine="odf")
    else:
        raise Exception("Ce fichier n'est pas un fichier Excel : " + str(fichier))
    return _shared_filter_excel_sheets(tables, normaliser_compact, keep_original=True)

def _lire_fichier_texte_uncached(fichier: Path) -> pd.DataFrame:
    """Read delimited text and legacy pipe/space climate tables."""
    fichier = Path(fichier)

    for encoding in ["utf-8-sig", "utf-8", "latin1", "cp1252"]:
        try:
            texte = fichier.read_text(encoding=encoding)
        except Exception:
            continue

        # Legacy ONM text exports use pipes as visual column boundaries and
        # spaces between the monthly values inside each pipe-delimited block:
        # | 1996 | 15.4 14.6 ... 15.8 | 20.1 |
        # Expand those blocks into real columns instead of reading the whole
        # row as one cell.
        lignes = texte.splitlines()
        lignes_pipe = [ligne for ligne in lignes if "|" in ligne]
        if len(lignes_pipe) >= 3:
            rows = []
            for ligne in lignes:
                brut = ligne.strip()
                if not brut or re.fullmatch(r"[-=_\s]+", brut):
                    continue
                if "|" in brut:
                    morceaux = [part.strip() for part in brut.split("|") if part.strip()]
                    cellules = []
                    for morceau in morceaux:
                        cellules.extend(morceau.split())
                    if cellules:
                        rows.append(cellules)
                else:
                    rows.append([brut])
            if rows:
                width = max(len(row) for row in rows)
                return pd.DataFrame([row + [None] * (width - len(row)) for row in rows])

        # Keep the historical CSV/TSV parsing path for ordinary text files.
        try:
            return pd.read_csv(
                fichier, sep=None, engine="python", header=None, encoding=encoding
            )
        except Exception:
            pass

        try:
            with fichier.open("r", encoding=encoding, newline="") as stream:
                sample = stream.read(8192)
                stream.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
                    delim = dialect.delimiter
                except csv.Error:
                    delim = "\t" if fichier.suffix.lower() == ".tsv" else ";"
                rows = list(csv.reader(stream, delimiter=delim))
            if rows:
                width = max(map(len, rows))
                return pd.DataFrame([row + [None] * (width - len(row)) for row in rows])
        except Exception:
            continue

    raise Exception("Impossible de lire le fichier texte : " + str(fichier))


def lire_excel_toutes_feuilles(fichier: Path) -> Dict[str, pd.DataFrame]:
    return _shared_cached_file_read(
        fichier, "bilan_excel_sheets_v1",
        lambda: _lire_excel_toutes_feuilles_uncached(Path(fichier)),
    )


def lire_fichier_texte(fichier: Path) -> pd.DataFrame:
    return _shared_cached_file_read(
        fichier, "bilan_text_table_v1",
        lambda: _lire_fichier_texte_uncached(Path(fichier)),
    )


def lire_fichier_en_feuilles(fichier: Path) -> Dict[str, pd.DataFrame]:
    """Dispatch supported spreadsheets, CSV/text, and extensionless legacy tables."""
    fichier = Path(fichier)
    extension = fichier.suffix.lower()
    if extension in EXTENSIONS_TABLEAUX:
        return _shared_dispatch_file_sheets(
            fichier, lire_excel_toutes_feuilles, lire_fichier_texte
        )
    if not extension and _est_fichier_texte_sans_extension(fichier):
        return {"table": lire_fichier_texte(fichier)}
    raise Exception("Format non supporte : " + extension)


def convertir_nombre_climatique(valeur, type_donnee: str) -> Optional[float]:
    nombre = convertir_nombre(valeur)
    if nombre is None:
        return None

    # Les precipitations negatives sont invalides.
    if type_donnee == "precipitation" and nombre < 0:
        return None

    # Eviter de prendre une annee comme valeur climatique.
    if float(nombre).is_integer() and 1800 <= int(nombre) <= 2100:
        return None

    # Filtrage doux contre les valeurs aberrantes.
    if type_donnee == "temperature":
        if nombre < -80 or nombre > 70:
            return None
    else:
        if nombre > 5000:
            return None

    return nombre
def reconnaitre_mois(valeur) -> Optional[str]:
    """Reconnait uniquement les noms de mois textuels."""
    texte = normaliser_compact(valeur)
    if texte == "":
        return None

    if texte.startswith(("sep", "septembre", "september")):
        return "sept"
    if texte.startswith(("oct", "octobre", "october")):
        return "oct"
    if texte.startswith(("nov", "novembre", "november")):
        return "nov"
    if texte.startswith(("dec", "decembre", "december")):
        return "dec"
    if texte.startswith(("jan", "janv", "janvier", "january")):
        return "janv"
    if texte.startswith(("fev", "fevr", "fevrier", "feb", "february")):
        return "fev"
    if texte.startswith(("mar", "mars", "march")):
        return "mars"
    if texte.startswith(("avr", "avril", "apr", "april")):
        return "avril"
    if texte.startswith(("mai", "may")):
        return "mai"
    if texte.startswith(("juin", "jun", "june")):
        return "juin"
    if texte.startswith(("juil", "jul", "july")):
        return "juil"
    if texte.startswith(("aout", "aou", "aug", "august")):
        return "aout"

    return None
def reconnaitre_mois_numero(valeur) -> Optional[str]:
    """Reconnait un mois numerique seulement pour les vraies lignes d'entete
    numeriques, par exemple 1..12 ou 9..8."""
    nombre = convertir_nombre(valeur)
    if nombre is not None and float(nombre).is_integer():
        mois = int(nombre)
        mapping = {
            9: "sept", 10: "oct", 11: "nov", 12: "dec",
            1: "janv", 2: "fev", 3: "mars", 4: "avril",
            5: "mai", 6: "juin", 7: "juil", 8: "aout"
        }
        return mapping.get(mois)
    return None
def mois_numero_depuis_nom(nom_mois: str) -> int:
    for nom, numero in MOIS_HYDRO:
        if nom == nom_mois:
            return numero
    raise ValueError(nom_mois)
def mois_nom_depuis_num(numero: int) -> str:
    for nom, num in MOIS_HYDRO:
        if num == numero:
            return nom
    raise ValueError(numero)
def ordre_mois_hydro_noms() -> List[str]:
    return [m for m, _ in MOIS_HYDRO]
def dict_vide_mois() -> Dict[str, List[float]]:
    return {nom: [] for nom, _ in MOIS_HYDRO}
def moyenne_liste(valeurs: Sequence[float]) -> Optional[float]:
    valeurs = [float(v) for v in valeurs if v is not None and not pd.isna(v)]
    if not valeurs:
        return None
    return sum(valeurs) / len(valeurs)
def convertir_colonne_dates(serie: pd.Series) -> pd.Series:
    # Essayer dayfirst=True puis dayfirst=False et garder le meilleur.
    candidats = []
    for dayfirst in [True, False]:
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Could not infer format.*", category=UserWarning)
                dates = pd.to_datetime(serie, errors="coerce", dayfirst=dayfirst)
            candidats.append(dates)
        except Exception:
            pass

    if not candidats:
        return pd.Series([pd.NaT] * len(serie))

    def score(s: pd.Series) -> int:
        ok = s.notna() & (s.dt.year >= 1800) & (s.dt.year <= 2100)
        return int(ok.sum())

    meilleur = max(candidats, key=score)
    return meilleur
def colonne_ressemble_aux_jours(serie: pd.Series) -> bool:
    """Evite de confondre une colonne Jour 1..31 avec une colonne Date."""
    vals = [convertir_nombre(v) for v in serie]
    vals = [v for v in vals if v is not None]
    if len(vals) < 5:
        return False
    nb_jours = sum(1 for v in vals if float(v).is_integer() and 1 <= int(v) <= 31)
    return nb_jours / len(vals) >= 0.60
def detecter_colonne_date(df: pd.DataFrame) -> Optional[int]:
    meilleure_colonne = None
    meilleur_score = 0

    for col in range(df.shape[1]):
        # Ne jamais prendre une colonne de jours 1..31 comme colonne de dates.
        if colonne_ressemble_aux_jours(df.iloc[:, col]):
            continue

        entete = " ".join(str(df.iloc[r, col]) for r in range(min(12, df.shape[0])) if pd.notna(df.iloc[r, col]))
        entete_norm = normaliser_texte(entete)

        # Les vraies dates sont souvent textuelles avec / ou -, ou des dates Excel seriels > 20...
        valeurs = [v for v in df.iloc[:, col] if pd.notna(v)]
        nb_date_like = 0
        nb_serial_excel = 0
        for v in valeurs:
            if isinstance(v, (pd.Timestamp, datetime)):
                nb_date_like += 1
                continue
            txt = str(v).strip()
            if "/" in txt or "-" in txt:
                nb_date_like += 1
            n = convertir_nombre(v)
            if n is not None and 20000 <= n <= 80000:
                nb_serial_excel += 1

        dates = convertir_colonne_dates(df.iloc[:, col])
        score = int(((dates.notna()) & (dates.dt.year >= 1800) & (dates.dt.year <= 2100)).sum())

        # Si pas de libelle Date et pas d'aspect date reel, on ignore.
        if "date" not in entete_norm and "time" not in entete_norm:
            if nb_date_like + nb_serial_excel < max(5, int(score * 0.50)):
                continue

        if "date" in entete_norm or "time" in entete_norm:
            score += 20

        if score > meilleur_score:
            meilleur_score = score
            meilleure_colonne = col

    return meilleure_colonne if meilleur_score >= 8 else None
def colonne_ressemble_aux_annees(serie: pd.Series) -> bool:
    vals = [convertir_nombre(v) for v in serie]
    vals = [v for v in vals if v is not None]
    if not vals:
        return False
    nb_annee = sum(1 for v in vals if float(v).is_integer() and 1800 <= int(v) <= 2100)
    return nb_annee / len(vals) >= 0.60
def texte_autour_colonne(df: pd.DataFrame, col: int, nb_lignes: int = 20) -> str:
    return " ".join(str(df.iloc[r, col]) for r in range(min(nb_lignes, df.shape[0])) if pd.notna(df.iloc[r, col]))
def score_colonne_valeur_journaliere(df: pd.DataFrame, col: int, type_donnee: str) -> int:
    texte = normaliser_texte(texte_autour_colonne(df, col, 25))
    compact = normaliser_compact(texte)
    if colonne_ressemble_aux_annees(df.iloc[:, col]):
        return -999

    interdit_commun = ["jour du max", "nbre", "nombre", "date"]
    for mot in interdit_commun:
        if mot in texte:
            return -999

    if type_donnee == "temperature":
        # Si MIN/MAX/MOY existent, MOY est prioritaire, MIN/MAX interdits.
        if any(m in texte for m in ["min", "minimum", "max", "maximum"]) or any(m in compact for m in ["tmin", "tmax"]):
            return -999
        if any(m in texte for m in ["pluie", "precip", "rain", "pr ", "totaux", "total"]):
            return -999
    else:
        if any(m in texte for m in ["temp", "temperature", "tp", "t moy", "tmin", "tmax"]):
            return -999
        if any(m in texte for m in ["pj max", "pjmax", "p max", "pmax", "max journalier"]):
            return -999

    valeurs_valides = 0
    for v in df.iloc[:, col]:
        if convertir_nombre_climatique(v, type_donnee) is not None:
            valeurs_valides += 1

    score = valeurs_valides
    if type_donnee == "temperature":
        if "moy" in texte or "mean" in texte or "average" in texte:
            score += 60
        if any(m in texte for m in ["temp", "temperature", "tp", "t moy", "tavg"]):
            score += 30
    else:
        if any(m in texte for m in ["pluie", "precip", "rain", "pr", "p mm"]):
            score += 40
        if any(m in texte for m in ["totaux", "total", "somme"]):
            score += 20
    return score
def detecter_colonnes_valeurs_date(df: pd.DataFrame, colonne_date: int, type_donnee: str) -> List[int]:
    scores = []
    for col in range(df.shape[1]):
        if col == colonne_date:
            continue
        score = score_colonne_valeur_journaliere(df, col, type_donnee)
        if score >= 8:
            scores.append((score, col))

    scores = sorted(scores, reverse=True)
    if not scores:
        return []

    # Pour temperature, prendre toutes les colonnes MOY si elles existent.
    if type_donnee == "temperature":
        moy_cols = []
        for _, col in scores:
            txt = normaliser_texte(texte_autour_colonne(df, col, 25))
            if "moy" in txt or "mean" in txt or "average" in txt:
                moy_cols.append(col)
        if moy_cols:
            return moy_cols

    # Sinon prendre la meilleure colonne seulement pour eviter de melanger des colonnes non...
    return [scores[0][1]]
def extraire_depuis_date_valeur(df: pd.DataFrame, type_donnee: str) -> List[Tuple[str, float]]:
    col_date = detecter_colonne_date(df)
    if col_date is None:
        return []

    cols_valeurs = detecter_colonnes_valeurs_date(df, col_date, type_donnee)
    if not cols_valeurs:
        return []

    dates = convertir_colonne_dates(df.iloc[:, col_date])
    lignes = []
    for i in range(len(df)):
        d = dates.iloc[i]
        if pd.isna(d) or d.year < 1800 or d.year > 2100:
            continue
        vals = []
        for col in cols_valeurs:
            val = convertir_nombre_climatique(df.iloc[i, col], type_donnee)
            if val is not None:
                vals.append(val)
        if not vals:
            continue
        valeur = moyenne_liste(vals)
        if valeur is None:
            continue
        lignes.append({"annee": int(d.year), "mois": int(d.month), "valeur": valeur})

    if not lignes:
        return []

    t = pd.DataFrame(lignes)
    resultats = []
    if type_donnee == "temperature":
        # moyenne mensuelle de toutes les valeurs journalieres
        g = t.groupby("mois")["valeur"].mean()
        for mois, valeur in g.items():
            if int(mois) in range(1, 13):
                resultats.append((mois_nom_depuis_num(int(mois)), float(valeur)))
    else:
        # somme mensuelle par annee, puis moyenne mensuelle
        mensuel = t.groupby(["annee", "mois"])["valeur"].sum().reset_index()
        g = mensuel.groupby("mois")["valeur"].mean()
        for mois, valeur in g.items():
            if int(mois) in range(1, 13):
                resultats.append((mois_nom_depuis_num(int(mois)), float(valeur)))
    return resultats
def identifier_colonnes_mois(ligne: Sequence) -> Dict[str, int]:
    """Identifie les colonnes mensuelles."""
    colonnes_textuelles = {}
    colonnes_numeriques = {}

    for col, val in enumerate(ligne):
        mois = reconnaitre_mois(val)
        if mois is not None:
            colonnes_textuelles[mois] = col
            continue

        mois_num = reconnaitre_mois_numero(val)
        if mois_num is not None:
            colonnes_numeriques[mois_num] = col

    if len(colonnes_textuelles) >= 3:
        return colonnes_textuelles

    # Accepter une entete numerique seulement si elle couvre une vraie serie de mois.
    # Cela evite de confondre des valeurs de pluie ordinaires avec des entetes.
    if len(colonnes_numeriques) >= 6:
        positions = sorted(colonnes_numeriques.values())
        # Les mois d'une entete doivent etre presque contigus.
        if positions[-1] - positions[0] <= len(positions) + 2:
            return colonnes_numeriques

    return colonnes_textuelles
def detecter_colonne_jours(df: pd.DataFrame, ligne_mois: int) -> Optional[int]:
    """Detecte une vraie colonne de jours 1..31."""
    meilleure_col = None
    meilleur_score = 0

    for col in range(min(8, df.shape[1])):
        jours = []
        for r in range(ligne_mois + 1, min(df.shape[0], ligne_mois + 45)):
            nombre = convertir_nombre(df.iloc[r, col])
            if nombre is None:
                continue
            if float(nombre).is_integer() and 1 <= int(nombre) <= 31:
                jours.append(int(nombre))

        if not jours:
            continue

        uniques = sorted(set(jours))
        score = len(uniques)

        # Conditions d'une vraie colonne jours : beaucoup de jours, commence pres de 1,
        # et couvre au moins jusqu'a 20. Cela evite les fausses detections dans les
        # tableaux mensuels annee-mois.
        if score >= 10 and min(uniques) <= 3 and max(uniques) >= 20:
            # Bonus si la sequence est vraiment 1..31 presque continue.
            attendus = set(range(1, min(max(uniques), 31) + 1))
            continuite = len(set(uniques).intersection(attendus))
            score += continuite
            if score > meilleur_score:
                meilleur_score = score
                meilleure_col = col

    return meilleure_col if meilleur_score >= 20 else None
def colonne_ressemble_aux_annees_dans_bloc(df: pd.DataFrame, col: int, start_row: int, end_row: int) -> bool:
    """Detect a year column inside the current monthly block only."""
    total = 0
    nb_annees = 0
    fin = min(end_row, df.shape[0], start_row + 100)
    for r in range(start_row, fin):
        ligne = df.iloc[r, :].tolist()
        # A new month header marks the end of the current table.
        if r > start_row and len(identifier_colonnes_mois(ligne)) >= 6:
            break
        val = df.iloc[r, col]
        if pd.isna(val) or str(val).strip() == "":
            continue
        texte = normaliser_compact(val)
        # Ignore visual separators and summary rows, not observations.
        if not texte or re.fullmatch(r"[-_=|]+", str(val).strip()):
            continue
        if texte in {"moyen", "moyenne", "moyennes", "annuel", "total", "totaux"}:
            continue
        if est_annee(val):
            total += 1
            nb_annees += 1
            continue
        # Numeric non-year values (e.g. days 1..31) must count against the
        # ratio; arbitrary labels in the first column are ignored.
        if convertir_nombre(val) is not None:
            total += 1
    return total >= 2 and nb_annees / total >= 0.70
def extraire_depuis_table_mensuelle_annees(df: pd.DataFrame, type_donnee: str) -> List[Tuple[str, float]]:
    """Extrait les moyennes mensuelles depuis un tableau historique classique :
    annee | sept | oct | nov | ..."""
    valeurs_par_mois = dict_vide_mois()

    for i in range(df.shape[0]):
        colonnes_mois = identifier_colonnes_mois(df.iloc[i, :].tolist())
        if len(colonnes_mois) < 6:
            continue
        if type_donnee == "temperature" and _bloc_temperature_est_extreme(df, i):
            continue

        # Chercher une colonne d'annees autour de l'entete, generalement a gauche.
        colonnes_annees = []
        for col in range(min(df.shape[1], 5)):
            if colonne_ressemble_aux_annees_dans_bloc(df, col, i + 1, min(df.shape[0], i + 120)):
                colonnes_annees.append(col)
        if not colonnes_annees:
            continue
        col_annee = colonnes_annees[0]

        lignes_vides = 0
        for r in range(i + 1, min(df.shape[0], i + 500)):
            ligne = df.iloc[r, :].tolist()

            # Si une nouvelle entete textuelle de mois apparait apres le bloc, arreter.
            if r > i + 1 and len(identifier_colonnes_mois(ligne)) >= 6 and not est_annee(df.iloc[r, col_annee]):
                break

            if all(pd.isna(v) or str(v).strip() == "" for v in ligne):
                lignes_vides += 1
                if lignes_vides >= 3:
                    break
                continue
            lignes_vides = 0

            if not est_annee(df.iloc[r, col_annee]):
                continue

            for nom_mois, col_mois in colonnes_mois.items():
                val = convertir_nombre_climatique(df.iloc[r, col_mois], type_donnee)
                if val is not None:
                    valeurs_par_mois[nom_mois].append(val)

        resultats = []
        for mois in ordre_mois_hydro_noms():
            moy = moyenne_liste(valeurs_par_mois[mois])
            if moy is not None:
                resultats.append((mois, moy))

        if len(resultats) >= 6:
            return resultats

    return []
def extraire_depuis_jours_mois(df: pd.DataFrame, type_donnee: str) -> List[Tuple[str, float]]:
    valeurs_par_mois = dict_vide_mois()

    for i in range(df.shape[0]):
        colonnes_mois = identifier_colonnes_mois(df.iloc[i, :].tolist())
        if len(colonnes_mois) < 3:
            continue
        if type_donnee == "temperature" and _bloc_temperature_est_extreme(df, i):
            continue

        col_jour = detecter_colonne_jours(df, i)
        if col_jour is None:
            continue

        # Ne pas confondre une colonne d'annees avec une colonne de jours.
        if colonne_ressemble_aux_annees_dans_bloc(df, col_jour, i + 1, min(df.shape[0], i + 120)):
            continue

        lignes_jours = []
        for r in range(i + 1, min(df.shape[0], i + 45)):
            if est_jour(df.iloc[r, col_jour]):
                lignes_jours.append(r)
            elif lignes_jours:
                break

        if not lignes_jours:
            continue

        for nom_mois, col_mois in colonnes_mois.items():
            vals = []
            for r in lignes_jours:
                val = convertir_nombre_climatique(df.iloc[r, col_mois], type_donnee)
                if val is not None:
                    vals.append(val)
            if not vals:
                continue
            if type_donnee == "temperature":
                valeurs_par_mois[nom_mois].append(sum(vals) / len(vals))
            else:
                valeurs_par_mois[nom_mois].append(sum(vals))

    resultats = []
    for mois in ordre_mois_hydro_noms():
        moy = moyenne_liste(valeurs_par_mois[mois])
        if moy is not None:
            resultats.append((mois, moy))
    return resultats
def ligne_est_moy_temperature(ligne: Sequence) -> bool:
    texte = normaliser_texte(" ".join(str(v) for v in ligne if pd.notna(v)))
    compact = normaliser_compact(texte)
    # "Moyenne mensuelle des temperatures maximales" is the mean of Tmax,
    # not the mean air temperature required by Thornthwaite.
    if ligne_est_min_ou_max_temperature(ligne):
        return False
    return (
        "moy" in texte or "moyenne" in texte or "mean" in texte
        or "average" in texte or "tmoy" in compact or "tmean" in compact
    )

def ligne_est_min_ou_max_temperature(ligne: Sequence) -> bool:
    texte = normaliser_texte(" ".join(str(v) for v in ligne if pd.notna(v)))
    compact = normaliser_compact(texte)
    return any(x in texte for x in ["min", "minimum", "max", "maximum"]) or any(x in compact for x in ["tmin", "tmax"])
def ligne_est_total_precip(ligne: Sequence) -> bool:
    texte = normaliser_texte(" ".join(str(v) for v in ligne if pd.notna(v)))
    compact = normaliser_compact(texte)
    # "Moyenne des precipitations mensuelles" is not a total row.
    return any(x in texte for x in ["totaux", "total", "somme", "cumul"]) or any(
        x in compact for x in ["ptot", "prtot", "pptot"]
    )


def ligne_est_moy_precipitation(ligne: Sequence) -> bool:
    texte = normaliser_texte(" ".join(str(v) for v in ligne if pd.notna(v)))
    return any(token in texte for token in ("moy", "moyenne", "mean", "average"))

def ligne_est_interdite_precip(ligne: Sequence) -> bool:
    texte = normaliser_texte(" ".join(str(v) for v in ligne if pd.notna(v)))
    compact = normaliser_compact(texte)
    # Keep monthly mean precipitation ("Moy"/"Moyenne"); reject extrema and PJmax.
    interdits = [
        "pj max", "pjmax", "p max", "pmax", "jour du max", "nbre", "nombre",
        "maximum", "maximale", "maximales", "minimum", "minimale", "minimales",
    ]
    return (
        re.search(r"\b(max|min)\b", texte) is not None
        or any(x in texte for x in interdits)
        or any(x in compact for x in ["pjmax", "pmax", "pmaximale"])
    )




def _bloc_temperature_est_extreme(df: pd.DataFrame, ligne_entete: int, fenetre: int = 20) -> bool:
    """Detect whether a monthly block belongs to Tmin/Tmax rather than mean temperature."""
    debut = max(0, int(ligne_entete) - int(fenetre))
    for r in range(int(ligne_entete) - 1, debut - 1, -1):
        ligne = df.iloc[r, :].tolist()
        texte = normaliser_texte(" ".join(str(v) for v in ligne if pd.notna(v)))
        if not texte:
            continue
        # Do not cross into a previous monthly table block.
        if len(identifier_colonnes_mois(ligne)) >= 6:
            break
        if ("temperature" in texte or "temperatures" in texte or "temp" in texte
                or "tmin" in normaliser_compact(texte) or "tmax" in normaliser_compact(texte)):
            if ligne_est_min_ou_max_temperature(ligne):
                return True
            if ligne_est_moy_temperature(ligne):
                return False
    return False

def extraire_depuis_mensuel_horizontal(df: pd.DataFrame, type_donnee: str) -> List[Tuple[str, float]]:
    valeurs_par_mois = dict_vide_mois()
    valeurs_moy_specifiques = dict_vide_mois()
    valeurs_totaux_specifiques = dict_vide_mois()

    for i in range(df.shape[0]):
        colonnes_mois = identifier_colonnes_mois(df.iloc[i, :].tolist())
        if len(colonnes_mois) < 3:
            continue
        if type_donnee == "temperature" and _bloc_temperature_est_extreme(df, i):
            continue

        # Parcourir les lignes apres l'entete des mois jusqu'a un nouveau bloc ou grande zone v...
        lignes_vides = 0
        for r in range(i + 1, min(df.shape[0], i + 400)):
            ligne = df.iloc[r, :].tolist()
            if len(identifier_colonnes_mois(ligne)) >= 3:
                break
            if all(pd.isna(v) or str(v).strip() == "" for v in ligne):
                lignes_vides += 1
                if lignes_vides >= 3:
                    break
                continue
            lignes_vides = 0

            if type_donnee == "temperature":
                est_moy = ligne_est_moy_temperature(ligne)
                est_minmax = ligne_est_min_ou_max_temperature(ligne)
                if est_minmax and not est_moy:
                    continue
                cible = valeurs_moy_specifiques if est_moy else valeurs_par_mois
            else:
                est_total = ligne_est_total_precip(ligne)
                est_moy = ligne_est_moy_precipitation(ligne)
                if ligne_est_interdite_precip(ligne) and not est_total and not est_moy:
                    continue
                if est_total:
                    cible = valeurs_totaux_specifiques
                elif est_moy:
                    cible = valeurs_moy_specifiques
                else:
                    cible = valeurs_par_mois

            nb_valeurs = 0
            for nom_mois, col in colonnes_mois.items():
                val = convertir_nombre_climatique(df.iloc[r, col], type_donnee)
                if val is not None:
                    cible[nom_mois].append(val)
                    nb_valeurs += 1

            # Si la ligne n'a presque pas de valeurs mensuelles, ignorer implicitement par absence...
            _ = nb_valeurs

    if type_donnee == "temperature" and any(valeurs_moy_specifiques[m] for m in valeurs_moy_specifiques):
        source = valeurs_moy_specifiques
    elif type_donnee == "precipitation" and any(valeurs_totaux_specifiques[m] for m in valeurs_totaux_specifiques):
        source = valeurs_totaux_specifiques
    elif type_donnee == "precipitation" and any(valeurs_moy_specifiques[m] for m in valeurs_moy_specifiques):
        source = valeurs_moy_specifiques
    else:
        source = valeurs_par_mois

    resultats = []
    for mois in ordre_mois_hydro_noms():
        moy = moyenne_liste(source[mois])
        if moy is not None:
            resultats.append((mois, moy))
    return resultats

def detecter_colonne_mois_verticale(df: pd.DataFrame) -> Optional[int]:
    meilleur_col = None
    meilleur_score = 0
    for col in range(df.shape[1]):
        score = 0
        for r in range(df.shape[0]):
            if reconnaitre_mois(df.iloc[r, col]) is not None:
                score += 1
        if score > meilleur_score:
            meilleur_score = score
            meilleur_col = col
    return meilleur_col if meilleur_score >= 3 else None
def extraire_depuis_mois_vertical(df: pd.DataFrame, type_donnee: str) -> List[Tuple[str, float]]:
    col_mois = detecter_colonne_mois_verticale(df)
    if col_mois is None:
        return []

    # Choisir les colonnes valeurs.
    candidats = []
    for col in range(df.shape[1]):
        if col == col_mois:
            continue
        score = score_colonne_valeur_journaliere(df, col, type_donnee)
        if score >= 3:
            candidats.append((score, col))

    if not candidats:
        return []

    candidats.sort(reverse=True)
    if type_donnee == "temperature":
        moy_cols = []
        for _, col in candidats:
            txt = normaliser_texte(texte_autour_colonne(df, col, 25))
            if "moy" in txt or "mean" in txt or "average" in txt:
                moy_cols.append(col)
        cols_valeurs = moy_cols if moy_cols else [candidats[0][1]]
    else:
        # Pour precipitation preferer total / pr / pluie.
        cols_valeurs = [candidats[0][1]]

    valeurs_par_mois = dict_vide_mois()
    for r in range(df.shape[0]):
        mois = reconnaitre_mois(df.iloc[r, col_mois])
        if mois is None:
            continue
        vals = []
        for col in cols_valeurs:
            val = convertir_nombre_climatique(df.iloc[r, col], type_donnee)
            if val is not None:
                vals.append(val)
        val_moy = moyenne_liste(vals)
        if val_moy is not None:
            valeurs_par_mois[mois].append(val_moy)

    resultats = []
    for mois in ordre_mois_hydro_noms():
        moy = moyenne_liste(valeurs_par_mois[mois])
        if moy is not None:
            resultats.append((mois, moy))
    return resultats
def fusionner_resultats(resultats: List[Tuple[str, float]]) -> Dict[str, float]:
    valeurs = dict_vide_mois()
    for mois, valeur in resultats:
        if mois in valeurs and valeur is not None:
            valeurs[mois].append(float(valeur))
    final = {}
    for mois in ordre_mois_hydro_noms():
        moy = moyenne_liste(valeurs[mois])
        if moy is not None:
            final[mois] = moy
    return final
def extraire_mensuel_depuis_fichier(fichier: Path, type_donnee: str) -> Dict[str, float]:
    feuilles = lire_fichier_en_feuilles(fichier)

    resultats_date = []
    resultats_mensuel_annees = []
    resultats_jours_mois = []
    resultats_mensuel_horizontal = []
    resultats_vertical = []
    mois_attendus = ordre_mois_hydro_noms()

    for nom_feuille, df in feuilles.items():
        if df is None or df.empty:
            continue
        # The common station files have one useful sheet. Try detectors in
        # priority order and stop as soon as one supplies all 12 months. The
        # old implementation ran every detector even after a valid result.
        detecteurs = [
            ("donnees journalieres date-valeur", extraire_depuis_date_valeur, resultats_date),
            ("donnees mensuelles historiques annee-mois", extraire_depuis_table_mensuelle_annees, resultats_mensuel_annees),
            ("donnees journalieres jours-mois", extraire_depuis_jours_mois, resultats_jours_mois),
            ("donnees mensuelles horizontales", extraire_depuis_mensuel_horizontal, resultats_mensuel_horizontal),
            ("donnees mensuelles verticales", extraire_depuis_mois_vertical, resultats_vertical),
        ]
        for source, detecteur, stockage in detecteurs:
            try:
                resultat = detecteur(df, type_donnee)
            except Exception:
                resultat = []
            if not resultat:
                continue
            stockage.extend(resultat)
            final_rapide = fusionner_resultats(resultat)
            if all(m in final_rapide for m in mois_attendus):
                print(f"  {type_donnee}: extraction depuis {source}")
                return final_rapide

    methodes = [
        ("donnees journalieres date-valeur", resultats_date),
        ("donnees mensuelles historiques annee-mois", resultats_mensuel_annees),
        ("donnees journalieres jours-mois", resultats_jours_mois),
        ("donnees mensuelles horizontales", resultats_mensuel_horizontal),
        ("donnees mensuelles verticales", resultats_vertical),
    ]

    # Priorite stricte : on accepte directement une methode seulement si elle couvre les 12...
    for source, res in methodes:
        if not res:
            continue
        final = fusionner_resultats(res)
        if all(m in final for m in mois_attendus):
            print(f"  {type_donnee}: extraction depuis {source}")
            return final

    # Secours intelligent : si une methode donne seulement quelques mois a cause d'un forma...
    # on complete les mois manquants avec la methode suivante dans l'ordre de priorite.
    final_combine = {}
    source_mois = {}
    for source, res in methodes:
        if not res:
            continue
        f = fusionner_resultats(res)
        for mois in mois_attendus:
            if mois not in final_combine and mois in f:
                final_combine[mois] = f[mois]
                source_mois[mois] = source

    if all(m in final_combine for m in mois_attendus):
        sources_utilisees = sorted(set(source_mois.values()))
        print(f"  {type_donnee}: extraction combinee depuis " + " + ".join(sources_utilisees))
        return final_combine

    if not final_combine:
        raise Exception(f"Aucune donnee mensuelle exploitable trouvee pour {type_donnee} dans {fichier.name}")

    manquants = [m for m in mois_attendus if m not in final_combine]
    disponibles = [m for m in mois_attendus if m in final_combine]
    raise Exception(
        f"Mois manquants pour {type_donnee} dans {fichier.name} : {', '.join(manquants)}. "
        f"Mois detectes : {', '.join(disponibles) if disponibles else 'aucun'}"
    )
MOTS_STATION_A_SUPPRIMER = [
    "temperature", "temperatures", "temp", "tp", "t",
    "precipitation", "precipitations", "pluie", "pluies", "pr", "p",
    "bilan", "hydrique", "thorn", "gumbel", "data", "donnees", "données",
    "station", "poste", "code", "nom", "de", "du", "des", "serie", "series",
]
def extraire_codes_station_depuis_texte(
    texte: str, *, autoriser_alphanumerique: bool = False,
    autoriser_codes_alphabetiques: bool = False,
) -> List[str]:
    """Extract station identifiers without assuming a country-specific code list.

    Numeric identifiers are accepted from filenames and metadata. Alphanumeric
    identifiers are accepted only when the caller reads an explicit code field.
    """
    if texte is None:
        return []
    texte = str(texte).strip()
    propres = set()

    for match_code in re.finditer(r"(?<!\d)(\d{3,12})(?!\d)", texte):
        code = match_code.group(1)
        if len(code) == 4 and 1800 <= int(code) <= 2200:
            continue
        if (len(code) == 8 and 1800 <= int(code[:4]) <= 2200
                and 1 <= int(code[4:6]) <= 12 and 1 <= int(code[6:8]) <= 31):
            continue
        # If the digits belong to a genuine alphanumeric identifier, keep the
        # complete identifier below instead of also returning its numeric tail.
        prefix_match = re.search(r"(?<![A-Za-z])([A-Za-z]{1,8}[-_]?)$", texte[:match_code.start()])
        prefix = prefix_match.group(1).rstrip("-_").upper() if prefix_match else ""
        generic_prefixes = {
            "TEMP", "TMAX", "TMIN", "PRECIP", "RAINFALL", "RAIN",
            "DATA", "FILE", "STATION", "SITE", "CODE", "PR", "TP",
        }
        if autoriser_alphanumerique and prefix and prefix not in generic_prefixes:
            continue
        propres.add(code)

    if autoriser_alphanumerique:
        # Alphanumeric identifiers are safe to extract when they contain digits.
        motif_alphanumerique = r"(?<![A-Za-z0-9])([A-Za-z]{1,8}[-_]?[0-9]{2,12})(?![A-Za-z0-9])"
        exclus = {
            "STATION", "STATIONS", "SITE", "SITES", "POSTE", "POSTES",
            "CODE", "CODES", "ID", "WMO", "ICAO", "NAME", "NOM",
            "TEMP", "TMAX", "TMIN", "PRECIP", "RAINFALL", "RAIN", "DATA",
            "FILE", "YEAR", "MONTH", "DAY", "ESTACION", "ESTACAO", "STAZIONE",
            "رمز", "كود", "رقم", "معرف", "المحطة", "الموقع",
        }
        for token in re.findall(motif_alphanumerique, texte, flags=re.I):
            code = re.sub(r"[^A-Za-z0-9]", "", token).upper()
            prefixe = re.match(r"^[A-Z]+", code)
            prefixe = prefixe.group(0) if prefixe else ""
            if code and code not in exclus and prefixe not in {
                "TEMP", "TMAX", "TMIN", "PRECIP", "RAINFALL", "RAIN",
                "DATA", "FILE", "STATION", "SITE", "CODE", "PR", "TP",
            }:
                propres.add(code)

        # A code made of letters only is inherently ambiguous with a station
        # name. Accept it only when the complete explicit code-field value is
        # one short token, never by extracting words from a station-name phrase.
        if autoriser_codes_alphabetiques:
            valeur_code = texte.strip().strip("|;,:=#- ")
            if re.fullmatch(r"[A-Za-z]{3,8}", valeur_code):
                code = valeur_code.upper()
                if code not in exclus:
                    propres.add(code)

    return sorted(
        propres,
        key=lambda code: (
            0 if len(code) == 6 else 1 if len(code) == 3 else 2,
            abs(len(code) - 6), len(code), code,
        ),
    )


_STATION_REFERENCE_CACHE = None
_STATION_REFERENCE_CONFIG_CACHE = None

_DEFAULT_STATION_HEADERS = {
    "name": (
        "station", "stations", "station name", "name station", "site", "site name",
        "nom station", "nom de station", "nom de la station", "nom poste", "poste",
        "estacion", "nombre estacion", "nombre de estacion", "nombre de la estacion",
        "estação", "nome estação", "nome da estação", "estacao", "nome estacao",
        "stazione", "nome stazione", "اسم المحطة", "اسم الموقع", "المحطة", "الموقع",
        "محطة", "موقع",
    ),
    "code": (
        "station code", "code station", "code de station", "code de la station",
        "code poste", "station id", "id station", "site id", "site code",
        "wmo", "wmo id", "wmo code", "icao", "icao code", "station number",
        "number station", "numero station", "numero de station", "code wmo",
        "codigo estacion", "codigo de estacion", "codigo de la estacion",
        "codigo puesto", "id estacion", "código da estação", "codigo da estacao",
        "id da estação", "id da estacao", "numero da estacao", "codice stazione",
        "id stazione", "stationscode", "stationsnummer", "رمز المحطة", "كود المحطة",
        "رقم المحطة", "معرف المحطة", "معرّف المحطة", "رمز الموقع", "كود الموقع", "رقم الموقع",
    ),
    "year": (
        "year", "years", "annual year", "hydrological year", "hydrologic year",
        "annee", "annee hydrologique", "año", "ano", "ano hidrologico",
        "jahr", "anno", "السنة", "سنة", "السنة الهيدرولوجية",
    ),
}


def _charger_configuration_reference_stations() -> Dict[str, object]:
    """Load generic station-field vocabulary and optional user-managed aliases."""
    global _STATION_REFERENCE_CONFIG_CACHE
    if _STATION_REFERENCE_CONFIG_CACHE is not None:
        return _STATION_REFERENCE_CONFIG_CACHE
    chemin = Path(__file__).resolve().parent.parent / "station_reference.json"
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
        _STATION_REFERENCE_CONFIG_CACHE = donnees if isinstance(donnees, dict) else {}
    except Exception:
        _STATION_REFERENCE_CONFIG_CACHE = {}
    return _STATION_REFERENCE_CONFIG_CACHE


def _entetes_station(type_entete: str) -> set:
    configuration = _charger_configuration_reference_stations()
    vocabulaire = configuration.get("metadata_headers") or {}
    valeurs = list(_DEFAULT_STATION_HEADERS.get(type_entete, ()))
    valeurs.extend(vocabulaire.get(type_entete) or [])
    return {normaliser_compact(value) for value in valeurs if str(value).strip()}


def _entetes_station_bruts(type_entete: str) -> List[str]:
    configuration = _charger_configuration_reference_stations()
    vocabulaire = configuration.get("metadata_headers") or {}
    valeurs = list(_DEFAULT_STATION_HEADERS.get(type_entete, ()))
    valeurs.extend(vocabulaire.get(type_entete) or [])
    return sorted({str(value).strip() for value in valeurs if str(value).strip()}, key=len, reverse=True)


def _est_entete_station(valeur, type_entete: str) -> bool:
    compact = normaliser_compact(valeur)
    if not compact:
        return False
    entetes = _entetes_station(type_entete)
    if compact in entetes:
        return True
    suffixes = {"wmo", "icao", "official", "local", "name", "code", "id", "en", "fr", "es", "pt", "ar"}
    return any(
        compact.startswith(entete) and compact[len(entete):] in suffixes
        for entete in entetes if len(entete) >= 5
    )


def _contient_lettre(texte: str) -> bool:
    return any(character.isalpha() for character in str(texte))


def _est_entete_nom_station_specifique(valeur) -> bool:
    compact = normaliser_compact(valeur)
    generiques = {
        normaliser_compact(item)
        for item in ("station", "stations", "site", "sites", "poste", "nom", "name",
                     "estacion", "estação", "estacao", "stazione", "محطة", "موقع", "المحطة", "الموقع")
    }
    return _est_entete_station(valeur, "name") and compact not in generiques


def nettoyer_nom_station(texte: str) -> str:
    """Normalize station names across scripts, without a bundled station list."""
    texte = normaliser_texte(texte)
    texte = re.sub(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])", " ", texte, flags=re.UNICODE)
    texte = re.sub(r"(?<!\d)\d{3,12}(?!\d)", " ", texte)
    mots_generiques = (set(MOTS_STATION_A_SUPPRIMER) - {"t", "p"}) | {
        "converted", "converti", "convertie", "conversion", "fichier", "file",
        "mensuel", "mensuelle", "journalier", "journaliere", "monthly", "daily",
        "moyenne", "moyennes", "maximum", "maximale", "maximales",
        "minimum", "minimale", "minimales", "periode", "period", "altitude",
        "latitude", "longitude", "annee", "year", "years", "station", "stations",
        "input", "output", "source", "result", "resultat", "test", "old",
        "copy", "copie", "final", "version", "original", "rain", "rainfall",
        "precip", "pluie", "pluviometrie", "hydro", "meteo", "max", "min",
        "moy", "moyen", "tmax", "tmin", "statistique", "statistiques", "statistics",
        "site", "sites", "name", "nom", "code", "codes", "id", "identifier",
        "identifiant", "number", "numero", "num", "location", "place", "value",
        "values", "variable", "parameter", "month", "day", "estacion", "estacao",
        "stazione", "nombre", "nome", "wmo", "icao", "رمز", "كود", "رقم",
        "معرف", "اسم", "المحطة", "الموقع", "محطة", "موقع", "السنة", "سنة",
        "الشهر", "اليوم",
    }
    mots = [mot for mot in texte.split() if mot not in mots_generiques]
    cle = re.sub(r"[^\w]+", "", "".join(mots), flags=re.UNICODE).strip("_")
    return cle


def _extraire_nom_station_depuis_ligne(texte: str) -> str:
    """Extract a station name from multilingual, explicitly labelled metadata."""
    if not texte:
        return ""
    texte = str(texte)
    match = None
    motifs = (
        r"nom\s*(?:de\s*)?(?:station|poste)\s*[:=]\s*(.+)",
        r"(?<!code )station\s*[:=]\s*(.+)",
        r"(?<!code )poste\s*[:=]\s*(.+)",
    )
    for motif in motifs:
        match = re.search(motif, texte, flags=re.I)
        if match:
            break
    if not match:
        for entete in _entetes_station_bruts("name"):
            motif = r"(?<!\w)" + re.escape(entete).replace(r"\ ", r"\s+") + r"\s*[:=]\s*(.+)"
            match = re.search(motif, texte, flags=re.I | re.UNICODE)
            if match:
                break
    if not match:
        return ""

    nom = match.group(1).strip()
    nom = re.split(
        r"(?:nom\s*(?:de\s*)?(?:station|poste)|periode|p[eé]riode|altitude|latitude|longitude|annee|ann[eé]e|year|code)\s*[:=]",
        nom, maxsplit=1, flags=re.I,
    )[0]
    for entete_code in _entetes_station_bruts("code"):
        motif_code = r"(?<!\w)" + re.escape(entete_code).replace(r"\ ", r"\s+") + r"\s*[:=]"
        match_code = re.search(motif_code, nom, flags=re.I | re.UNICODE)
        if match_code:
            nom = nom[:match_code.start()]
            break
    nom = nom.strip(" |;,-_")
    if re.fullmatch(r"\d{3,12}", nom.replace(" ", "")):
        return ""
    cle = nettoyer_nom_station(nom)
    if len(cle) < 2 or not _contient_lettre(cle) or _est_entete_station(nom, "name"):
        return ""
    return nom


def _extraire_code_station_depuis_ligne(texte: str) -> str:
    """Read a code immediately following any configured multilingual code label."""
    if not texte:
        return ""
    texte = str(texte)
    for entete in _entetes_station_bruts("code"):
        motif = (
            r"(?<!\w)" + re.escape(entete).replace(r"\ ", r"\s+")
            + r"(?:\s*[:=#-]\s*|\s+)([A-Za-z0-9][A-Za-z0-9._/-]{2,19})(?![A-Za-z0-9])"
        )
        match = re.search(motif, texte, flags=re.I | re.UNICODE)
        if match:
            return match.group(1)
    return ""


def _charger_reference_stations() -> List[Dict[str, object]]:
    """Load optional user-defined station aliases; the public file is generic and empty."""
    global _STATION_REFERENCE_CACHE
    if _STATION_REFERENCE_CACHE is not None:
        return _STATION_REFERENCE_CACHE
    donnees = _charger_configuration_reference_stations()
    _STATION_REFERENCE_CACHE = list(donnees.get("stations") or [])
    return _STATION_REFERENCE_CACHE

def _enrichir_identifiants_avec_reference(codes, noms):
    """Expand known code/name aliases without applying fuzzy or substring matching."""
    codes = set(map(str, codes or []))
    noms = set(map(str, noms or []))
    for station in _charger_reference_stations():
        codes_reference = set(map(str, station.get("codes") or []))
        noms_reference = set(map(str, station.get("name_keys") or []))
        if codes.intersection(codes_reference) or noms.intersection(noms_reference):
            codes.update(codes_reference)
            canonical_key = nettoyer_nom_station(station.get("canonical_name") or "")
            if canonical_key:
                noms.add(canonical_key)
    return codes, noms


def _est_classeur_multi_stations(feuilles: Sequence[pd.DataFrame]) -> bool:
    """Detect multi-station tables from their own metadata, without a station catalogue."""
    year_headers = _entetes_station("year")

    for df in feuilles:
        if df is None or df.empty:
            continue
        zone = df.iloc[: min(500, df.shape[0]), : min(80, df.shape[1])]

        # Structured layout: one row per station or per station/year.
        for r in range(min(30, zone.shape[0])):
            vals = ["" if pd.isna(v) else str(v).strip() for v in zone.iloc[r, :].tolist()]
            code_cols = [i for i, value in enumerate(vals) if _est_entete_station(value, "code")]
            name_cols = [i for i, value in enumerate(vals) if _est_entete_station(value, "name")]
            if not code_cols and not name_cols:
                continue

            codes, names = set(), set()
            for rr in range(r + 1, zone.shape[0]):
                row = zone.iloc[rr, :].tolist()
                for c in code_cols:
                    if c < len(row) and pd.notna(row[c]):
                        codes.update(extraire_codes_station_depuis_texte(
                            row[c], autoriser_alphanumerique=True,
                            autoriser_codes_alphabetiques=True,
                        ))
                for c in name_cols:
                    if c < len(row) and pd.notna(row[c]):
                        value = str(row[c]).strip()
                        key = nettoyer_nom_station(value)
                        if (key and len(key) >= 2 and _contient_lettre(key)
                                and not _est_entete_station(value, "name")
                                and not _est_entete_station(value, "code")):
                            names.add(key)
            if len(codes) > 1 or len(names) > 1:
                return True

        # Wide layout: station names are column headings beside a year column.
        for r in range(min(12, zone.shape[0])):
            vals = ["" if pd.isna(v) else str(v).strip() for v in zone.iloc[r, :].tolist()]
            has_year = any(value in year_headers or _est_entete_station(value, "year") for value in vals)
            if not has_year:
                continue
            names, codes = set(), set()
            for value in vals:
                key = nettoyer_nom_station(value) if value else ""
                if (key and len(key) >= 2 and _contient_lettre(key)
                        and not _est_entete_station(value, "name")
                        and not _est_entete_station(value, "code")):
                    names.add(key)
                if value:
                    codes.update(extraire_codes_station_depuis_texte(
                        value, autoriser_alphanumerique=True,
                        autoriser_codes_alphabetiques=True,
                    ))
            if len(names) >= 2 or len(codes) >= 2:
                return True
    return False

def _obtenir_ancrages_images_excel(fichier: Path) -> List[List[Tuple[int, int, int, int]]]:
    """Return zero-based image bounds (top, left, bottom, right) per sheet.

    Embedded images are layout hints only. Station names/codes are always
    extracted from worksheet cells located below the image, never from pixels.
    """
    fichier = Path(fichier)
    if fichier.suffix.lower() not in {".xlsx", ".xlsm"}:
        return []
    try:
        from openpyxl import load_workbook
        classeur = load_workbook(
            fichier, read_only=False, data_only=True,
            keep_vba=(fichier.suffix.lower() == ".xlsm"),
        )
        try:
            resultat = []
            for feuille in classeur.worksheets:
                ancrages = []
                for image in getattr(feuille, "_images", []) or []:
                    ancre = getattr(image, "anchor", None)
                    debut = getattr(ancre, "_from", None)
                    if debut is None:
                        continue
                    fin = getattr(ancre, "_to", None)
                    row_top = max(0, int(debut.row))
                    col_left = max(0, int(debut.col))
                    # Two-cell anchors define the image's lower/right edge.
                    # For one-cell anchors, estimate the occupied cells from
                    # the actual rendered image dimensions (EMU -> pixels).
                    if fin is not None:
                        row_bottom = max(row_top, int(fin.row) - 1)
                        col_right = max(col_left, int(fin.col) - 1)
                    else:
                        extension = getattr(ancre, "ext", None)
                        width_px = float((getattr(extension, "cx", 0) / 9525) or getattr(image, "width", 0))
                        height_px = float((getattr(extension, "cy", 0) / 9525) or getattr(image, "height", 0))
                        span_rows = max(1, int((height_px + 19) // 20))
                        span_cols = max(1, int((width_px + 63) // 64))
                        row_bottom = row_top + span_rows - 1
                        col_right = col_left + span_cols - 1
                    ancrages.append((row_top, col_left, row_bottom, col_right))
                resultat.append(ancrages)
            return resultat
        finally:
            classeur.close()
    except Exception:
        # Legacy formats still use cell-based metadata detection.
        return []


def _code_station_non_libelle(valeur) -> str:
    """Recognize a standalone station code without mistaking years for IDs."""
    if valeur is None:
        return ""
    texte = str(valeur).strip().strip("()[]{}<># ")
    if not texte:
        return ""
    match = re.fullmatch(r"(\d{3,10})(?:\.0+)?", texte)
    if match:
        chiffres = match.group(1)
        nombre = int(chiffres)
        if 1800 <= nombre <= 2200:
            return ""
        return chiffres
    match = re.fullmatch(
        r"(?:[A-Za-z]{1,5}[-_/]?\d{2,10}|\d{2,10}[-_/]?[A-Za-z]{1,5})",
        texte,
    )
    if not match:
        return ""
    code = re.sub(r"[^A-Za-z0-9]", "", texte).upper()
    return code if len(code) >= 3 and any(char.isdigit() for char in code) else ""


def _nom_station_non_libelle(valeur) -> str:
    """Conservative candidate extractor for unlabelled top-of-sheet metadata."""
    if valeur is None:
        return ""
    texte = str(valeur).strip().strip("|;,:= -_\t\r\n")
    if not texte or len(texte) > 72:
        return ""
    if _code_station_non_libelle(texte):
        return ""

    # Remove common descriptive labels while preserving the station's own name.
    texte = re.sub(
        r"(?i)^(?:station|site|poste|name|nom|location|place)\s*(?:de|du|da|do|of|the|la|le)?\s*[:=#-]?\s*",
        "", texte,
    )
    texte = re.sub(
        r"(?i)\s+(?:station|site|poste|name|nom)$", "", texte,
    ).strip(" |;,:=-_()[]")
    texte = re.sub(r"\b\d{3,10}(?:\.0+)?\b", " ", texte)
    if not texte or not _contient_lettre(texte):
        return ""

    normalise = normaliser_texte(texte)
    compact = normaliser_compact(texte)
    if not normalise or _est_entete_station(texte, "name") or _est_entete_station(texte, "code"):
        return ""

    # Exclude workbook titles, variable labels, administrative headings and
    # climate-table labels. These are not station identities.
    mots_interdits = {
        "hydrostation", "hydrologie", "hydrologique", "bilan", "hydrique",
        "temperature", "temperatures", "precipitation", "precipitations",
        "pluie", "pluviometrie", "rain", "rainfall", "water", "balance",
        "moyenne", "moyennes", "mean", "average", "maximum", "minimum",
        "mensuel", "mensuelle", "monthly", "annuel", "annuelle", "annual",
        "journalier", "journaliere", "daily", "data", "donnees", "dataset",
        "rapport", "report", "resultat", "result", "tableau", "table", "chart",
        "graphique", "periode", "period", "annee", "year", "month", "mois",
        "date", "day", "altitude", "latitude", "longitude", "direction",
        "ministere", "ministry", "office", "national", "service", "universite",
        "university", "laboratoire", "laboratory", "logo", "template", "modele",
        "example", "exemple", "test", "source", "output", "input", "fichier", "file",
    }
    mots = set(normalise.split())
    if mots.intersection(mots_interdits):
        return ""
    if compact in {"hydrostation", "bilanhydrique", "temperature", "precipitation", "station", "site"}:
        return ""

    cle = nettoyer_nom_station(texte)
    if len(cle) < 3 or not _contient_lettre(cle):
        return ""
    # An unlabelled station name should be a short text value, not a sentence.
    if len(normalise.split()) > 7:
        return ""
    return texte


def _extraire_paire_station_cellule(valeur) -> Tuple[str, str]:
    """Extract a station name and code written together in one cell."""
    if valeur is None:
        return "", ""
    texte = str(valeur).strip()
    if not texte:
        return "", ""
    # Prefer a standalone numeric code or an explicitly delimited/alphanumeric
    # code token. Do not absorb the last word of a station name (e.g. "Delta 444555")
    # into a fake code such as "DELTA444555".
    motif = re.compile(
        r"(?<![A-Za-z0-9])(?:[A-Za-z]{1,5}[-_/]\d{2,10}|[A-Za-z]{1,3}\d{2,10}|\d{3,10})(?:\.0+)?(?![A-Za-z0-9])"
    )
    for match in motif.finditer(texte):
        code = _code_station_non_libelle(match.group(0))
        if not code:
            continue
        reste = (texte[:match.start()] + " " + texte[match.end():]).strip()
        reste = re.sub(
            r"(?i)\b(?:station|site|poste|name|nom|code|id|number|numero|num|wmo|icao)\b",
            " ", reste,
        )
        reste = re.sub(
            r"(?:رمز|كود|رقم|معرف|معرّف|المحطة|الموقع|محطة|موقع)",
            " ", reste,
        )
        nom = _nom_station_non_libelle(reste)
        if nom:
            return nom, code
    return "", ""


def extraire_identifiants_station_depuis_contenu(fichier: Path) -> Dict[str, object]:
    """Read station identity from image-below metadata and the first 10 rows.

    Supported layouts include a name/code pair in one cell, or a name and a
    code in separate cells on the same row. For embedded worksheet logos,
    unlabelled values in the cells immediately below the image are also read.
    """
    fichier = Path(fichier)
    ident = {
        "code": "", "nom": "", "cle_nom": "", "codes": [], "noms": [],
        "codes_logo": [], "noms_logo": [],
        "codes_top10": [], "noms_top10": [],
        "conflit": "", "type_temperature": "inconnu",
        "codes_explicites": [], "noms_explicites": [], "multi_station": False,
    }

    extension = fichier.suffix.lower()
    ancrages_images_par_feuille = []
    configuration = _charger_configuration_reference_stations()
    layouts = configuration.get("metadata_layouts") or {}
    lignes_sous_image = max(3, min(25, int(layouts.get("rows_below_image", 12))))
    lignes_premieres = 10

    try:
        if extension in {".xlsx", ".xls", ".xlsm", ".xlsb", ".ods"}:
            ancrages_images_par_feuille = _obtenir_ancrages_images_excel(fichier)
            lignes_a_lire = 30
            for feuille_ancrages in ancrages_images_par_feuille:
                for row_top, _col_left, row_bottom, _col_right in feuille_ancrages:
                    debut_sous_image = max(row_top + 1, row_bottom + 1)
                    lignes_a_lire = max(lignes_a_lire, debut_sous_image + lignes_sous_image)
            lignes_a_lire = min(80, lignes_a_lire)
            with pd.ExcelFile(fichier) as excel:
                feuilles = [
                    pd.read_excel(excel, sheet_name=nom, header=None, nrows=lignes_a_lire)
                    for nom in excel.sheet_names
                ]
        elif extension in {".csv", ".txt", ".tsv"} or not extension:
            df = _lire_fichier_texte_uncached(fichier)
            feuilles = [df.head(30)]
        else:
            return ident
    except Exception as exc:
        ident["conflit"] = f"Lecture d'identite impossible: {exc}"
        return ident

    # Multi-station workbooks remain a separate workflow. Their table structure
    # is inspected independently from the station-identity matching window.
    if _est_classeur_multi_stations(feuilles):
        ident["multi_station"] = True
        ident["conflit"] = "Classeur multi-stations: plusieurs stations sont organisees dans le meme tableau."
        return ident

    codes_logo = set()
    noms_logo = {}
    codes_top10 = set()
    noms_top10 = {}
    codes_explicites = set()
    noms_explicites = {}
    types_temperature = set()

    def ajouter_nom(cible, nom, explicite=False):
        if not nom:
            return
        cle = nettoyer_nom_station(nom)
        if len(cle) < 2 or not _contient_lettre(cle):
            return
        cible[cle] = str(nom).strip()
        if explicite:
            noms_explicites[cle] = str(nom).strip()

    def ajouter_code(cible, code, explicite=False):
        if not code:
            return
        code = re.sub(r"[^A-Za-z0-9]", "", str(code)).upper()
        if len(code) < 3:
            return
        cible.add(code)
        if explicite:
            codes_explicites.add(code)

    def est_libelle_code(item):
        label = normaliser_texte(item).strip()
        compact = normaliser_compact(item)
        return (
            _est_entete_station(item, "code")
            or label in {"code", "codes", "id", "identifier", "identifiant", "numero", "number", "num"}
            or compact in {"codestation", "codeposte", "stationcode", "postcode", "stationid", "siteid"}
            or ("code" in label and any(word in label for word in ("station", "poste", "site", "estacion", "stazione")))
        )

    def est_libelle_nom(item):
        label = normaliser_texte(item).strip()
        return (
            _est_entete_station(item, "name")
            or label in {"station", "stations", "site", "sites", "poste", "nom", "name", "location", "place"}
            or ("nom" in label and any(word in label for word in ("station", "poste", "site")))
        )

    for index_feuille, df in enumerate(feuilles):
        if df is None or df.empty:
            continue

        ancrages = (
            ancrages_images_par_feuille[index_feuille]
            if index_feuille < len(ancrages_images_par_feuille) else []
        )
        zones_image = {}
        for row_top, col_left, row_bottom, col_right in ancrages:
            # Begin below the complete image, not merely below its top-left cell.
            row_start = max(row_top + 1, row_bottom + 1)
            row_end = min(len(df), row_start + lignes_sous_image)
            col_start = max(0, col_left - 3)
            col_end = min(60, max(col_left + 10, col_right + 8))
            for rr in range(row_start, row_end):
                zones_image.setdefault(rr, set()).update(range(col_start, col_end))

        # Read a small header window for file-type validation, but identity
        # matching below is still limited to the first ten rows plus logo area.
        last_row = min(len(df), 30)
        if zones_image:
            last_row = max(last_row, min(len(df), max(zones_image) + 1))

        for r in range(last_row):
            values = ["" if pd.isna(v) else str(v).strip() for v in df.iloc[r, :60].tolist()]
            non_empty = [v for v in values if v]
            if not non_empty:
                continue

            colonnes_logo = zones_image.get(r, set())
            est_zone_logo = bool(colonnes_logo)
            est_premiere_zone = r < lignes_premieres

            row_text = " ".join(non_empty)
            row_norm = normaliser_texte(row_text)
            # Temperature-type validation is independent from station matching
            # and may use the broader 30-row header window.
            if "temp" in row_norm or "temperature" in row_norm or "temperatures" in row_norm:
                if ligne_est_min_ou_max_temperature(non_empty):
                    types_temperature.add("minmax")
                elif any(token in row_norm for token in ("moy", "mean", "average")):
                    types_temperature.add("moyenne")

            if not est_zone_logo and not est_premiere_zone:
                continue

            row_is_measurement = any(token in row_norm for token in (
                "altitude", "latitude", "longitude", "temperature", "precipitation",
                "pluie", "rainfall", "annee", "year", "date", "month", "mois",
                "periode", "period", "moyenne", "mean", "maximum", "minimum",
            )) or any(est_annee(value) for value in non_empty) or len(identifier_colonnes_mois(non_empty)) >= 3

            row_codes = set()
            row_names = {}
            row_codes_logo = set()
            row_names_logo = {}
            row_codes_explicit = set()
            row_names_explicit = {}

            for c, item in enumerate(values):
                if not item:
                    continue
                in_logo_columns = est_zone_logo and c in colonnes_logo
                # For rows after the first ten, inspect only cells under the logo.
                if not est_premiere_zone and not in_logo_columns:
                    continue

                # Name and code may be written together in a single cell.
                pair_name, pair_code = _extraire_paire_station_cellule(item)
                if pair_name and pair_code:
                    row_codes.add(pair_code)
                    pair_key = nettoyer_nom_station(pair_name)
                    row_names[pair_key] = pair_name
                    if in_logo_columns:
                        row_codes_logo.add(pair_code)
                        row_names_logo[pair_key] = pair_name
                    continue

                # Explicit metadata in one cell, e.g. "Station: ..." or "Code: ...".
                code_direct = _extraire_code_station_depuis_ligne(item)
                if code_direct:
                    for found in extraire_codes_station_depuis_texte(
                        code_direct, autoriser_alphanumerique=True,
                        autoriser_codes_alphabetiques=True,
                    ):
                        row_codes.add(found)
                        row_codes_explicit.add(found)
                        if in_logo_columns:
                            row_codes_logo.add(found)
                name_direct = _extraire_nom_station_depuis_ligne(item)
                if name_direct:
                    key_name = nettoyer_nom_station(name_direct)
                    row_names[key_name] = name_direct
                    row_names_explicit[key_name] = name_direct
                    if in_logo_columns:
                        row_names_logo[key_name] = name_direct

                # A label and its value may occupy separate cells on the same row.
                code_label = est_libelle_code(item)
                name_label = est_libelle_nom(item) and not code_label
                if code_label or name_label:
                    for candidate_col in range(c + 1, min(len(values), c + 7)):
                        candidate = values[candidate_col]
                        if not candidate:
                            continue
                        candidate_in_logo = est_zone_logo and candidate_col in colonnes_logo
                        if code_label:
                            found_codes = extraire_codes_station_depuis_texte(
                                candidate, autoriser_alphanumerique=True,
                                autoriser_codes_alphabetiques=True,
                            )
                            if not found_codes:
                                simple = _code_station_non_libelle(candidate)
                                found_codes = [simple] if simple else []
                            if found_codes:
                                for found in found_codes:
                                    row_codes.add(found)
                                    row_codes_explicit.add(found)
                                    if in_logo_columns or candidate_in_logo:
                                        row_codes_logo.add(found)
                                break
                        if name_label:
                            candidate_name = _nom_station_non_libelle(candidate)
                            if candidate_name:
                                key_name = nettoyer_nom_station(candidate_name)
                                row_names[key_name] = candidate_name
                                row_names_explicit[key_name] = candidate_name
                                if in_logo_columns or candidate_in_logo:
                                    row_names_logo[key_name] = candidate_name
                                break

                # Unlabelled values are accepted only in the first ten rows or
                # in the cells immediately below an embedded image. Measurement
                # rows are excluded to avoid treating rainfall values as IDs.
                if not row_is_measurement:
                    simple_code = _code_station_non_libelle(item)
                    if simple_code and (in_logo_columns or est_premiere_zone):
                        row_codes.add(simple_code)
                        if in_logo_columns:
                            row_codes_logo.add(simple_code)
                    simple_name = _nom_station_non_libelle(item)
                    if simple_name:
                        key_name = nettoyer_nom_station(simple_name)
                        row_names[key_name] = simple_name
                        if in_logo_columns:
                            row_names_logo[key_name] = simple_name

            if est_premiere_zone:
                if not row_is_measurement:
                    # Same-row matching: a code/name pair can be in one cell or
                    # in two separate cells. A single unlabelled identity is
                    # also retained for comparison with the other workbook.
                    if len(row_codes) == 1:
                        codes_top10.update(row_codes)
                    if len(row_names) == 1:
                        noms_top10.update(row_names)
                codes_top10.update(row_codes_explicit)
                noms_top10.update(row_names_explicit)

            if est_zone_logo and not row_is_measurement:
                # The logo area may contain the code and name on different rows.
                # Only cells inside the image's column footprint count as logo metadata.
                codes_logo.update(row_codes_logo)
                noms_logo.update(row_names_logo)

    # Discard ambiguous logo regions that contain multiple distinct IDs/names.
    if len(codes_logo) > 1:
        codes_logo.clear()
    if len(noms_logo) > 1:
        noms_logo.clear()

    codes = set(codes_logo) | set(codes_top10)
    noms = dict(noms_logo)
    noms.update(noms_top10)

    ident["codes_logo"] = sorted(codes_logo)
    ident["noms_logo"] = sorted(noms_logo)
    ident["codes_top10"] = sorted(codes_top10)
    ident["noms_top10"] = sorted(noms_top10)
    ident["codes"] = sorted(codes)
    ident["noms"] = sorted(noms)
    ident["codes_explicites"] = sorted(codes_explicites | codes_top10 | codes_logo)
    ident["noms_explicites"] = sorted(set(noms_explicites) | set(noms))
    if codes_logo:
        ident["code"] = next(iter(codes_logo))
    elif codes_top10:
        ident["code"] = sorted(codes_top10, key=lambda x: (len(x) != 6, len(x), x))[0]
    if noms_logo:
        ident["cle_nom"] = next(iter(noms_logo))
        ident["nom"] = noms_logo[ident["cle_nom"]]
    elif noms_top10:
        ident["cle_nom"] = sorted(noms_top10)[0]
        ident["nom"] = noms_top10[ident["cle_nom"]]
    if len(codes) > 1:
        ident["conflit"] = "Plusieurs codes station detectes dans les zones de metadata."
    if len(noms) > 1:
        ident["conflit"] = (ident["conflit"] + "; " if ident["conflit"] else "") + \
            "Plusieurs noms station detectes dans les zones de metadata."
    if "moyenne" in types_temperature:
        ident["type_temperature"] = "moyenne"
    elif "minmax" in types_temperature:
        ident["type_temperature"] = "minmax"
    return ident











_STATION_META_CACHE = {}


def extraire_identifiants_station(
    fichier: Path, inclure_contenu: bool = True
) -> Dict[str, object]:
    """Return station IDs with explicit source priority: logo, top 10 rows, filename."""
    fichier = Path(fichier)
    cache_type = "station_metadata_v7_content" if inclure_contenu else "station_metadata_v7_filename"
    key = _cache_key_fichier(fichier, cache_type)
    if key in _STATION_META_CACHE:
        return _STATION_META_CACHE[key]

    stem_compact = re.sub(r"[^A-Za-z0-9]", "", fichier.stem)
    if re.fullmatch(r"[A-Z]{1,4}\d{2,12}", stem_compact):
        codes_fichier = {stem_compact}
    else:
        codes_fichier = set(extraire_codes_station_depuis_texte(fichier.stem))
    nom_fichier = nettoyer_nom_station(fichier.stem)
    if (not _contient_lettre(nom_fichier)
            or any(normaliser_compact(code) == normaliser_compact(stem_compact) for code in codes_fichier)):
        nom_fichier = ""
    noms_fichier = {nom_fichier} if nom_fichier else set()

    nom_fichier_norm = normaliser_texte(fichier.stem)
    type_temperature_fichier = (
        "minmax"
        if re.search(r"\b(tmin|tmax|temperature\s*(?:min|max|minimum|maximum)|temp\s*(?:min|max))\b",
                     nom_fichier_norm)
        else "inconnu"
    )

    content = (
        extraire_identifiants_station_depuis_contenu(fichier)
        if inclure_contenu else
        {
            "code": "", "nom": "", "cle_nom": "", "codes": [], "noms": [],
            "codes_logo": [], "noms_logo": [], "codes_top10": [], "noms_top10": [],
            "conflit": "", "multi_station": False, "type_temperature": "inconnu",
            "codes_explicites": [], "noms_explicites": [],
        }
    )

    codes_logo = set(map(str, content.get("codes_logo") or []))
    noms_logo = set(map(str, content.get("noms_logo") or []))
    codes_top10 = set(map(str, content.get("codes_top10") or []))
    noms_top10 = set(map(str, content.get("noms_top10") or []))
    codes_contenu = codes_logo | codes_top10
    noms_contenu = noms_logo | noms_top10

    codes, noms = _enrichir_identifiants_avec_reference(
        codes_contenu | codes_fichier,
        noms_contenu | noms_fichier,
    )
    # Preferred single value is selected by source priority, not lexical order.
    codes_preferes = codes_logo or codes_top10 or codes_fichier
    noms_preferes = noms_logo or noms_top10 or noms_fichier
    preferred_codes = sorted(codes_preferes, key=lambda c: (len(str(c)) != 6, len(str(c)), str(c)))
    preferred_names = sorted(noms_preferes)

    identity = {
        "code": str(preferred_codes[0]) if preferred_codes else "",
        "codes": sorted(codes),
        "cle_nom": str(preferred_names[0]) if preferred_names else "",
        "names": sorted(noms),
        "nom_source": fichier.stem,
        "nom_interne": str(content.get("nom") or ""),
        "conflit": str(content.get("conflit") or ""),
        "multi_station": bool(content.get("multi_station", False)),
        "source_fichier": sorted(codes_fichier | noms_fichier),
        "source_contenu": sorted(codes_contenu | noms_contenu),
        "codes_logo": sorted(codes_logo),
        "names_logo": sorted(noms_logo),
        "codes_top10": sorted(codes_top10),
        "names_top10": sorted(noms_top10),
        "codes_filename": sorted(codes_fichier),
        "names_filename": sorted(noms_fichier),
        "type_temperature": (
            str(content.get("type_temperature"))
            if content.get("type_temperature") not in (None, "", "inconnu")
            else type_temperature_fichier
        ),
    }
    _STATION_META_CACHE[key] = identity
    return identity




def nom_station_sortie(f_temp: Path, f_pr: Path) -> str:
    """
    Nom du fichier de sortie : priorité au code station, puis au nom précipitation.
    """
    id_pr = extraire_identifiants_station(f_pr)
    id_t = extraire_identifiants_station(f_temp)

    code = id_pr.get("code") or id_t.get("code")
    if code:
        return code

    cle = id_pr.get("cle_nom") or id_t.get("cle_nom")
    if cle:
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", cle).strip("_") or "station"

    return re.sub(r"[^a-zA-Z0-9_-]+", "_", (f_pr.stem or f_temp.stem)).strip("_") or "station"
def _source_correspondance_identites(a: Dict[str, object], b: Dict[str, object]) -> str:
    """Return the first exact match source according to the requested priority."""
    def comparer(codes_a, noms_a, codes_b, noms_b):
        commun_codes = set(map(str, codes_a or [])) & set(map(str, codes_b or []))
        commun_noms = set(map(str, noms_a or [])) & set(map(str, noms_b or []))
        return bool(commun_codes or commun_noms)

    logo_a = (a.get("codes_logo") or [], a.get("names_logo") or [])
    logo_b = (b.get("codes_logo") or [], b.get("names_logo") or [])
    top_a = (a.get("codes_top10") or [], a.get("names_top10") or [])
    top_b = (b.get("codes_top10") or [], b.get("names_top10") or [])

    # 1) Metadata below the embedded logo in both workbooks.
    if comparer(*logo_a, *logo_b):
        return "sous_logo"
    # 2) A logo identifier in one file against the first ten rows of the other.
    if comparer(*logo_a, *top_b):
        return "sous_logo_vers_10_premieres_lignes"
    if comparer(*top_a, *logo_b):
        return "10_premieres_lignes_vers_sous_logo"
    # 3) Same name/code inside the first ten rows of both files.
    if comparer(*top_a, *top_b):
        return "10_premieres_lignes"

    internal_a = set(logo_a[0]) | set(logo_a[1]) | set(top_a[0]) | set(top_a[1])
    internal_b = set(logo_b[0]) | set(logo_b[1]) | set(top_b[0]) | set(top_b[1])
    filename_a = (a.get("codes_filename") or [], a.get("names_filename") or [])
    filename_b = (b.get("codes_filename") or [], b.get("names_filename") or [])

    # Filename matching is only a fallback when it does not contradict
    # internal metadata. If both files contain internal IDs and they disagree,
    # do not override that conflict with similar filenames.
    if internal_a and not internal_b:
        if comparer(*logo_a, *filename_b) or comparer(*top_a, *filename_b):
            return "contenu_vers_nom_fichier"
    elif internal_b and not internal_a:
        if comparer(*filename_a, *logo_b) or comparer(*filename_a, *top_b):
            return "nom_fichier_vers_contenu"
    elif not internal_a and not internal_b:
        if comparer(*filename_a, *filename_b):
            return "nom_fichier"
    return ""


def _identites_correspondent(a: Dict[str, object], b: Dict[str, object]) -> bool:
    """A station matches only on an exact shared code or normalized station name."""
    return bool(_source_correspondance_identites(a, b))


def _nom_station_depuis_fichier(fichier: Path) -> str:
    ident = extraire_identifiants_station(fichier)
    codes = list(ident.get("codes") or [])
    if codes:
        # Le code 6 chiffres est le plus utile pour les sorties hydro si disponible.
        codes.sort(key=lambda c: (len(str(c)) != 6, len(str(c))))
        return str(codes[0])
    cle = str(ident.get("cle_nom") or "")
    if cle:
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", cle).strip("_") or "station"
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", fichier.stem).strip("_") or "station"
_ASSOCIATION_TRACE = {}


def _resume_identite_station(identite: Dict[str, object]) -> str:
    codes = list(map(str, identite.get("codes") or []))
    noms = list(map(str, identite.get("names") or []))
    morceaux = []
    if codes:
        morceaux.append("codes=" + ",".join(codes))
    if noms:
        morceaux.append("noms=" + ",".join(noms))
    return " | ".join(morceaux) if morceaux else "aucun identifiant detecte"


def _identite_sortie_station(f_temp: Path, f_pr: Path,
                             id_temp: Dict[str, object],
                             id_pr: Dict[str, object]) -> str:
    codes_communs = set(map(str, id_temp.get("codes") or [])) & set(map(str, id_pr.get("codes") or []))
    if codes_communs:
        code = sorted(codes_communs, key=lambda c: (len(str(c)) != 6, len(str(c)), str(c)))[0]
        return code
    code = id_pr.get("code") or id_temp.get("code")
    if code:
        return str(code)
    noms_communs = set(id_temp.get("names") or []) & set(id_pr.get("names") or [])
    noms = sorted(noms_communs) if noms_communs else (list(id_pr.get("names") or []) or list(id_temp.get("names") or []))
    if noms:
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", str(noms[0])).strip("_") or "station"
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", (f_pr.stem or f_temp.stem)).strip("_") or "station"


def _resoudre_paires_uniques(temperatures, precipitations, identites):
    """Resolve only one-to-one exact code/name matches; never pair by list order."""
    candidats_t = {
        t: [p for p in precipitations if _identites_correspondent(identites[t], identites[p])]
        for t in temperatures
    }
    candidats_p = {
        p: [t for t in temperatures if p in candidats_t[t]]
        for p in precipitations
    }
    paires = []
    utilises_t, utilises_p = set(), set()
    for t in temperatures:
        options = candidats_t[t]
        if len(options) == 1 and len(candidats_p[options[0]]) == 1:
            p = options[0]
            paires.append((t, p))
            utilises_t.add(t)
            utilises_p.add(p)
    return (
        paires,
        [t for t in temperatures if t not in utilises_t],
        [p for p in precipitations if p not in utilises_p],
        candidats_t,
        candidats_p,
    )


def _enregistrer_trace_association(etape, t, p, id_t, id_p):
    codes_t = set(map(str, id_t.get("codes") or []))
    codes_p = set(map(str, id_p.get("codes") or []))
    noms_t = set(id_t.get("names") or [])
    noms_p = set(id_p.get("names") or [])
    communs = []
    communs.extend("code:" + x for x in sorted(codes_t & codes_p))
    communs.extend("nom:" + x for x in sorted(noms_t & noms_p))
    etape_finale = _source_correspondance_identites(id_t, id_p) or etape

    _ASSOCIATION_TRACE[(str(Path(t).resolve()), str(Path(p).resolve()))] = {
        "etape_correspondance": etape_finale,
        "identifiants_temperature": _resume_identite_station(id_t),
        "identifiants_precipitation": _resume_identite_station(id_p),
        "identifiant_commun": ", ".join(communs),
    }




def _ligne_rejet_station(fichier, type_fichier, identite, raison, autre_identite=None):
    return {
        "station": str(identite.get("code") or identite.get("cle_nom") or ""),
        "temperature": str(fichier) if type_fichier == "temperature" else "",
        "precipitation": str(fichier) if type_fichier == "precipitation" else "",
        "resultat": "IGNORE",
        "erreur": raison,
        "identifiants_temperature": (
            _resume_identite_station(identite) if type_fichier == "temperature"
            else (_resume_identite_station(autre_identite) if autre_identite else "")
        ),
        "identifiants_precipitation": (
            _resume_identite_station(identite) if type_fichier == "precipitation"
            else (_resume_identite_station(autre_identite) if autre_identite else "")
        ),
        "etape_correspondance": "sous_logo puis 10 premieres lignes puis nom_fichier",
        "type_temperature_source": str(identite.get("type_temperature") or "inconnu"),
    }




def planifier_associations(
    fichiers_temp: List[Path], fichiers_pr: List[Path]
) -> Tuple[List[Tuple[str, Path, Path]], List[Dict[str, str]]]:
    """Pair stations by logo metadata, first 10 rows, then safe filename fallback."""
    temperatures = sorted(set(map(Path, fichiers_temp)), key=lambda p: str(p).casefold())
    precipitations = sorted(set(map(Path, fichiers_pr)), key=lambda p: str(p).casefold())
    _ASSOCIATION_TRACE.clear()

    # Read only the logo area and first ten worksheet rows for identity matching.
    # This is done for both sides so a logo code can match a name/code in the
    # other workbook, in either direction.
    identites = {
        f: extraire_identifiants_station(f, inclure_contenu=True)
        for f in temperatures + precipitations
    }

    temperatures_admissibles = []
    precipitations_admissibles = []
    rejets = []
    for t in temperatures:
        ident = identites[t]
        if ident.get("multi_station"):
            rejets.append(_ligne_rejet_station(
                t, "temperature", ident,
                "Classeur multi-stations: il contient plusieurs stations et ne peut pas etre associe comme fichier individuel."
            ))
        elif ident.get("type_temperature") == "minmax":
            rejets.append(_ligne_rejet_station(
                t, "temperature", ident,
                "Fichier contenant uniquement des temperatures MIN/MAX; Thornthwaite exige la temperature moyenne."
            ))
        else:
            temperatures_admissibles.append(t)

    for p in precipitations:
        ident = identites[p]
        if ident.get("multi_station"):
            rejets.append(_ligne_rejet_station(
                p, "precipitation", ident,
                "Classeur multi-stations: il contient plusieurs stations; les fichiers individuels sont prioritaires pour la synchronisation."
            ))
        else:
            precipitations_admissibles.append(p)

    pairs, t_restants, p_restants, cand_t, cand_p = _resoudre_paires_uniques(
        temperatures_admissibles, precipitations_admissibles, identites
    )

    paires_finales = []
    for t, p in pairs:
        id_t, id_p = identites[t], identites[p]
        station = _identite_sortie_station(t, p, id_t, id_p)
        paires_finales.append((station, t, p))
        _enregistrer_trace_association("contenu", t, p, id_t, id_p)

    for t in t_restants:
        options = cand_t.get(t, [])
        if len(options) > 1:
            raison = "Association ambigue: " + ", ".join(p.name for p in options)
        elif options:
            raison = "Precipitation deja associee a plusieurs fichiers temperature"
        else:
            raison = "Aucun code ou nom commun (sous logo, puis 10 premieres lignes, puis nom de fichier)"
        ident = identites[t]
        if ident.get("conflit"):
            raison += " | " + str(ident["conflit"])
        rejets.append(_ligne_rejet_station(t, "temperature", ident, raison))

    for p in p_restants:
        options = cand_p.get(p, [])
        if len(options) > 1:
            raison = "Association ambigue: " + ", ".join(t.name for t in options)
        elif options:
            raison = "Temperature deja associee a plusieurs fichiers precipitation"
        else:
            raison = "Aucun code ou nom commun (sous logo, puis 10 premieres lignes, puis nom de fichier)"
        ident = identites[p]
        if ident.get("conflit"):
            raison += " | " + str(ident["conflit"])
        rejets.append(_ligne_rejet_station(p, "precipitation", ident, raison))

    # Prevent two output workbooks with the same station name.
    compteur = Counter(station.casefold() for station, _, _ in paires_finales)
    paires_valides = []
    for station, t, p in paires_finales:
        if compteur[station.casefold()] > 1:
            rejets.append(_ligne_rejet_station(
                t, "temperature", identites[t],
                f"Plusieurs paires generent le meme nom de station {station}"
            ))
            rejets.append(_ligne_rejet_station(
                p, "precipitation", identites[p],
                f"Plusieurs paires generent le meme nom de station {station}"
            ))
        else:
            paires_valides.append((station, t, p))

    return paires_valides, rejets


def planifier_fichier_avec_dossier(
    fichiers_temp: List[Path], fichiers_pr: List[Path], fichier_est_temperature: bool
) -> Tuple[List[Tuple[str, Path, Path]], List[Dict[str, str]]]:
    """Match one selected file against a folder using logo/first-10-row metadata."""
    single = Path(fichiers_temp[0] if fichier_est_temperature else fichiers_pr[0])
    others = [Path(x) for x in (fichiers_pr if fichier_est_temperature else fichiers_temp)]

    id_single = extraire_identifiants_station(single, inclure_contenu=True)
    ids_others = {
        other: extraire_identifiants_station(other, inclure_contenu=True)
        for other in others
    }
    if id_single.get("multi_station"):
        row = _ligne_rejet_station(
            single, "temperature" if fichier_est_temperature else "precipitation", id_single,
            "Classeur multi-stations: il ne peut pas etre associe comme fichier individuel."
        )
        return [], [row]

    if fichier_est_temperature and id_single.get("type_temperature") == "minmax":
        row = _ligne_rejet_station(
            single, "temperature", id_single,
            "Fichier contenant uniquement des temperatures MIN/MAX; Thornthwaite exige la temperature moyenne."
        )
        return [], [row]

    if fichier_est_temperature:
        if id_single.get("multi_station"):
            return [], [_ligne_rejet_station(single, "temperature", id_single,
                "Classeur multi-stations: il ne peut pas etre associe comme fichier individuel.")]
        ids_others = {other: ident for other, ident in ids_others.items()
                      if not ident.get("multi_station")}
    else:
        ids_others = {other: ident for other, ident in ids_others.items()
                      if ident.get("type_temperature") != "minmax" and not ident.get("multi_station")}

    matches = [other for other, ident in ids_others.items()
               if _identites_correspondent(id_single, ident)]
    if len(matches) == 1:
        other = matches[0]
        id_other = ids_others[other]
        t, p = (single, other) if fichier_est_temperature else (other, single)
        id_t, id_p = (id_single, id_other) if fichier_est_temperature else (id_other, id_single)
        _enregistrer_trace_association("contenu", t, p, id_t, id_p)
        return [(_identite_sortie_station(t, p, id_t, id_p), t, p)], []

    err = (
        "Aucun fichier correspondant au code/nom station (sous logo puis 10 premieres lignes)"
        if not matches else
        "Association ambigue: plusieurs fichiers correspondent: " + ", ".join(x.name for x in matches)
    )
    row = _ligne_rejet_station(single, "temperature" if fichier_est_temperature else "precipitation", id_single, err)
    return [], [row]



def planifier_diffusion_fichier_unique(
    fichiers_temp: List[Path], fichiers_pr: List[Path]
) -> Tuple[List[Tuple[str, Path, Path]], List[Dict[str, str]]]:
    """Mode NON demande dans la boite de dialogue: diffuser le fichier unique sur tout le dossier."""
    if len(fichiers_temp) == 1 and len(fichiers_pr) >= 1:
        ft = Path(fichiers_temp[0])
        return [(_nom_station_depuis_fichier(Path(fp)), ft, Path(fp)) for fp in fichiers_pr], []
    if len(fichiers_pr) == 1 and len(fichiers_temp) >= 1:
        fp = Path(fichiers_pr[0])
        return [(_nom_station_depuis_fichier(Path(ft)), Path(ft), fp) for ft in fichiers_temp], []
    raise ValueError("Le mode diffusion exige exactement un fichier d'un cote et un ou plusieurs fichiers de l'autre.")
def associer_fichiers_temperature_precipitation_intelligent(
    fichiers_temp: List[Path], fichiers_pr: List[Path]
) -> List[Tuple[str, Path, Path]]:
    """Manual one-to-one selection, accepting a shared code OR a shared name."""
    if not fichiers_temp or not fichiers_pr:
        raise ValueError("Fichiers temperature et precipitation requis.")

    if len(fichiers_temp) == len(fichiers_pr) == 1:
        t, p = Path(fichiers_temp[0]), Path(fichiers_pr[0])
        id_t = extraire_identifiants_station(t, inclure_contenu=True)
        id_p = extraire_identifiants_station(p, inclure_contenu=True)
        if id_t.get("multi_station") or id_p.get("multi_station"):
            raise ValueError(
                "Un des fichiers contient plusieurs stations; veuillez choisir des fichiers individuels."
            )
        if id_t.get("type_temperature") == "minmax":
            raise ValueError(
                "Le fichier temperature contient uniquement des valeurs MIN/MAX; "
                "le bilan Thornthwaite exige la temperature moyenne."
            )
        if _identites_correspondent(id_t, id_p):
            return [(_identite_sortie_station(t, p, id_t, id_p), t, p)]

        has_t = bool(id_t.get("codes") or id_t.get("names"))
        has_p = bool(id_p.get("codes") or id_p.get("names"))
        if has_t and has_p:
            raise ValueError(
                "Aucun code OU nom de station commun entre les deux fichiers: "
                f"T=({_resume_identite_station(id_t)}), "
                f"P=({_resume_identite_station(id_p)})"
            )
        # Preserve explicit manual pairing when at least one file contains no
        # station identifier at all.
        return [(nom_station_sortie(t, p), t, p)]

    pairs, rejected = planifier_associations(fichiers_temp, fichiers_pr)
    if rejected:
        reasons = "; ".join(
            f"{Path(row['temperature'] or row['precipitation']).name}: {row['erreur']}"
            for row in rejected
        )
        label = "Nombre de fichiers different" if len(fichiers_temp) != len(fichiers_pr) else "Association incomplete"
        raise ValueError(label + ": " + reasons)
    return pairs


def chercher_annee_autour_bloc(df: pd.DataFrame, ligne_mois: int) -> Optional[int]:
    """
    Cherche une année autour d'un bloc jours x mois.
    """
    for r in range(max(0, ligne_mois - 8), min(df.shape[0], ligne_mois + 3)):
        for c in range(min(df.shape[1], 10)):
            if est_annee(df.iloc[r, c]):
                valeur_annee = convertir_nombre(df.iloc[r, c])
                if valeur_annee is not None and float(valeur_annee).is_integer():
                    return int(valeur_annee)
    return None
def extraire_annuel_date_valeur(df: pd.DataFrame, type_donnee: str) -> Dict[int, Dict[str, float]]:
    """Données longues date-valeur :
    - Temperature : moyenne mensuelle par année
    - Precipitation : somme mensuelle par année"""
    col_date = detecter_colonne_date(df)
    if col_date is None:
        return {}

    cols_valeurs = detecter_colonnes_valeurs_date(df, col_date, type_donnee)
    if not cols_valeurs:
        return {}

    dates = convertir_colonne_dates(df.iloc[:, col_date])
    lignes = []

    for i in range(len(df)):
        d = dates.iloc[i]
        if pd.isna(d) or d.year < 1800 or d.year > 2100:
            continue

        vals = []
        for col in cols_valeurs:
            val = convertir_nombre_climatique(df.iloc[i, col], type_donnee)
            if val is not None:
                vals.append(val)

        if not vals:
            continue

        lignes.append({
            "annee": int(d.year),
            "mois": int(d.month),
            "valeur": moyenne_liste(vals)
        })

    if not lignes:
        return {}

    table = pd.DataFrame(lignes)

    if type_donnee == "precipitation":
        mensuel = table.groupby(["annee", "mois"])["valeur"].sum().reset_index()
    else:
        mensuel = table.groupby(["annee", "mois"])["valeur"].mean().reset_index()

    resultat = {}

    for _, row in mensuel.iterrows():
        mois = mois_nom_depuis_num(int(row["mois"]))
        annee = int(row["annee"])
        resultat.setdefault(annee, {})[mois] = float(row["valeur"])

    return resultat
def extraire_annuel_table_mensuelle_annees(df: pd.DataFrame, type_donnee: str) -> Dict[int, Dict[str, float]]:
    """
    Tableau historique :
    annee | sept | oct | ... | aout
    """
    resultat = {}

    for i in range(df.shape[0]):
        colonnes_mois = identifier_colonnes_mois(df.iloc[i, :].tolist())
        if len(colonnes_mois) < 6:
            continue
        if type_donnee == "temperature" and _bloc_temperature_est_extreme(df, i):
            continue

        col_annee = None
        for col in range(min(df.shape[1], 6)):
            if colonne_ressemble_aux_annees_dans_bloc(df, col, i + 1, min(df.shape[0], i + 300)):
                col_annee = col
                break

        if col_annee is None:
            continue

        lignes_vides = 0

        for r in range(i + 1, min(df.shape[0], i + 600)):
            ligne = df.iloc[r, :].tolist()

            if all(pd.isna(v) or str(v).strip() == "" for v in ligne):
                lignes_vides += 1
                if lignes_vides >= 3:
                    break
                continue

            lignes_vides = 0

            if not est_annee(df.iloc[r, col_annee]):
                # Si nouvelle entête de mois après bloc, arrêter.
                if r > i + 1 and len(identifier_colonnes_mois(ligne)) >= 6:
                    break
                continue

            valeur_annee = convertir_nombre(df.iloc[r, col_annee])
            if valeur_annee is None or not float(valeur_annee).is_integer():
                continue
            annee = int(valeur_annee)
            if not (1800 <= annee <= 2100):
                continue
            ligne_res = {}

            # Eviter MIN / MAX pour temperature si le tableau contient des lignes libellées.
            if type_donnee == "temperature":
                txt = normaliser_texte(" ".join(str(v) for v in ligne if pd.notna(v)))
                if any(m in txt for m in ["min", "minimum", "max", "maximum"]):
                    continue

            for mois, col_mois in colonnes_mois.items():
                val = convertir_nombre_climatique(df.iloc[r, col_mois], type_donnee)
                if val is not None:
                    ligne_res[mois] = val

            if ligne_res:
                resultat.setdefault(annee, {}).update(ligne_res)

    return resultat
def extraire_annuel_jours_mois(df: pd.DataFrame, type_donnee: str) -> Dict[int, Dict[str, float]]:
    """
    Blocs journaliers : une année autour du bloc + jours en lignes + mois en colonnes.
    """
    resultat = {}

    for i in range(df.shape[0]):
        colonnes_mois = identifier_colonnes_mois(df.iloc[i, :].tolist())
        if len(colonnes_mois) < 3:
            continue
        if type_donnee == "temperature" and _bloc_temperature_est_extreme(df, i):
            continue

        col_jour = detecter_colonne_jours(df, i)
        if col_jour is None:
            continue

        if colonne_ressemble_aux_annees_dans_bloc(df, col_jour, i + 1, min(df.shape[0], i + 120)):
            continue

        annee = chercher_annee_autour_bloc(df, i)
        if annee is None:
            continue

        lignes_jours = []
        for r in range(i + 1, min(df.shape[0], i + 45)):
            if est_jour(df.iloc[r, col_jour]):
                lignes_jours.append(r)
            elif lignes_jours:
                break

        if not lignes_jours:
            continue

        for mois, col_mois in colonnes_mois.items():
            vals = []
            for r in lignes_jours:
                val = convertir_nombre_climatique(df.iloc[r, col_mois], type_donnee)
                if val is not None:
                    vals.append(val)

            if not vals:
                continue

            if type_donnee == "precipitation":
                valeur = sum(vals)
            else:
                valeur = sum(vals) / len(vals)

            resultat.setdefault(annee, {})[mois] = float(valeur)

    return resultat
_DETAIL_ANNUEL_CACHE = {}
_MENSUEL_CACHE = {}
def _cache_key_fichier(fichier: Path, type_donnee: str):
    """Cache key that is invalidated when a source workbook changes."""
    path = Path(fichier)
    try:
        stat = path.stat()
        return (
            str(path.resolve()),
            int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1000000000))),
            int(stat.st_size),
            type_donnee,
        )
    except Exception:
        return (str(path), None, None, type_donnee)
def fusionner_series_annuelles(series_liste: List[Dict[int, Dict[str, float]]]) -> Dict[int, Dict[str, float]]:
    """Fusionne des extractions annuelles."""
    resultat = {}

    for series in series_liste:
        for annee, mois_dict in series.items():
            resultat.setdefault(int(annee), {})
            for mois, valeur in mois_dict.items():
                if mois not in resultat[int(annee)]:
                    resultat[int(annee)][mois] = float(valeur)

    return resultat
def extraire_series_annuelles_depuis_fichier(fichier: Path, type_donnee: str) -> Dict[int, Dict[str, float]]:
    """Extraction annuelle si les années existent dans le fichier."""
    cle = _cache_key_fichier(fichier, type_donnee)

    if cle in _DETAIL_ANNUEL_CACHE:
        return _DETAIL_ANNUEL_CACHE[cle]

    feuilles = lire_fichier_en_feuilles(fichier)

    resultats_date = []
    resultats_mensuel_annees = []
    resultats_jours_mois = []

    for _, df in feuilles.items():
        if df is None or df.empty:
            continue

        try:
            r = extraire_annuel_date_valeur(df, type_donnee)
            if r:
                resultats_date.append(r)
        except Exception:
            pass

        try:
            r = extraire_annuel_table_mensuelle_annees(df, type_donnee)
            if r:
                resultats_mensuel_annees.append(r)
        except Exception:
            pass

        try:
            r = extraire_annuel_jours_mois(df, type_donnee)
            if r:
                resultats_jours_mois.append(r)
        except Exception:
            pass

    candidats = [
        fusionner_series_annuelles(resultats_date),
        fusionner_series_annuelles(resultats_mensuel_annees),
        fusionner_series_annuelles(resultats_jours_mois),
    ]

    def score_series(series):
        if not series:
            return 0
        mois_attendus = set(ordre_mois_hydro_noms())
        score = 0
        for _, mois_dict in series.items():
            score += len(set(mois_dict.keys()) & mois_attendus)
        return score

    meilleur = {}
    meilleur_score = 0
    for cand in candidats:
        s = score_series(cand)
        if s > meilleur_score:
            meilleur = cand
            meilleur_score = s

    _DETAIL_ANNUEL_CACHE[cle] = meilleur
    return meilleur
def moyenne_mensuelle_depuis_series_annuelles(series: Dict[int, Dict[str, float]], annees: Optional[Sequence[int]] = None) -> Dict[str, float]:
    """
    Calcule les moyennes mensuelles à partir d'un détail annuel.
    """
    mois_attendus = ordre_mois_hydro_noms()
    valeurs = {m: [] for m in mois_attendus}

    annees_utilisees = sorted(annees) if annees is not None else sorted(series.keys())

    for annee in annees_utilisees:
        mois_dict = series.get(int(annee), {})
        for mois in mois_attendus:
            if mois in mois_dict and mois_dict[mois] is not None:
                val = convertir_nombre(mois_dict[mois])
                if val is not None:
                    valeurs[mois].append(val)

    final = {}
    for mois in mois_attendus:
        moy = moyenne_liste(valeurs[mois])
        if moy is not None:
            final[mois] = moy

    return final
def extraire_mensuel_cache(fichier: Path, type_donnee: str) -> Dict[str, float]:
    """
    Cache pour éviter de relire le même fichier Temperature si on l'applique à plusieurs stations.
    """
    cle = _cache_key_fichier(fichier, type_donnee)

    if cle not in _MENSUEL_CACHE:
        _MENSUEL_CACHE[cle] = extraire_mensuel_depuis_fichier(fichier, type_donnee)

    return _MENSUEL_CACHE[cle]
def extraire_tp_pr_avec_annees_communes(fichier_temp: Path, fichier_pr: Path) -> Tuple[Dict[str, float], Dict[str, float], List[int]]:
    """Synchronise les periodes comme dans la version historique entrainee."""
    series_temp = extraire_series_annuelles_depuis_fichier(fichier_temp, "temperature")
    series_pr = extraire_series_annuelles_depuis_fichier(fichier_pr, "precipitation")
    mois_attendus = set(ordre_mois_hydro_noms())

    if series_temp and series_pr:
        commun = sorted(set(series_temp.keys()) & set(series_pr.keys()))
        if commun:
            tp = moyenne_mensuelle_depuis_series_annuelles(series_temp, commun)
            pr = moyenne_mensuelle_depuis_series_annuelles(series_pr, commun)
            if mois_attendus.issubset(tp) and mois_attendus.issubset(pr):
                return tp, pr, commun
            print("Annees communes detectees mais blocs mensuels incomplets; retour aux moyennes mensuelles disponibles.")

    # Une source peut etre annuelle et l'autre deja agregee. L'ancienne version
    # utilisait alors les moyennes mensuelles disponibles : on conserve ce choix.
    tp = extraire_mensuel_cache(fichier_temp, "temperature")
    pr = extraire_mensuel_cache(fichier_pr, "precipitation")
    manquants_t = sorted(mois_attendus - set(tp))
    manquants_p = sorted(mois_attendus - set(pr))
    if manquants_t or manquants_p:
        raise ValueError(
            f"Donnees mensuelles incompletes (temperature={manquants_t}; precipitation={manquants_p})."
        )
    return tp, pr, []
def chemin_sortie_unique(folder: Path, stem: str, utilises=None) -> Path:
    """Never reuse an existing filename (also handles case insensitive Windows)."""
    utilises = utilises if utilises is not None else set()
    stem = re.sub(r"[^\w-]+", "_", stem, flags=re.UNICODE).strip("_.") or "station"
    existing = {p.name.casefold() for p in folder.iterdir()} if folder.is_dir() else set()
    for n in range(1, 100000):
        suffix = "" if n == 1 else f"_{n}"
        candidate = folder / f"{stem}{suffix}.xlsx"
        if candidate.name.casefold() not in utilises and candidate.name.casefold() not in existing and not candidate.exists():
            utilises.add(candidate.name.casefold())
            return candidate
    raise FileExistsError("Impossible de reserver un nom de fichier unique")
def annulation_demandee(chemin: str) -> bool:
    return _shared_cancel_requested(chemin)
