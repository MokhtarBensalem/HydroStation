# -*- coding: utf-8 -*-
"""Helpers shared by the Bilan Hydrique and Gumbel processing engines."""
from __future__ import annotations

import copy
import math
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Iterable, Optional

# Identical input formats and hydrological month order shared by both engines.
EXTENSIONS_TABLEAUX = [".xlsx", ".xls", ".xlsm", ".xlsb", ".ods", ".csv", ".txt", ".tsv"]
MOIS_HYDRO = [
    ("sept", 9), ("oct", 10), ("nov", 11), ("dec", 12),
    ("janv", 1), ("fev", 2), ("mars", 3), ("avril", 4),
    ("mai", 5), ("juin", 6), ("juil", 7), ("aout", 8),
]


# A small LRU cache for source files read several times during one batch.
# The key includes size and mtime_ns, so a changed file is read again.
_FILE_READ_CACHE = OrderedDict()
_FILE_READ_CACHE_LOCK = threading.RLock()
_FILE_READ_CACHE_MAX = 12


def clean_path(value) -> str:
    """Normalize a path string in the same way in both processing engines."""
    return str(value).strip().strip('"').strip("'")


def normalize_text(value, *, none_as_empty: bool = False, remove_braces: bool = False) -> str:
    """Normalize accents, case and separators; options preserve domain rules."""
    if value is None and none_as_empty:
        text = ""
    else:
        text = str(value).strip().lower()
    import unicodedata
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    separators = ["\n", "\t", "_", "-", ".", ":", ";", "/", "\\", "(", ")", "[", "]"]
    if remove_braces:
        separators.extend(["{", "}"])
    for char in separators:
        text = text.replace(char, " ")
    return " ".join(text.split())


def compact_text(value, normalizer: Callable) -> str:
    return normalizer(value).replace(" ", "")


def parse_number(
    value,
    *,
    missing_values: Iterable = (),
    allow_negative: bool = True,
    strip_outer_pipes: bool = False,
    comma_mode: str = "decimal",
    reject_bool: bool = False,
) -> Optional[float]:
    """Shared numeric parser with explicit options for each hydrology method."""
    if value is None:
        return None
    try:
        import pandas as pd
        if pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, (int, float)) and not (reject_bool and isinstance(value, bool)):
        number = float(value)
    else:
        text = str(value).strip().replace("\u00a0", " ")
        if not text:
            return None
        if strip_outer_pipes:
            text = text.strip("|").strip()
        text = text.replace(" ", "")
        if comma_mode == "decimal":
            text = text.replace(",", ".")
        elif comma_mode == "gumbel":
            if text.count(",") == 1 and text.count(".") == 0:
                text = text.replace(",", ".")
            elif text.count(",") > 0 and text.count(".") > 0:
                text = text.replace(",", "")
        else:
            raise ValueError("Unknown comma_mode: " + str(comma_mode))
        try:
            number = float(text)
        except Exception:
            return None

    if not math.isfinite(number):
        return None
    try:
        if number in missing_values:
            return None
    except Exception:
        pass
    if not allow_negative and number < 0:
        return None
    return number


def integer_in_range(value, converter: Callable, minimum: int, maximum: int) -> bool:
    number = converter(value)
    return number is not None and float(number).is_integer() and minimum <= int(number) <= maximum


def create_unique_directory(parent, base_name: str, *, create_parent: bool = True) -> Path:
    """Create a numbered output directory, optionally creating its parent."""
    parent = Path(parent)
    if create_parent:
        parent.mkdir(parents=True, exist_ok=True)
    target = parent / base_name
    if not target.exists():
        target.mkdir(parents=True, exist_ok=True)
        return target
    index = 2
    while True:
        target = parent / f"{base_name} {index}"
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
            return target
        index += 1


def list_directory_files(
    directory,
    extensions,
    *,
    excluded_tokens=(),
    normalizer: Optional[Callable] = None,
    skip_lock_files: bool = False,
    skip_empty: bool = True,
):
    """List accepted files in one directory with configurable domain filters."""
    directory = Path(directory)
    allowed = {str(ext).lower() for ext in extensions}
    excluded_tokens = tuple(excluded_tokens or ())
    files = []
    for file_path in directory.iterdir():
        if not file_path.is_file() or file_path.suffix.lower() not in allowed:
            continue
        if skip_lock_files and file_path.name.startswith("~$"):
            continue
        try:
            if skip_empty and file_path.stat().st_size <= 0:
                continue
        except OSError:
            continue
        if excluded_tokens and normalizer is not None:
            normalized_name = normalizer(file_path.stem)
            if any(token in normalized_name for token in excluded_tokens):
                continue
        files.append(file_path)
    return sorted(set(files), key=lambda path: path.name.lower())


def dispatch_file_sheets(file_path, excel_reader: Callable, text_reader: Callable):
    """Common extension dispatcher; parsing rules remain with each engine."""
    extension = Path(file_path).suffix.lower()
    if extension in {".xlsx", ".xls", ".xlsm", ".xlsb", ".ods"}:
        return excel_reader(file_path)
    if extension in {".csv", ".txt", ".tsv"}:
        return {"table": text_reader(file_path)}
    raise Exception("Format non supporte : " + extension)


def filter_excel_sheets(tables, compact_normalizer: Callable, *, keep_original: bool = False):
    """Apply the common empty/auxiliary-sheet filter to Excel sheet mappings."""
    tokens = (
        "manquante", "missing", "xlstat", "analyse", "analysis",
        "statistique", "statistic", "graphique", "chart", "pivot",
    )
    result = {}
    for name, table in tables.items():
        if any(token in compact_normalizer(name) for token in tokens) or table is None:
            continue
        cleaned = table.dropna(how="all").dropna(axis=1, how="all")
        if not cleaned.empty:
            result[name] = table if keep_original else cleaned
    return result


def _clone_cached(value):
    """Copy cached pandas objects so a caller cannot mutate the cache."""
    module_name = type(value).__module__
    if module_name.startswith("pandas") and hasattr(value, "copy"):
        try:
            return value.copy(deep=True)
        except Exception:
            pass
    if isinstance(value, dict):
        return {key: _clone_cached(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clone_cached(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_clone_cached(item) for item in value)
    return copy.deepcopy(value)


def cached_file_read(file_path, cache_tag: str, loader: Callable, *, max_entries: int = None):
    """Cache parsed file content by absolute path, size and modification time."""
    path = Path(file_path)
    try:
        stat = path.stat()
        key = (str(path.resolve()), int(stat.st_size), int(stat.st_mtime_ns), str(cache_tag))
    except OSError:
        return loader()

    with _FILE_READ_CACHE_LOCK:
        if key in _FILE_READ_CACHE:
            _FILE_READ_CACHE.move_to_end(key)
            return _clone_cached(_FILE_READ_CACHE[key])

    loaded = loader()
    with _FILE_READ_CACHE_LOCK:
        # Remove stale versions of this same file before storing the new one.
        for old_key in list(_FILE_READ_CACHE.keys()):
            if old_key[0] == key[0] and old_key != key:
                _FILE_READ_CACHE.pop(old_key, None)
        _FILE_READ_CACHE[key] = loaded
        _FILE_READ_CACHE.move_to_end(key)
        limit = _FILE_READ_CACHE_MAX if max_entries is None else max(1, int(max_entries))
        while len(_FILE_READ_CACHE) > limit:
            _FILE_READ_CACHE.popitem(last=False)
    return _clone_cached(loaded)


def clear_file_read_cache() -> None:
    with _FILE_READ_CACHE_LOCK:
        _FILE_READ_CACHE.clear()


def cancel_requested(cancel_file) -> bool:
    return bool(cancel_file) and Path(cancel_file).is_file()


def emit_hydro_marker(name: str, value=None) -> None:
    """Emit the same machine-readable progress protocol for both workflows."""
    if value is None:
        print(f"HYDRO_{name}", flush=True)
    else:
        print(f"HYDRO_{name}:{value}", flush=True)


def emit_station_report(fields, rows) -> None:
    """Send report data to the UI without creating a CSV file on disk."""
    import json

    emit_hydro_marker("REPORT_FIELDS", json.dumps(fields, ensure_ascii=False))
    for row in rows:
        emit_hydro_marker("REPORT_ROW", json.dumps(row, ensure_ascii=False, default=str))
    emit_hydro_marker("REPORT_READY")


def installer_paquet(package_name: str) -> bool:
    print(
        "Dependance manquante: %s. Lancez install_requirements.bat puis relancez le programme."
        % package_name
    )
    return False



def _preferred_station_code(codes):
    values = sorted({str(value).strip() for value in (codes or []) if str(value).strip()})
    if not values:
        return ""
    return sorted(values, key=lambda value: (len(value) != 6, len(value), value))[0]


def resolve_station_metadata(identities, fallback_name: str = ""):
    """Resolve a readable station name/code from one or more identity dictionaries."""
    identities = [item for item in (identities or []) if isinstance(item, dict)]
    if any(bool(item.get("multi_station")) for item in identities):
        return {
            "station_name": "Plusieurs stations (fichier agrégé)",
            "station_code": "Multiple",
        }
    code_sets = []
    name_sets = []
    observed_names = []
    for identity in identities:
        codes = set(str(v).strip() for v in (identity.get("codes") or []) if str(v).strip())
        if identity.get("code"):
            codes.add(str(identity["code"]).strip())
        names = set(str(v).strip() for v in (identity.get("names") or []) if str(v).strip())
        if identity.get("cle_nom"):
            names.add(str(identity["cle_nom"]).strip())
        if codes:
            code_sets.append(codes)
        if names:
            name_sets.append(names)
        for key in ("nom_interne", "nom"):
            value = str(identity.get(key) or "").strip()
            if value:
                observed_names.append(value)

    all_codes = set().union(*code_sets) if code_sets else set()
    all_names = set().union(*name_sets) if name_sets else set()
    common_codes = set.intersection(*code_sets) if len(code_sets) >= 2 else set()
    common_names = set.intersection(*name_sets) if len(name_sets) >= 2 else set()
    preferred_codes = common_codes or all_codes
    preferred_names = common_names or all_names

    # station_reference.json is stored next to this shared module.
    reference = []
    try:
        import json
        reference_path = Path(__file__).resolve().parent / "station_reference.json"
        reference = json.loads(reference_path.read_text(encoding="utf-8")).get("stations", [])
    except Exception:
        reference = []

    station_name = ""
    station_code = _preferred_station_code(preferred_codes)
    for station in reference:
        ref_codes = {str(v).strip() for v in (station.get("codes") or [])}
        ref_names = {str(v).strip() for v in (station.get("name_keys") or [])}
        if preferred_codes.intersection(ref_codes) or preferred_names.intersection(ref_names):
            station_name = str(station.get("canonical_name") or "").strip()
            # Preserve the shared/observed code if possible; otherwise use the
            # preferred hydro code from the station reference.
            station_code = _preferred_station_code(preferred_codes.intersection(ref_codes) or ref_codes)
            break

    if not station_name:
        station_name = next((name for name in observed_names if name), "")
    if not station_name and preferred_names:
        station_name = sorted(preferred_names)[0]
    if not station_name:
        station_name = str(fallback_name or "").strip()
    if not station_name:
        station_name = "Non identifié"
    if not station_code:
        station_code = "Non identifié"
    return {"station_name": station_name, "station_code": station_code}


def format_years_used(years_used=None) -> str:
    """Format actual years as compact consecutive ranges."""
    years = set()
    if years_used is None:
        values = ()
    elif isinstance(years_used, (str, int, float)):
        values = (years_used,)
    else:
        values = years_used
    for value in values:
        try:
            number = float(str(value).strip())
            year = int(number)
            if number == year and 1 <= year <= 9999:
                years.add(year)
        except (TypeError, ValueError, OverflowError):
            continue
    ordered = sorted(years)
    if not ordered:
        return "Non disponibles"

    ranges = []
    first = previous = ordered[0]
    for year in ordered[1:]:
        if year == previous + 1:
            previous = year
            continue
        ranges.append((first, previous))
        first = previous = year
    ranges.append((first, previous))

    return ", ".join(
        str(first) if first == last else f"{first}–{last}"
        for first, last in ranges
    )


def add_station_metadata_sheet(
    workbook,
    *,
    station_name: str = "",
    station_code: str = "",
    years_used=None,
    worksheet=None,
    worksheet_name: str = "",
):
    """Write the three requested station fields in the calculation worksheet."""
    from openpyxl.styles import Font, Alignment

    if "Informations" in workbook.sheetnames:
        del workbook["Informations"]

    if worksheet is not None:
        ws = worksheet
    elif worksheet_name and worksheet_name in workbook.sheetnames:
        ws = workbook[worksheet_name]
    elif "01_Ajustement_Gumbel" in workbook.sheetnames:
        ws = workbook["01_Ajustement_Gumbel"]
    else:
        ws = workbook.active

    rows = [
        ("Nom de la station", station_name or "Non identifié"),
        ("Code de la station", station_code or "Non identifié"),
        ("Années utilisées", format_years_used(years_used)),
    ]
    for row_index, (label, value) in enumerate(rows, start=1):
        left = ws.cell(row_index, 1, label)
        left.font = Font(name="Times New Roman", size=10, bold=True)
        left.alignment = Alignment(vertical="center")

        right = ws.cell(row_index, 2, value)
        right.font = Font(name="Times New Roman", size=10)
        right.alignment = Alignment(vertical="center")

    ws.column_dimensions["A"].width = max(ws.column_dimensions["A"].width or 0, 18)
    ws.column_dimensions["B"].width = max(ws.column_dimensions["B"].width or 0, 24)
    return ws
