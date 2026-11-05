# -*- coding: utf-8 -*-
"""
Traitement automatique du bilan hydrique Thornthwaite
-----------------------------------------------------
Objectif : calculer le bilan hydrique Thornthwaite directement en Python,
sans dependance a un modele Excel externe. Le classeur de sortie est construit
par le code Python avec la meme structure de calcul et les memes series de
graphiques que le modele de reference.

Regles de calcul :
    Tp :
      - donnees journalieres  -> moyenne mensuelle
      - donnees mensuelles    -> moyenne par mois
      - tableau MIN/MAX/MOY   -> utiliser seulement MOY
      - plusieurs lignes/colonnes MOY -> moyenne des MOY

    Pr :
      - donnees journalieres  -> somme mensuelle par annee puis moyenne mensuelle
      - donnees mensuelles    -> moyenne par mois
      - tableaux avec Totaux / PJ max -> utiliser Totaux pour Pr, jamais PJ max

Les calculs sont faits en Python avec la precision complete du modele de
reference. Les valeurs affichees gardent les memes arrondis, mais les courbes
utilisent les valeurs numeriques completes. Excel n'est plus necessaire pour
recalculer les resultats.
"""

import re
import warnings
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Sequence

warnings.filterwarnings("ignore")

from modules.shared import EXTENSIONS_TABLEAUX, MOIS_HYDRO, installer_paquet

# ============================================================
# Parametres generaux
# ============================================================





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

# ============================================================
# Installation automatique des dependances
# ============================================================

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
    # xlrd pour .xls, odfpy pour .ods, pyxlsb pour .xlsb, pywin32 pour conversion .xls du modele.


# Importer apres verification basique.
verifier_dependances()
from datetime import datetime
import pandas as pd  # noqa: E402

# ============================================================
# Nettoyage et reconnaissance
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
    cancel_requested as _shared_cancel_requested,
)


# ---------------------------------------------------------------------------
# Shared helpers: same implementation and interface as the Gumbel support.
# The options below preserve the original Bilan-specific parsing rules.
# ---------------------------------------------------------------------------
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
    """
    Reconnait uniquement les noms de mois textuels.

    Correction importante : dans les versions precedentes, les nombres 1..12
    etaient reconnus comme mois dans tous les contextes. Dans les tableaux
    mensuels historiques, cela confondait des valeurs de pluie comme 2, 5, 8,
    12 avec des entetes de mois et coupait la serie trop tot.
    """
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
    """
    Reconnait un mois numerique seulement pour les vraies lignes d'entete
    numeriques, par exemple 1..12 ou 9..8. Ne doit pas etre utilise sur une
    cellule de donnees ordinaire.
    """
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
    """
    Evite de confondre une colonne Jour 1..31 avec une colonne Date.
    Ce probleme provoquait une fausse extraction uniquement sur Janvier.
    """
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

        # Les vraies dates sont souvent textuelles avec / ou -, ou des dates Excel seriels > 20000.
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

    # Sinon prendre la meilleure colonne seulement pour eviter de melanger des colonnes non climatiques.
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
    """
    Identifie les colonnes mensuelles.

    Correction importante pour les donnees de precipitation mensuelles :
    une ligne de valeurs peut contenir 2, 5, 8 ou 12 mm. Ces nombres ne sont
    pas des mois. On accepte les mois numeriques seulement si la ligne ressemble
    vraiment a une ligne d'entete numerique avec au moins 6 mois.
    """
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
    """
    Detecte une vraie colonne de jours 1..31.

    Correction : une colonne de valeurs pluviometriques peut contenir beaucoup
    de nombres entre 1 et 31. On exige donc une sequence de jours suffisamment
    complete et proche de 1..31, pas seulement quelques valeurs dans cet intervalle.
    """
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
    """Detect a year column inside the current monthly block only.

    Some ONM workbooks contain three consecutive tables (mean, minimum,
    maximum temperature). The previous scan continued through all three
    blocks, counted their headers and summary rows as non-year values, and
    rejected the valid first block. It also needs to tolerate year cells such
    as ``| 1996 |`` and summary labels such as ``|Moyen|``.
    """
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
    """
    Extrait les moyennes mensuelles depuis un tableau historique classique :
    annee | sept | oct | nov | ... | aout | annuel

    Pour les precipitations, c'est le cas du fichier 150503.xlsx :
    les valeurs sont deja des totaux mensuels par annee. Il faut donc calculer
    la moyenne de chaque mois sur toutes les annees, et non prendre une seule ligne.
    """
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

        # Parcourir les lignes apres l'entete des mois jusqu'a un nouveau bloc ou grande zone vide.
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

            # Si la ligne n'a presque pas de valeurs mensuelles, ignorer implicitement par absence d'ajout.
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

    # Priorite stricte : on accepte directement une methode seulement si elle couvre les 12 mois.
    for source, res in methodes:
        if not res:
            continue
        final = fusionner_resultats(res)
        if all(m in final for m in mois_attendus):
            print(f"  {type_donnee}: extraction depuis {source}")
            return final

    # Secours intelligent : si une methode donne seulement quelques mois a cause d'un format bizarre,
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
def extraire_codes_station_depuis_texte(texte: str) -> List[str]:
    """Return all plausible station codes from a filename or labelled text."""
    if texte is None:
        return []
    candidats = re.findall(r"(?<!\d)(\d{3,8})(?!\d)", str(texte))
    propres = set()
    for code in candidats:
        if len(code) == 4 and 1800 <= int(code) <= 2200:
            continue
        propres.add(code)
    return sorted(
        propres,
        key=lambda code: (
            0 if len(code) == 6 else 1 if len(code) == 3 else 2,
            abs(len(code) - 6),
            len(code),
            code,
        ),
    )



def nettoyer_nom_station(texte: str) -> str:
    """Normalize station names, ignoring case, accents, punctuation and file labels."""
    texte = normaliser_texte(texte)
    texte = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", texte)
    texte = re.sub(r"(?<!\d)\d{3,8}(?!\d)", " ", texte)
    mots_generiques = (set(MOTS_STATION_A_SUPPRIMER) - {"t", "p"}) | {
        "converted", "converti", "convertie", "conversion", "fichier", "file",
        "mensuel", "mensuelle", "journalier", "journaliere", "monthly", "daily",
        "moyenne", "moyennes", "maximum", "maximale", "maximales",
        "minimum", "minimale", "minimales", "periode", "period", "altitude",
        "latitude", "longitude", "annee", "year", "station",
        "input", "output", "source", "result", "resultat", "test", "new",
        "old", "copy", "copie", "final", "version", "original", "bedjaia",
        "rain", "rainfall", "precip", "pluie", "pluviometrie", "hydro", "meteo",
        "max", "min", "moy", "moyen", "tmax", "tmin",
        "statistique", "statistiques", "statistics",
    }
    mots = [mot for mot in texte.split() if mot not in mots_generiques]
    cle = re.sub(r"[^a-z0-9]+", "", "".join(mots)).strip()

    # Alias observes dans les fichiers hydroclimatiques fournis.
    alias = {
        "bbarreridj": "bordjbouarreridj",
        "bordjbouarreridj": "bordjbouarreridj",
    }
    return alias.get(cle, cle)






def _extraire_nom_station_depuis_ligne(texte: str) -> str:
    """Extract a station name, including when code and name share one cell."""
    if not texte:
        return ""
    texte = str(texte)

    # Prefer the explicit name label even if a code label appears earlier
    # in the same cell/row: "Code station: 150101 Nom station: BOUIRA".
    motifs = (
        r"nom\s*(?:de\s*)?(?:station|poste)\s*[:=]\s*(.+)",
        r"(?<!code )station\s*[:=]\s*(.+)",
        r"(?<!code )poste\s*[:=]\s*(.+)",
    )
    match = None
    for motif in motifs:
        match = re.search(motif, texte, flags=re.I)
        if match:
            break
    if not match:
        return ""

    nom = match.group(1).strip()
    nom = re.split(
        r"(?:nom\s*(?:de\s*)?(?:station|poste)|periode|p[eé]riode|altitude|latitude|longitude|annee|ann[eé]e|year|code)\s*[:=]",
        nom,
        maxsplit=1,
        flags=re.I,
    )[0]
    nom = nom.strip(" |;,-_")
    if re.fullmatch(r"\d{3,8}", nom.replace(" ", "")):
        return ""
    cle = nettoyer_nom_station(nom)
    if len(cle) < 3 or not re.search(r"[a-z]", cle):
        return ""
    if normaliser_texte(nom) in {
        "station", "nom station", "poste", "nom poste", "code station",
        "annee", "mois", "periode", "altitude", "latitude", "longitude",
    }:
        return ""
    return nom



_STATION_REFERENCE_CACHE = None


def _charger_reference_stations() -> List[Dict[str, object]]:
    """Load the optional station cross-reference learned from the Soummam dataset."""
    global _STATION_REFERENCE_CACHE
    if _STATION_REFERENCE_CACHE is not None:
        return _STATION_REFERENCE_CACHE
    chemin = Path(__file__).resolve().parent.parent / "station_reference.json"
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
        _STATION_REFERENCE_CACHE = list(donnees.get("stations") or [])
    except Exception:
        _STATION_REFERENCE_CACHE = []
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
    """Return True for workbooks whose sheets are station-group datasets.

    These workbooks must not be treated as one station during one-to-one
    folder pairing. Examples include Station_Code/Station_Name tables,
    code_fichier/station tables, and sheets whose columns are many station
    names against Hydrological_Year.
    """
    reference = _charger_reference_stations()
    reference_names = {
        str(key) for station in reference
        for key in (station.get("name_keys") or [])
    }
    code_headers = {
        "stationcode", "codestation", "codefichier", "stationid", "idstation",
        "codeposte", "postcode",
    }
    name_headers = {
        "stationname", "nomstation", "station", "nomposte", "poste",
    }
    year_headers = {"hydrologicalyear", "anneehydrologique", "annee", "year"}

    for df in feuilles:
        if df is None or df.empty:
            continue
        zone = df.iloc[: min(500, df.shape[0]), : min(80, df.shape[1])]
        # Structured station records: one row per station (or station-year).
        for r in range(min(20, zone.shape[0])):
            vals = ["" if pd.isna(v) else str(v).strip() for v in zone.iloc[r, :].tolist()]
            compact = [normaliser_compact(v) for v in vals]
            code_cols = [i for i, v in enumerate(compact) if v in code_headers]
            name_cols = [i for i, v in enumerate(compact) if v in name_headers]
            if code_cols or name_cols:
                codes = set()
                names = set()
                for rr in range(r + 1, zone.shape[0]):
                    row = zone.iloc[rr, :].tolist()
                    for c in code_cols:
                        if c < len(row):
                            codes.update(extraire_codes_station_depuis_texte(row[c]))
                    for c in name_cols:
                        if c < len(row) and pd.notna(row[c]):
                            key = nettoyer_nom_station(row[c])
                            # Only known station aliases count here. A label
                            # cell such as "Nom station:" is not itself a
                            # station name and must not turn a single-station
                            # workbook into a false multi-station workbook.
                            if key and key in reference_names:
                                names.add(key)
                if len(codes) > 1 or len(names) > 1:
                    return True

        # Wide layout: one column per station and one row per hydrological year.
        for r in range(min(10, zone.shape[0])):
            vals = ["" if pd.isna(v) else str(v).strip() for v in zone.iloc[r, :].tolist()]
            compact = [normaliser_compact(v) for v in vals]
            has_year = any(v in year_headers for v in compact)
            known_names = {nettoyer_nom_station(v) for v in vals if v}
            known_names.intersection_update(reference_names)
            if has_year and len(known_names) >= 2:
                return True
    return False


def extraire_identifiants_station_depuis_contenu(fichier: Path) -> Dict[str, object]:
    """Search station names/codes throughout worksheet headers and legacy text files.

    Supports identifiers in one cell, split over adjacent cells, or placed on
    the next row. All worksheet tabs are checked, not only the first three.
    """
    fichier = Path(fichier)
    ident = {
        "code": "", "nom": "", "cle_nom": "", "codes": [], "noms": [],
        "conflit": "", "type_temperature": "inconnu",
        "codes_explicites": [], "noms_explicites": [], "multi_station": False,
    }

    try:
        extension = fichier.suffix.lower()
        if extension in {".xlsx", ".xls", ".xlsm", ".xlsb", ".ods"}:
            with pd.ExcelFile(fichier) as excel:
                feuilles = [
                    pd.read_excel(excel, sheet_name=nom, header=None, nrows=500)
                    for nom in excel.sheet_names
                ]
        elif extension in {".csv", ".txt", ".tsv"} or not extension:
            df = _lire_fichier_texte_uncached(fichier)
            feuilles = [df.head(500)]
        else:
            return ident
    except Exception as exc:
        ident["conflit"] = f"Lecture d'identite impossible: {exc}"
        return ident

    if _est_classeur_multi_stations(feuilles):
        ident["multi_station"] = True
        ident["conflit"] = "Classeur multi-stations: plusieurs stations sont organisees dans le meme tableau."
        return ident

    # Strong station metadata (Code station / Nom station) takes priority
    # over generic repeated labels such as "Station : SETIF..." in copied
    # temperature template blocks. This prevents stale template labels from
    # overriding the explicit station ID at the top of the file.
    identifiants_forts_presents = False
    for df in feuilles:
        if df is None or df.empty:
            continue
        for row in df.iloc[:20, :40].itertuples(index=False, name=None):
            texte_ligne = normaliser_texte(" ".join(str(v) for v in row if pd.notna(v)))
            compact_ligne = normaliser_compact("".join(str(v) for v in row if pd.notna(v)))
            if (re.search(r"\b(?:code|nom)\s*(?:de\s*)?(?:station|poste)\b", texte_ligne)
                    or any(token in compact_ligne for token in ("codestation", "nomstation", "codeposte", "nomposte"))):
                identifiants_forts_presents = True
                break
        if identifiants_forts_presents:
            break

    codes = set()
    codes_explicites = set()
    noms = {}
    noms_explicites = {}
    types_temperature = set()
    reference = _charger_reference_stations()
    reference_par_nom = {
        str(name_key): station
        for station in reference
        for name_key in (station.get("name_keys") or [])
    }
    reference_par_code = {
        str(code): station
        for station in reference
        for code in (station.get("codes") or [])
    }

    def ajouter_reference(station, nom_observe=None, code_observe=None):
        for code in station.get("codes") or []:
            codes.add(str(code))
        canonical_name = str(station.get("canonical_name") or "")
        canonical_key = nettoyer_nom_station(canonical_name)
        if canonical_key:
            noms[canonical_key] = canonical_name
        if nom_observe and canonical_key:
            noms_explicites[canonical_key] = canonical_name
        if code_observe:
            codes_explicites.add(str(code_observe))

    def ajouter_code(texte):
        for code in extraire_codes_station_depuis_texte(texte):
            codes.add(code)
            codes_explicites.add(code)

    def candidat_nom(texte):
        if texte is None:
            return ""
        candidat = str(texte).strip().strip("|;,:=-_ ")
        if not candidat:
            return ""
        norm = normaliser_texte(candidat)
        if not norm or reconnaitre_mois(candidat):
            return ""
        if re.fullmatch(r"[\d\s.,/\\:_-]+", candidat):
            return ""
        if any(norm.startswith(x) for x in (
            "periode ", "period ", "altitude ", "latitude ", "longitude ",
            "annee ", "year ", "code ", "mois ", "janv ", "fevr ",
        )):
            return ""
        cle = nettoyer_nom_station(candidat)
        if len(cle) < 3 or not re.search(r"[a-z]", cle):
            return ""
        if cle in {"annee", "mois", "periode", "altitude", "latitude", "longitude"}:
            return ""
        return candidat

    for df in feuilles:
        if df is None or df.empty:
            continue
        zone = df.iloc[:500, :60]
        grille = []
        for row in zone.itertuples(index=False, name=None):
            grille.append(["" if pd.isna(v) else str(v).strip() for v in row])

        for r, values in enumerate(grille):
            non_empty = [v for v in values if v]
            if not non_empty:
                continue

            # Reference-assisted scan: station names/codes can appear as plain
            # cell values without a "Station:" label. Check exact cells and
            # short adjacent-cell windows only (never fuzzy substring matches).
            for c, item in enumerate(values):
                if not item:
                    continue
                if not identifiants_forts_presents:
                    key_cell = nettoyer_nom_station(item)
                    if key_cell in reference_par_nom:
                        ajouter_reference(reference_par_nom[key_cell], nom_observe=key_cell)
                code_cell = re.fullmatch(r"\s*(\d{3,8})(?:\.0+)?\s*", item)
                # A bare numeric value can be a rainfall measurement. Treat it
                # as a station code only in the metadata area; farther down the
                # sheet, require an explicit "Code station" label.
                if (code_cell and code_cell.group(1) in reference_par_code
                        and r <= 12 and c <= 5):
                    ajouter_reference(reference_par_code[code_cell.group(1)], code_observe=code_cell.group(1))
                for largeur in (2, 3, 4):
                    fenetre = [x for x in values[c:c + largeur] if x]
                    if len(fenetre) < 2:
                        continue
                    key_fenetre = nettoyer_nom_station(" ".join(fenetre))
                    if key_fenetre in reference_par_nom:
                        ajouter_reference(reference_par_nom[key_fenetre], nom_observe=key_fenetre)

            row_text = " ".join(non_empty)
            row_norm = normaliser_texte(row_text)
            row_compact = normaliser_compact("".join(non_empty))

            # Determine whether the source describes average air temperature
            # or averages of minimum/maximum temperatures. The latter are not
            # valid substitutes for mean temperature in Thornthwaite.
            if "temp" in row_norm or "temperature" in row_norm or "temperatures" in row_norm:
                if ligne_est_min_ou_max_temperature(non_empty):
                    types_temperature.add("minmax")
                elif any(token in row_norm for token in ("moy", "mean", "average")):
                    types_temperature.add("moyenne")

            # First pass: explicit code/name labels may be fragmented over cells.
            for texte_ligne in (row_text, "".join(non_empty)):
                norm = normaliser_texte(texte_ligne)
                compact = normaliser_compact(texte_ligne)

                code_patterns = (
                    r"\bcode\s*(?:de\s*)?(?:station|poste)\s+(\d{3,8})\b",
                    r"\b(?:station|poste)\s*(?:code|n|no|num|numero)\s+(\d{3,8})\b",
                    r"\b(?:station|poste)\s+(\d{3,8})\b",
                )
                for pattern in code_patterns:
                    for match in re.finditer(pattern, norm, flags=re.I):
                        ajouter_code(match.group(1))

                compact_code_patterns = (
                    r"(?:codestation|codeposte|stationcode|postcode)(\d{3,8})",
                    r"(?:station|poste)(\d{3,8})",
                )
                for pattern in compact_code_patterns:
                    for match in re.finditer(pattern, compact, flags=re.I):
                        ajouter_code(match.group(1))

                nom = _extraire_nom_station_depuis_ligne(texte_ligne)
                # If a top-level explicit Nom station / Code station exists,
                # later generic "Station:" labels belong to repeated template
                # blocks and are not authoritative station identifiers.
                nom_label_explicite = re.search(
                    r"\bnom\s*(?:de\s*)?(?:station|poste)\b", norm, flags=re.I
                ) is not None
                if nom and (not identifiants_forts_presents or nom_label_explicite):
                    cle = nettoyer_nom_station(nom)
                    if cle:
                        noms[cle] = nom
                        noms_explicites[cle] = nom

            # Second pass: label and value can occupy separate cells/rows.
            for c, item in enumerate(values):
                if not item:
                    continue
                label = normaliser_texte(item)
                compact_label = normaliser_compact(item)
                is_code_label = (
                    ("code" in label and ("station" in label or "poste" in label))
                    or label.strip() in {"code", "codes", "station code", "code station", "code poste", "station id", "id station"}
                    or re.search(r"\b(station|poste)\s*(n|no|num|numero)\b", label) is not None
                    or compact_label in {"codestation", "codeposte", "stationcode", "postcode"}
                )
                is_name_label = (
                    (("nom" in label) and ("station" in label or "poste" in label))
                    or label.strip() in {"station", "stations", "poste", "nom", "nom station", "nom poste",
                                         "station name", "name station", "site name"}
                    or "designation station" in label
                ) and not is_code_label
                if identifiants_forts_presents and is_name_label and not (
                    "nom" in label and ("station" in label or "poste" in label)
                ):
                    is_name_label = False

                if not (is_code_label or is_name_label):
                    continue

                has_delimiter = re.search(r"[:=]", item) is not None
                rhs = re.split(r"[:=]", item, maxsplit=1)[-1] if has_delimiter else ""
                candidats = [rhs] if rhs.strip() else []
                cellules_droite = values[c + 1:c + 7]
                cellules_bas = []
                for rr in range(r + 1, min(len(grille), r + 4)):
                    cellules_bas.extend(grille[rr][max(0, c - 1):min(len(grille[rr]), c + 4)])
                # Column headings such as "Stations" or "Nom station" have
                # their value below in the same column. A labelled cell with
                # ":" often stores its value immediately to the right.
                if is_name_label and not has_delimiter:
                    candidats.extend(grille[rr][c] for rr in range(r + 1, min(len(grille), r + 4)))
                    candidats.extend(cellules_droite)
                    candidats.extend(cellules_bas)
                else:
                    candidats.extend(cellules_droite)
                    candidats.extend(cellules_bas)

                if is_code_label:
                    for candidat in candidats:
                        codes_found = extraire_codes_station_depuis_texte(candidat)
                        if codes_found:
                            ajouter_code(candidat)
                            break

                if is_name_label:
                    nom_direct = _extraire_nom_station_depuis_ligne(item)
                    if nom_direct:
                        nom = nom_direct
                    else:
                        nom = next((candidate for x in candidats if (candidate := candidat_nom(x))), "")
                    if nom:
                        cle = nettoyer_nom_station(nom)
                        if cle:
                            noms[cle] = nom
                            noms_explicites[cle] = nom

    ident["codes"] = sorted(codes)
    ident["noms"] = sorted(noms)
    ident["codes_explicites"] = sorted(codes_explicites)
    ident["noms_explicites"] = sorted(noms_explicites)
    ident["multi_station"] = False
    if len(codes_explicites) > 1:
        ident["conflit"] = "Plusieurs codes station explicites dans le contenu : " + ", ".join(sorted(codes_explicites))
    if len(noms) > 1:
        ident["conflit"] = (ident["conflit"] + "; " if ident["conflit"] else "") + \
            "Plusieurs noms station explicites dans le contenu : " + ", ".join(sorted(noms))
    if codes:
        ident["code"] = sorted(codes, key=lambda x: (len(x) != 6, len(x)))[0]
    if noms:
        cle = sorted(noms)[0]
        ident["cle_nom"] = cle
        ident["nom"] = noms[cle]
    if "moyenne" in types_temperature:
        ident["type_temperature"] = "moyenne"
    elif "minmax" in types_temperature:
        ident["type_temperature"] = "minmax"
    return ident











_STATION_META_CACHE = {}


def extraire_identifiants_station(
    fichier: Path, inclure_contenu: bool = True
) -> Dict[str, object]:
    """Return filename identifiers first, optionally enriched with file-content identifiers."""
    fichier = Path(fichier)
    cache_type = "station_metadata_v5_content" if inclure_contenu else "station_metadata_v5_filename"
    key = _cache_key_fichier(fichier, cache_type)
    if key in _STATION_META_CACHE:
        return _STATION_META_CACHE[key]

    codes_fichier = set(extraire_codes_station_depuis_texte(fichier.stem))
    nom_fichier = nettoyer_nom_station(fichier.stem)
    if not re.search(r"[a-z]", nom_fichier):
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
        {"code": "", "nom": "", "cle_nom": "", "codes": [], "noms": [], "conflit": ""}
    )
    if codes_fichier or noms_fichier:
        # Stage 1 (filename) is authoritative when it contains a station ID.
        # Content is still read for temperature-type validation, but it cannot
        # replace or add unrelated template station labels.
        codes = set(codes_fichier)
        noms = set(noms_fichier)
    else:
        codes = set(content.get("codes") or [])
        if content.get("code"):
            codes.add(str(content["code"]))
        noms = set(content.get("noms") or [])
        if content.get("cle_nom"):
            noms.add(str(content["cle_nom"]))

    codes, noms = _enrichir_identifiants_avec_reference(codes, noms)
    preferred_codes = sorted(
        codes, key=lambda c: (len(str(c)) != 6, len(str(c)), str(c))
    )
    preferred_names = sorted(noms)
    identity = {
        "code": str(content.get("code") or (preferred_codes[0] if preferred_codes else "")),
        "codes": preferred_codes,
        "cle_nom": str(content.get("cle_nom") or (preferred_names[0] if preferred_names else "")),
        "names": preferred_names,
        "nom_source": fichier.stem,
        "nom_interne": str(content.get("nom") or ""),
        "conflit": str(content.get("conflit") or ""),
        "multi_station": bool(content.get("multi_station", False)) if not (codes_fichier or noms_fichier) else False,
        "source_fichier": sorted(codes_fichier | noms_fichier),
        "source_contenu": sorted(
            set(content.get("codes_explicites") or []) |
            set(content.get("noms_explicites") or [])
        ),
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
def _identites_correspondent(a: Dict[str, object], b: Dict[str, object]) -> bool:
    """A station matches when at least one normalized code OR one normalized name is shared."""
    codes_a = set(map(str, a.get("codes") or ([a.get("code")] if a.get("code") else [])))
    codes_b = set(map(str, b.get("codes") or ([b.get("code")] if b.get("code") else [])))
    if codes_a.intersection(codes_b):
        return True

    names_a = set(a.get("names") or [])
    names_b = set(b.get("names") or [])
    if not names_a and a.get("cle_nom"):
        names_a.add(str(a["cle_nom"]))
    if not names_b and b.get("cle_nom"):
        names_b.add(str(b["cle_nom"]))
    return bool(names_a.intersection(names_b))

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
    codes_pr = list(id_pr.get("codes") or [])
    codes_temp = list(id_temp.get("codes") or [])
    codes = codes_pr or codes_temp
    if codes:
        codes = sorted(codes, key=lambda c: (len(str(c)) != 6, len(str(c)), str(c)))
        return str(codes[0])
    noms = list(id_pr.get("names") or []) or list(id_temp.get("names") or [])
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
    etape_finale = etape
    source_t = set(id_t.get("source_fichier") or []) | set(id_t.get("source_contenu") or [])
    source_p = set(id_p.get("source_fichier") or []) | set(id_p.get("source_contenu") or [])
    if etape == "nom_fichier" and not source_t.intersection(source_p) and communs:
        etape_finale = "nom_fichier_reference"
    elif etape == "contenu" and not source_t.intersection(source_p) and communs:
        etape_finale = "contenu_reference"

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
        "etape_correspondance": "nom_fichier puis contenu",
        "type_temperature_source": str(identite.get("type_temperature") or "inconnu"),
    }




def planifier_associations(
    fichiers_temp: List[Path], fichiers_pr: List[Path]
) -> Tuple[List[Tuple[str, Path, Path]], List[Dict[str, str]]]:
    """Two-stage station pairing: filename first, then file contents for unresolved files.

    A pair is accepted when one normalized station code OR one normalized
    station name is shared. Case, accents and punctuation do not matter.
    """
    temperatures = sorted(set(map(Path, fichiers_temp)), key=lambda p: str(p).casefold())
    precipitations = sorted(set(map(Path, fichiers_pr)), key=lambda p: str(p).casefold())
    _ASSOCIATION_TRACE.clear()

    # Stage 1: inspect filenames only. Content is deliberately not opened when
    # an unambiguous filename match already exists.
    identites_nom = {
        f: extraire_identifiants_station(f, inclure_contenu=False)
        for f in temperatures + precipitations
    }
    pairs_1, t_restants, p_restants, cand_t_1, cand_p_1 = _resoudre_paires_uniques(
        temperatures, precipitations, identites_nom
    )

    paires_finales = []
    for t, p in pairs_1:
        id_t, id_p = identites_nom[t], identites_nom[p]
        station = _identite_sortie_station(t, p, id_t, id_p)
        paires_finales.append((station, t, p))
        _enregistrer_trace_association("nom_fichier", t, p, id_t, id_p)

    # Stage 2: only unresolved files are opened. Keep filename IDs and enrich
    # them with every explicit code/name found inside the workbook/text file.
    identites_completes = dict(identites_nom)
    for f in t_restants + p_restants:
        identites_completes[f] = extraire_identifiants_station(f, inclure_contenu=True)

    temperatures_admissibles = []
    precipitations_admissibles = []
    rejets = []
    for t in t_restants:
        if identites_completes[t].get("multi_station"):
            rejets.append(_ligne_rejet_station(
                t, "temperature", identites_completes[t],
                "Classeur multi-stations: il contient plusieurs stations et ne peut pas etre associe comme fichier individuel."
            ))
        elif identites_completes[t].get("type_temperature") == "minmax":
            rejets.append(_ligne_rejet_station(
                t, "temperature", identites_completes[t],
                "Fichier contenant uniquement des temperatures MIN/MAX; "
                "Thornthwaite exige la temperature moyenne."
            ))
        else:
            temperatures_admissibles.append(t)

    for p in p_restants:
        if identites_completes[p].get("multi_station"):
            rejets.append(_ligne_rejet_station(
                p, "precipitation", identites_completes[p],
                "Classeur multi-stations: il contient plusieurs stations; les fichiers individuels sont prioritaires pour la synchronisation."
            ))
        else:
            precipitations_admissibles.append(p)

    pairs_2, t_restants_2, p_restants_2, cand_t_2, cand_p_2 = _resoudre_paires_uniques(
        temperatures_admissibles, precipitations_admissibles, identites_completes
    )
    for t, p in pairs_2:
        id_t, id_p = identites_completes[t], identites_completes[p]
        station = _identite_sortie_station(t, p, id_t, id_p)
        paires_finales.append((station, t, p))
        _enregistrer_trace_association("contenu", t, p, id_t, id_p)

    for t in t_restants_2:
        options = cand_t_2.get(t, [])
        if len(options) > 1:
            raison = "Association ambigue: " + ", ".join(p.name for p in options)
        elif options:
            raison = "Precipitation deja associee a plusieurs fichiers temperature"
        else:
            raison = "Aucun code ou nom de station commun (nom du fichier puis contenu)"
        ident = identites_completes[t]
        if ident.get("conflit"):
            raison += " | " + str(ident["conflit"])
        rejets.append(_ligne_rejet_station(t, "temperature", ident, raison))

    for p in p_restants_2:
        options = cand_p_2.get(p, [])
        if len(options) > 1:
            raison = "Association ambigue: " + ", ".join(t.name for t in options)
        elif options:
            raison = "Temperature deja associee a plusieurs fichiers precipitation"
        else:
            raison = "Aucun code ou nom de station commun (nom du fichier puis contenu)"
        ident = identites_completes[p]
        if ident.get("conflit"):
            raison += " | " + str(ident["conflit"])
        rejets.append(_ligne_rejet_station(p, "precipitation", ident, raison))

    # Prevent two output workbooks with the same station name.
    compteur = Counter(station.casefold() for station, _, _ in paires_finales)
    paires_valides = []
    for station, t, p in paires_finales:
        if compteur[station.casefold()] > 1:
            rejets.append(_ligne_rejet_station(
                t, "temperature", identites_completes[t],
                f"Plusieurs paires generent le meme nom de station {station}"
            ))
            rejets.append(_ligne_rejet_station(
                p, "precipitation", identites_completes[p],
                f"Plusieurs paires generent le meme nom de station {station}"
            ))
        else:
            paires_valides.append((station, t, p))

    return paires_valides, rejets


def planifier_fichier_avec_dossier(
    fichiers_temp: List[Path], fichiers_pr: List[Path], fichier_est_temperature: bool
) -> Tuple[List[Tuple[str, Path, Path]], List[Dict[str, str]]]:
    """Match a selected single file against a folder: filename first, content second."""
    single = Path(fichiers_temp[0] if fichier_est_temperature else fichiers_pr[0])
    others = [Path(x) for x in (fichiers_pr if fichier_est_temperature else fichiers_temp)]

    id_single_name = extraire_identifiants_station(single, inclure_contenu=False)
    ids_others_name = {
        other: extraire_identifiants_station(other, inclure_contenu=False)
        for other in others
    }
    matches = [
        other for other, ident in ids_others_name.items()
        if _identites_correspondent(id_single_name, ident)
    ]

    if len(matches) == 1:
        other = matches[0]
        id_other = ids_others_name[other]
        t, p = (single, other) if fichier_est_temperature else (other, single)
        id_t, id_p = (id_single_name, id_other) if fichier_est_temperature else (id_other, id_single_name)
        _enregistrer_trace_association("nom_fichier", t, p, id_t, id_p)
        return [(_identite_sortie_station(t, p, id_t, id_p), t, p)], []

    # No unique filename match: scan the selected file and all possible files
    # in the opposite folder for internal station metadata.
    id_single_full = extraire_identifiants_station(single, inclure_contenu=True)
    ids_others_full = {
        other: extraire_identifiants_station(other, inclure_contenu=True)
        for other in others
    }
    if fichier_est_temperature and id_single_full.get("type_temperature") == "minmax":
        row = _ligne_rejet_station(
            single, "temperature", id_single_full,
            "Fichier contenant uniquement des temperatures MIN/MAX; "
            "Thornthwaite exige la temperature moyenne."
        )
        return [], [row]

    if not fichier_est_temperature:
        ids_others_full = {
            other: ident for other, ident in ids_others_full.items()
            if ident.get("type_temperature") != "minmax"
        }
    matches = [
        other for other, ident in ids_others_full.items()
        if _identites_correspondent(id_single_full, ident)
    ]
    if len(matches) == 1:
        other = matches[0]
        id_other = ids_others_full[other]
        t, p = (single, other) if fichier_est_temperature else (other, single)
        id_t, id_p = (id_single_full, id_other) if fichier_est_temperature else (id_other, id_single_full)
        _enregistrer_trace_association("contenu", t, p, id_t, id_p)
        return [(_identite_sortie_station(t, p, id_t, id_p), t, p)], []

    err = (
        "Aucun fichier correspondant au code/nom station dans le dossier"
        if not matches else
        "Association ambigue: plusieurs fichiers correspondent: " + ", ".join(x.name for x in matches)
    )
    ident = id_single_full
    row = _ligne_rejet_station(single, "temperature" if fichier_est_temperature else "precipitation", ident, err)
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
        id_t = extraire_identifiants_station(t, inclure_contenu=False)
        id_p = extraire_identifiants_station(p, inclure_contenu=False)
        if _identites_correspondent(id_t, id_p):
            return [(_identite_sortie_station(t, p, id_t, id_p), t, p)]

        id_t = extraire_identifiants_station(t, inclure_contenu=True)
        id_p = extraire_identifiants_station(p, inclure_contenu=True)
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
    """
    Données longues date-valeur :
    - Temperature : moyenne mensuelle par année
    - Precipitation : somme mensuelle par année
    """
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
    """
    Fusionne des extractions annuelles. Priorité au premier résultat complet,
    mais complète les mois manquants avec les suivants.
    """
    resultat = {}

    for series in series_liste:
        for annee, mois_dict in series.items():
            resultat.setdefault(int(annee), {})
            for mois, valeur in mois_dict.items():
                if mois not in resultat[int(annee)]:
                    resultat[int(annee)][mois] = float(valeur)

    return resultat
def extraire_series_annuelles_depuis_fichier(fichier: Path, type_donnee: str) -> Dict[int, Dict[str, float]]:
    """
    Extraction annuelle si les années existent dans le fichier.
    Si le fichier ne contient qu'un tableau de moyennes mensuelles, retourne {}.
    """
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
    """Synchronise les periodes comme dans la version historique entrainee.

    - si les DEUX sources offrent un detail annuel exploitable : moyenne sur les
      annees communes ;
    - si une source est deja une normale/moyenne mensuelle (ou si aucun bloc
      annuel commun complet n'est exploitable) : utiliser les moyennes mensuelles
      de chaque source, sans bloquer le traitement.

    Ce comportement est indispensable au mode fichier unique + dossier utilise
    dans les jeux de donnees hydroclimatiques heterogenes.
    """
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
