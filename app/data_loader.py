"""Excel parsing: turns uploaded workbooks into clean PO / Invoice / GRN records."""
from __future__ import annotations

import datetime as dt
import math
import re
from typing import Any

import pandas as pd

from .doc_config import COLUMN_ALIASES, DOC_TYPES

ALLOWED_EXTENSIONS = {".xlsx", ".xlsm"}


def _norm(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


# Lookup of every known canonical column, keyed by normalised name
_CANONICAL: dict[str, str] = {}
for _cfg in DOC_TYPES.values():
    for _f in _cfg["header_fields"] + _cfg["line_fields"]:
        _CANONICAL[_norm(_f)] = _f
for _alias, _canon in COLUMN_ALIASES.items():
    _CANONICAL[_norm(_alias)] = _canon


def canonical_column(name: Any) -> str:
    raw = str(name).strip()
    return _CANONICAL.get(_norm(raw), raw.replace(" ", "_"))


def clean_value(v: Any) -> Any:
    """Convert pandas/numpy values to plain JSON-safe Python values."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, (pd.Timestamp, dt.datetime)):
        if pd.isna(v):
            return None
        if v.hour == v.minute == v.second == 0:
            return v.strftime("%Y-%m-%d")
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, dt.date):
        return v.isoformat()
    if hasattr(v, "item"):  # numpy scalar
        v = v.item()
        if isinstance(v, float) and math.isnan(v):
            return None
    if isinstance(v, str):
        v = v.strip()
        return v or None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def classify_sheet(sheet_name: str, columns: list[str]) -> tuple[str, str] | None:
    """Return (doc_type, 'header'|'detail') or None if the sheet is not recognised."""
    cols = set(columns)
    n = _norm(sheet_name)
    # Invoice sheets may also carry GRN_No / PO_No as references, so the sheet name decides first
    # (when it names a type whose key column is present), then the most specific key column.
    by_name = next((doc for doc, words in (("invoice", ("invoice", "inv")), ("grn", ("grn", "goodsreceived")),
                                           ("po", ("purchaseorder", "po")))
                    if any(n.startswith(w) for w in words) and DOC_TYPES[doc]["key"] in cols), None)
    if by_name:
        doc = by_name
    elif "Invoice_No" in cols:
        doc = "invoice"
    elif "GRN_No" in cols:
        doc = "grn"
    elif "PO_No" in cols:
        doc = "po"
    else:
        return None

    if any(w in n for w in ("detail", "line", "item")):
        part = "detail"
    elif "header" in n or "head" in n:
        part = "header"
    else:
        part = "detail" if ({"Line_No", "Item_Code", "PO_Line_No"} & cols) else "header"
    return doc, part


def parse_workbook(path: str) -> tuple[dict, list[str]]:
    """
    Parse every sheet of a workbook.

    Returns ({(doc, part): {"records": [...], "columns": [...], "sheet": name}}, warnings)
    """
    found: dict = {}
    warnings: list[str] = []
    sheets = pd.read_excel(path, sheet_name=None, dtype=object)
    for sheet_name, df in sheets.items():
        df = df.dropna(how="all")
        df = df.loc[:, [c for c in df.columns if not str(c).startswith("Unnamed")]]
        if df.empty:
            continue
        df.columns = [canonical_column(c) for c in df.columns]
        kind = classify_sheet(sheet_name, list(df.columns))
        if kind is None:
            warnings.append(f"Sheet '{sheet_name}' skipped: no PO_No / Invoice_No / GRN_No column found.")
            continue
        records = []
        for row in df.to_dict(orient="records"):
            rec = {k: clean_value(v) for k, v in row.items()}
            key_field = DOC_TYPES[kind[0]]["key"]
            if rec.get(key_field) is None:
                continue
            for k in ("PO_No", "Invoice_No", "GRN_No", "Supplier_Code", "Contract_No"):
                if rec.get(k) is not None:
                    rec[k] = _key_str(rec[k])
            records.append(rec)
        if kind in found:
            found[kind]["records"].extend(records)
        else:
            found[kind] = {"records": records, "columns": list(df.columns), "sheet": sheet_name}
    return found, warnings


def _key_str(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def merge_into_dataset(dataset: dict, parsed: dict, filename: str) -> list[str]:
    """Merge parsed sheets into the user's dataset. Re-uploading a doc type replaces it."""
    messages = []
    for (doc, part), payload in parsed.items():
        entry = dataset.setdefault(doc, {"header": [], "detail": [], "columns": {}, "sources": {}})
        entry[part] = payload["records"]
        entry["columns"][part] = payload["columns"]
        entry["sources"][part] = f"{filename} › {payload['sheet']}"
        entry["loaded_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        messages.append(
            f"{DOC_TYPES[doc]['label']} {part}: {len(payload['records'])} rows loaded from sheet '{payload['sheet']}'."
        )
    return messages


def dataset_summary(dataset: dict) -> list[dict]:
    out = []
    for doc, cfg in DOC_TYPES.items():
        entry = dataset.get(doc) or {}
        out.append({
            "doc": doc,
            "label": cfg["label"],
            "headers": len(entry.get("header", [])),
            "lines": len(entry.get("detail", [])),
            "sources": entry.get("sources", {}),
            "loaded_at": entry.get("loaded_at"),
        })
    return out


# --------------------------------------------------------------------------- lookups

def list_documents(dataset: dict, doc: str) -> list[dict]:
    cfg = DOC_TYPES[doc]
    entry = dataset.get(doc) or {}
    out = []
    for h in entry.get("header", []):
        key = h.get(cfg["key"])
        out.append({
            "id": key,
            "po_no": h.get("PO_No"),
            "date": h.get(cfg["date"]),
            "supplier": h.get("Supplier_Name") or h.get("Supplier_Code"),
        })
    return out


def get_document(dataset: dict, doc: str, doc_id: str) -> dict | None:
    cfg = DOC_TYPES[doc]
    entry = dataset.get(doc) or {}
    header = next((h for h in entry.get("header", []) if h.get(cfg["key"]) == doc_id), None)
    lines = [d for d in entry.get("detail", []) if d.get(cfg["key"]) == doc_id]
    if header is None and not lines:
        return None
    if header is None:
        header = {cfg["key"]: doc_id}
    lines.sort(key=lambda r: (_sortable(r.get("Line_No"))))
    return {"type": doc, "id": doc_id, "header": header, "lines": lines}


def _sortable(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("inf")


def ordered_columns(dataset: dict, doc: str, part: str) -> list[str]:
    """Configured field order first, then any extra columns present in the upload."""
    cfg = DOC_TYPES[doc]
    preferred = cfg["header_fields"] if part == "header" else cfg["line_fields"]
    present = ((dataset.get(doc) or {}).get("columns") or {}).get(part)
    if not present:
        return list(preferred)
    cols = [c for c in preferred if c in present]
    skip = {cfg["key"]} if part == "detail" else set()
    cols += [c for c in present if c not in cols and c not in skip]
    return cols
