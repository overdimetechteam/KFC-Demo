"""
Three-way matching engine: Purchase Order  <->  Goods Received Note(s)  <->  Supplier Invoice(s).

One PO can be delivered on several GRNs and billed on several invoices. The engine compares the PO
against *all* selected invoices and GRNs together:
    - line quantities and values are summed across every selected invoice / GRN per PO line,
    - PO-level totals compare the cumulative invoiced and received values against the PO,
    - document-level checks (PO reference, dates, tax arithmetic, ...) run for each document.

Every check produces a row with a status:
    PASS  - values agree (within tolerance)
    FAIL  - a mismatch that should block payment
    WARN  - an exception worth reviewing (partial receipt, late delivery, ...)
    INFO  - context only
Overall result:
    MATCHED                  - no FAIL, no WARN
    MATCHED WITH WARNINGS    - no FAIL, at least one WARN
    MISMATCH                 - at least one FAIL
    INCOMPLETE               - no FAIL, but the PO, Invoice or GRN is missing
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .data_loader import get_document

PASS, FAIL, WARN, INFO = "PASS", "FAIL", "WARN", "INFO"
_RANK = {INFO: 0, PASS: 1, WARN: 2, FAIL: 3}

DEFAULT_SETTINGS = {
    "qty_tolerance_pct": 0.0,     # allowed % difference on quantities
    "price_tolerance_pct": 0.0,   # allowed % difference on unit prices
    "amount_tolerance": 0.01,     # absolute rounding tolerance on amounts
    "vat_rate_pct": 18.0,         # VAT % applied on (supply value - discount)
}

_NAME = {"po": "PO", "invoice": "Invoice", "grn": "GRN"}
_TOTAL_FIELD = {"po": "Total_Value", "invoice": "Total_Supply_Value", "grn": "Total_Value"}


# ------------------------------------------------------------------ helpers

def num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def text(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip().upper()


def fmt(v: Any) -> str:
    n = num(v)
    if n is None:
        return "—" if v in (None, "") else str(v)
    return f"{n:,.2f}" if not float(n).is_integer() else f"{n:,.0f}"


def worst(statuses) -> str:
    statuses = list(statuses) or [PASS]
    return max(statuses, key=lambda s: _RANK[s])


def as_id_list(v: Any) -> list[str]:
    """Normalise a document number, or a list of them, to a de-duplicated list of strings."""
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    out: list[str] = []
    for x in v:
        x = str(x).strip() if x is not None else ""
        if x and x not in out:
            out.append(x)
    return out


@dataclass
class Check:
    group: str
    check: str
    po: Any = None
    invoice: Any = None
    grn: Any = None
    status: str = PASS
    note: str = ""
    fields: list = field(default_factory=list)  # [(doc, field_name, doc_id)] to highlight in the UI

    def as_dict(self):
        return {"group": self.group, "check": self.check, "po": self.po, "invoice": self.invoice,
                "grn": self.grn, "status": self.status, "note": self.note, "fields": self.fields}


class ThreeWayMatcher:
    def __init__(self, dataset: dict, settings: dict | None = None):
        self.ds = dataset
        self.s = {**DEFAULT_SETTINGS, **{k: v for k, v in (settings or {}).items() if v is not None}}
        for k in DEFAULT_SETTINGS:
            self.s[k] = float(self.s[k])
        self.checks: list[Check] = []
        self.multi = {"po": False, "invoice": False, "grn": False}

    # ---------------------------------------------------------- tolerance
    def _close(self, a, b, pct=0.0) -> bool:
        a, b = num(a), num(b)
        if a is None or b is None:
            return a is None and b is None
        allowed = max(self.s["amount_tolerance"], abs(b) * pct / 100.0)
        return abs(a - b) <= allowed + 1e-9

    def add(self, *args, **kwargs) -> Check:
        c = Check(*args, **kwargs)
        self.checks.append(c)
        return c

    # ---------------------------------------------------------- multi-document helpers
    def _t(self, label: str, d: dict) -> str:
        """Tag a per-document check with the document number when several of that type are compared."""
        return f"{label} · {d['id']}" if self.multi[d["type"]] else label

    @staticmethod
    def _f(d: dict, fld: str) -> tuple:
        return (d["type"], fld, d["id"])

    @staticmethod
    def _joined(entries) -> Any:
        """[(doc, value)] -> the single value when all agree, else 'DOC-1: a; DOC-2: b'."""
        entries = list(entries)
        if not entries:
            return None
        if len(entries) == 1 or len({text(v) for _, v in entries}) == 1:
            return entries[0][1]
        return "; ".join(f"{d['id']}: {fmt(v)}" for d, v in entries)

    # ---------------------------------------------------------- main entry
    def run(self, po_no: str | None, invoice_nos=None, grn_nos=None) -> dict:
        inv_ids, grn_ids = as_id_list(invoice_nos), as_id_list(grn_nos)
        po = get_document(self.ds, "po", po_no) if po_no else None
        inv_found = [(i, get_document(self.ds, "invoice", i)) for i in inv_ids]
        grn_found = [(g, get_document(self.ds, "grn", g)) for g in grn_ids]

        self._presence(po_no, po, inv_found, grn_found)
        invs = [d for _, d in inv_found if d]
        grns = [d for _, d in grn_found if d]
        self.multi.update(invoice=len(invs) > 1, grn=len(grns) > 1)
        self.pair_of = self._pairing(invs, grns)

        if po:
            self._header_checks(po, invs, grns)
            self._amount_checks(po, invs, grns)
            self._history_checks(po, invs, grns)
            self._pair_checks(po, invs, grns)
        lines = self._line_checks(po, invs, grns) if po else []
        pairs = self._pairs_table(po, invs, grns) if po else []

        statuses = [c.status for c in self.checks] + [ln["status"] for ln in lines]
        fails = statuses.count(FAIL)
        warns = statuses.count(WARN)
        incomplete = not (po and invs and grns)
        if fails:
            overall = "MISMATCH"
        elif incomplete:
            overall = "INCOMPLETE"
        else:
            overall = "MATCHED WITH WARNINGS" if warns else "MATCHED"

        return {
            "po_no": po_no,
            "invoice_no": ", ".join(inv_ids) or None, "grn_no": ", ".join(grn_ids) or None,
            "invoice_nos": inv_ids, "grn_nos": grn_ids,
            "overall": overall,
            "counts": {"PASS": statuses.count(PASS), "FAIL": fails, "WARN": warns, "INFO": statuses.count(INFO)},
            "header_checks": [c.as_dict() for c in self.checks],
            "line_checks": lines,
            "pairs": pairs,
            "highlights": self._highlights(lines),
            "settings": self.s,
        }

    # ---------------------------------------------------------- presence
    def _presence(self, po_no, po, inv_found, grn_found):
        if not po_no:
            self.add("Documents", "Purchase Order selected", status=FAIL,
                     note="No Purchase Order selected - three-way match is incomplete.")
        elif po is None:
            self.add("Documents", "Purchase Order found", status=FAIL,
                     note=f"Purchase Order '{po_no}' not found in the uploaded data.")
        elif not po["lines"]:
            self.add("Documents", "Purchase Order line items", status=WARN,
                     note=f"Purchase Order '{po_no}' has no detail lines.")
        for label, found in (("Invoice", inv_found), ("GRN", grn_found)):
            if not found:
                self.add("Documents", f"{label} selected", status=WARN,
                         note=f"No {label} selected - three-way match is incomplete.")
            for wanted, got in found:
                if got is None:
                    self.add("Documents", f"{label} found", status=FAIL,
                             note=f"{label} '{wanted}' not found in the uploaded data.")
                elif not got["lines"]:
                    self.add("Documents", f"{label} line items", status=WARN,
                             note=f"{label} '{wanted}' has no detail lines.")

    # ---------------------------------------------------------- header
    def _field3(self, group, label, fpo, finv, fgrn, po, invs, grns, numeric=False):
        entries = []   # (doc, field, value)
        if fpo:
            entries.append((po, fpo, po["header"].get(fpo)))
        if finv:
            entries += [(d, finv, d["header"].get(finv)) for d in invs]
        if fgrn:
            entries += [(d, fgrn, d["header"].get(fgrn)) for d in grns]
        if len(entries) < 2:
            return
        ref = next((v for _, _, v in entries if v is not None), None)

        def same(v):
            if v is None:
                return False
            return self._close(v, ref) if numeric else text(v) == text(ref)

        ok = all(same(v) for _, _, v in entries)
        flds, missing, differ = [], [], []
        if not ok:
            for d, f, v in entries:
                if v is None:
                    missing.append(d["id"] if self.multi[d["type"]] else _NAME[d["type"]].upper())
                elif not same(v):
                    differ.append(d["id"])
                if d is po or not same(v):
                    flds.append(self._f(d, f))
        if ok:
            note = ""
        elif missing:
            note = "Missing on " + ", ".join(missing)
        else:
            note = "Values differ" + (" on " + ", ".join(differ) if len(entries) > 2 else "")
        cell = {t: self._joined((d, v) for d, _, v in entries if d["type"] == t) for t in ("po", "invoice", "grn")}
        self.add(group, label, cell["po"], cell["invoice"], cell["grn"], PASS if ok else FAIL, note, flds)

    def _header_checks(self, po, invs, grns):
        ph = po["header"]
        po_no = ph.get("PO_No")
        for d in invs + grns:
            ref = d["header"].get("PO_No")
            ok = text(ref) == text(po_no)
            name = _NAME[d["type"]]
            self.add("Reference", self._t(f"{name} quotes the PO number", d), po_no,
                     ref if d["type"] == "invoice" else None, ref if d["type"] == "grn" else None,
                     PASS if ok else FAIL, "" if ok else f"{name} refers to a different PO",
                     [] if ok else [self._f(d, "PO_No"), self._f(po, "PO_No")])

        args = (po, invs, grns)
        self._field3("Supplier", "Supplier code", "Supplier_Code", "Supplier_Code", "Supplier_Code", *args)
        self._field3("Supplier", "Supplier name", "Supplier_Name", "Supplier_Name", None, *args)
        self._field3("Supplier", "Supplier TIN", "Supplier_TIN", "Supplier_TIN", None, *args)
        self._field3("Buyer", "Buyer / purchaser", "Buyer_Name", "Purchaser_Name", None, *args)
        self._field3("Buyer", "Contract number", "Contract_No", "Contract_No", None, *args)

        # ---- dates
        po_date, po_del = ph.get("PO_Date"), ph.get("Delivery_Date")
        for d in invs:
            ih = d["header"]
            if po_date and ih.get("Invoice_Date"):
                ok = ih["Invoice_Date"] >= po_date
                self.add("Dates", self._t("Invoice date on/after PO date", d), po_date, ih["Invoice_Date"], None,
                         PASS if ok else FAIL, "" if ok else "Invoice is dated before the PO was raised",
                         [] if ok else [self._f(d, "Invoice_Date"), self._f(po, "PO_Date")])
        for d in grns:
            gh = d["header"]
            if po_date and gh.get("GRN_Date"):
                ok = gh["GRN_Date"] >= po_date
                self.add("Dates", self._t("GRN date on/after PO date", d), po_date, None, gh["GRN_Date"],
                         PASS if ok else FAIL, "" if ok else "Goods received before the PO was raised",
                         [] if ok else [self._f(d, "GRN_Date"), self._f(po, "PO_Date")])
            if po_del and gh.get("GRN_Date"):
                ok = gh["GRN_Date"] <= po_del
                self.add("Dates", self._t("Delivered by PO delivery date", d), po_del, None, gh["GRN_Date"],
                         PASS if ok else WARN, "" if ok else "Late delivery against PO delivery date",
                         [] if ok else [self._f(d, "GRN_Date"), self._f(po, "Delivery_Date")])

        grn_dates = sorted(((g["header"]["GRN_Date"], g) for g in grns if g["header"].get("GRN_Date")),
                           key=lambda x: x[0])
        for d in invs:
            ih = d["header"]
            if not grn_dates:
                break
            # the GRN this invoice bills: the one it quotes in GRN_No, else the one dated on its delivery date
            paired = self.pair_of.get(d["id"])
            if paired is not None and paired["header"].get("GRN_Date"):
                dt_ = paired["header"]["GRN_Date"]
                if ih.get("Delivery_Date"):
                    ok = ih["Delivery_Date"] == dt_
                    self.add("Dates", self._t("Invoice delivery date = its GRN date", d), None, ih["Delivery_Date"], dt_,
                             PASS if ok else WARN, "" if ok else f"Delivery date on invoice differs from the date of {paired['id']}",
                             [] if ok else [self._f(d, "Delivery_Date"), self._f(paired, "GRN_Date")])
                if ih.get("Invoice_Date"):
                    ok = ih["Invoice_Date"] >= dt_
                    self.add("Dates", self._t("Invoice raised after goods received", d), None, ih["Invoice_Date"], dt_,
                             PASS if ok else WARN, "" if ok else f"Supplier billed before {paired['id']} was received",
                             [] if ok else [self._f(d, "Invoice_Date")])
                continue
            matched = next((g for dt_, g in grn_dates if dt_ == ih.get("Delivery_Date")), None)
            if ih.get("Delivery_Date"):
                ok = matched is not None
                grn_cell = matched["header"]["GRN_Date"] if ok else self._joined((g, dt_) for dt_, g in grn_dates)
                label = "Invoice delivery date = GRN date" if not self.multi["grn"] else "Invoice delivery date matches a GRN date"
                note = "" if ok else ("Delivery date on invoice differs from the GRN date" if not self.multi["grn"]
                                      else "Delivery date on invoice matches none of the selected GRN dates")
                self.add("Dates", self._t(label, d), None, ih["Delivery_Date"], grn_cell,
                         PASS if ok else WARN, note,
                         [] if ok else [self._f(d, "Delivery_Date")] + [self._f(g, "GRN_Date") for _, g in grn_dates])
            if ih.get("Invoice_Date"):
                dt_, g = (matched["header"]["GRN_Date"], matched) if matched else grn_dates[0]
                ok = ih["Invoice_Date"] >= dt_
                self.add("Dates", self._t("Invoice raised after goods received", d), None, ih["Invoice_Date"], dt_,
                         PASS if ok else WARN,
                         "" if ok else "Supplier billed before goods were received"
                         + (f" on {g['id']}" if self.multi["grn"] else ""),
                         [] if ok else [self._f(d, "Invoice_Date")])

    # ---------------------------------------------------------- amounts
    def _amount_checks(self, po, invs, grns):
        ph = po["header"]
        tol = self.s["amount_tolerance"]

        def line_sum(doc):
            vals = [num(r.get("Line_Total")) for r in doc["lines"]]
            vals = [v for v in vals if v is not None]
            return round(sum(vals), 2) if vals else None

        # header totals vs own lines (internal consistency), per document
        for doc in [po] + invs + grns:
            d, fld = doc["type"], _TOTAL_FIELD[doc["type"]]
            if num(doc["header"].get(fld)) is None:
                continue
            s = line_sum(doc)
            if s is None:
                continue
            ok = self._close(doc["header"][fld], s)
            vals = {"po": None, "invoice": None, "grn": None}
            vals[d] = f"{fmt(doc['header'][fld])} vs lines {fmt(s)}"
            self.add("Totals", self._t(f"{_NAME[d]} header total = sum of lines", doc),
                     vals["po"], vals["invoice"], vals["grn"], PASS if ok else FAIL,
                     "" if ok else f"Header total differs from line total by {fmt(num(doc['header'][fld]) - s)}",
                     [] if ok else [self._f(doc, fld)])

        def cumulative(docs):
            vals = [num(x["header"].get(_TOTAL_FIELD[x["type"]])) for x in docs]
            return round(sum(vals), 2) if docs and None not in vals else None

        po_val = num(ph.get("Total_Value"))
        inv_val, grn_val = cumulative(invs), cumulative(grns)
        inv_flds = [self._f(x, "Total_Supply_Value") for x in invs]
        grn_flds = [self._f(x, "Total_Value") for x in grns]
        inv_lbl = f"Total invoiced ({len(invs)} invoices)" if self.multi["invoice"] else "Invoice value"
        grn_lbl = f"Total received ({len(grns)} GRNs)" if self.multi["grn"] else "GRN value"

        if po_val is not None and inv_val is not None:
            ok = self._close(inv_val, po_val)
            self.add("Totals", f"{inv_lbl} vs PO value", po_val, inv_val, None,
                     PASS if ok else FAIL,
                     "" if ok else f"Invoiced value {'exceeds' if inv_val > po_val else 'is below'} PO by {fmt(abs(inv_val - po_val))}",
                     [] if ok else inv_flds + [self._f(po, "Total_Value")])
        if po_val is not None and grn_val is not None:
            if self._close(grn_val, po_val):
                st, note = PASS, ""
            elif grn_val > po_val:
                st, note = FAIL, f"Received value exceeds PO by {fmt(grn_val - po_val)}"
            else:
                st, note = WARN, (f"Partial receipt: {fmt(po_val - grn_val)} of PO value not received on "
                                  + ("these GRNs" if self.multi["grn"] else "this GRN"))
            self.add("Totals", f"{grn_lbl} vs PO value", po_val, None, grn_val, st, note,
                     [] if st == PASS else grn_flds + [self._f(po, "Total_Value")])
        if inv_val is not None and grn_val is not None:
            if self._close(inv_val, grn_val):
                st, note = PASS, ""
            elif inv_val > grn_val:
                st, note = FAIL, f"Invoiced {fmt(inv_val - grn_val)} more than the value received"
            else:
                st, note = WARN, f"Invoiced {fmt(grn_val - inv_val)} less than the value received"
            self.add("Totals", f"{inv_lbl} vs {'total received' if self.multi['grn'] else 'GRN value'}",
                     None, inv_val, grn_val, st, note,
                     [] if st == PASS else inv_flds + grn_flds)

        # invoice tax arithmetic, per invoice
        for d in invs:
            ih = d["header"]
            supply, disc = num(ih.get("Total_Supply_Value")), num(ih.get("Discount")) or 0.0
            vat, cess, net = num(ih.get("VAT_Amount")), num(ih.get("Less_Cess")) or 0.0, num(ih.get("Net_Total"))
            if supply is not None and vat is not None:
                expected = round((supply - disc) * self.s["vat_rate_pct"] / 100.0, 2)
                ok = abs(vat - expected) <= max(tol, 0.01)
                self.add("Tax", self._t(f"VAT = {self.s['vat_rate_pct']:g}% × (supply − discount)", d), None,
                         f"{fmt(vat)} (expected {fmt(expected)})", None, PASS if ok else FAIL,
                         "" if ok else f"VAT differs from expected by {fmt(vat - expected)}",
                         [] if ok else [self._f(d, "VAT_Amount")])
            if supply is not None and net is not None and vat is not None:
                expected = round(supply - disc + vat - cess, 2)
                ok = abs(net - expected) <= max(tol, 0.01)
                self.add("Tax", self._t("Net = supply − discount + VAT − cess", d), None,
                         f"{fmt(net)} (expected {fmt(expected)})", None, PASS if ok else FAIL,
                         "" if ok else f"Net total differs from expected by {fmt(net - expected)}",
                         [] if ok else [self._f(d, "Net_Total")])

    # ---------------------------------------------------------- cross-document history
    def _history_checks(self, po, invs, grns):
        ph = po["header"]
        po_no = ph.get("PO_No")
        po_val = num(ph.get("Total_Value"))
        invoices = (self.ds.get("invoice") or {}).get("header", [])
        all_grns = (self.ds.get("grn") or {}).get("header", [])
        sel_inv = {d["id"] for d in invs}
        sel_grn = {d["id"] for d in grns}

        for d in invs:
            ih = d["header"]
            if not ih.get("Supplier_Ref_No"):
                continue
            dups = [i["Invoice_No"] for i in invoices
                    if i.get("Invoice_No") != d["id"]
                    and text(i.get("Supplier_Ref_No")) == text(ih.get("Supplier_Ref_No"))
                    and text(i.get("Supplier_Code")) == text(ih.get("Supplier_Code"))]
            self.add("Duplicates", self._t("Supplier invoice reference is unique", d), None,
                     ih.get("Supplier_Ref_No"), None, FAIL if dups else PASS,
                     ("Possible duplicate: same supplier reference on " + ", ".join(dups)) if dups else "",
                     [self._f(d, "Supplier_Ref_No")] if dups else [])

        # linked documents left out of this match make the comparison partial
        po_inv = [i for i in invoices if text(i.get("PO_No")) == text(po_no)]
        other_inv = [i for i in po_inv if i.get("Invoice_No") not in sel_inv]
        if other_inv:
            total = sum(num(i.get("Total_Supply_Value")) or 0 for i in other_inv) + \
                sum(num(d["header"].get("Total_Supply_Value")) or 0 for d in invs)
            over = total > (po_val or 0) + self.s["amount_tolerance"]
            self.add("Linked documents", "Invoices for this PO not in this match", None,
                     ", ".join(i["Invoice_No"] for i in other_inv), None, WARN,
                     f"Not compared. Cumulative invoiced incl. these: {fmt(total)} vs PO {fmt(po_val)}"
                     + (" — over-billed" if over else ""))

        po_grns = [g for g in all_grns if text(g.get("PO_No")) == text(po_no)]
        other_grn = [g for g in po_grns if g.get("GRN_No") not in sel_grn]
        if other_grn:
            total = sum(num(g.get("Total_Value")) or 0 for g in other_grn) + \
                sum(num(d["header"].get("Total_Value")) or 0 for d in grns)
            self.add("Linked documents", "GRNs for this PO not in this match", None, None,
                     ", ".join(g["GRN_No"] for g in other_grn), WARN,
                     f"Not compared. Cumulative received incl. these: {fmt(total)} vs PO {fmt(po_val)}")
        if invs or grns:
            self.add("Linked documents", "Documents compared against this PO", None,
                     ", ".join(d["id"] for d in invs) or None, ", ".join(d["id"] for d in grns) or None, INFO,
                     f"{len(invs)} of {len(po_inv)} linked invoice(s), {len(grns)} of {len(po_grns)} linked GRN(s)")

        if not grns and not po_grns:
            self.add("Receipts", "Goods receipt exists", None, None, None, FAIL,
                     f"No GRN found for {po_no} — goods not yet received")
        if not invs and not po_inv:
            self.add("Receipts", "Invoice exists", None, None, None, WARN,
                     f"No invoice found for {po_no}")
        if ph.get("Status"):
            self.add("Status", "PO status", ph.get("Status"),
                     self._joined((d, d["header"].get("Status")) for d in invs),
                     self._joined((d, d["header"].get("Status")) for d in grns), INFO, "")

    # ---------------------------------------------------------- invoice <-> GRN pairs
    # Each invoice bills exactly one GRN (Invoice_Header.GRN_No) and each GRN is billed by exactly
    # one invoice, so a PO must have as many invoices as GRNs.
    @staticmethod
    def _grn_refs(inv: dict) -> list[str]:
        return as_id_list(str(inv["header"].get("GRN_No") or "").split(","))

    @staticmethod
    def _has_grn_refs(invs) -> bool:
        return any("GRN_No" in d["header"] for d in invs)

    def _pairing(self, invs, grns) -> dict:
        """invoice id -> the selected GRN it bills, when it quotes exactly one selected GRN."""
        by_id = {g["id"]: g for g in grns}
        out = {}
        for d in invs:
            refs = self._grn_refs(d)
            if len(refs) == 1 and refs[0] in by_id:
                out[d["id"]] = by_id[refs[0]]
        return out

    def _pair_checks(self, po, invs, grns):
        if not (invs and grns):
            return
        n_i, n_g = len(invs), len(grns)
        ok = n_i == n_g
        self.add("Pairs", "Invoice count = GRN count", None, n_i, n_g, PASS if ok else FAIL,
                 "" if ok else f"{n_i} invoice{'s' if n_i != 1 else ''} vs {n_g} GRN{'s' if n_g != 1 else ''}"
                               " — every GRN must be billed by its own invoice")
        if not self._has_grn_refs(invs):
            return   # the invoice data carries no GRN reference, so pairs cannot be formed

        po_no = text(po["header"].get("PO_No"))
        selected = {g["id"] for g in grns}
        all_grns = {g.get("GRN_No"): g for g in (self.ds.get("grn") or {}).get("header", [])}
        billed: dict[str, list] = {}
        for d in invs:
            refs = self._grn_refs(d)
            for r in refs:
                billed.setdefault(r, []).append(d["id"])
            if len(refs) == 1 and refs[0] in selected:
                st, note = PASS, ""
            elif not refs:
                st, note = FAIL, "Invoice does not quote the GRN it bills"
            elif len(refs) > 1:
                st, note = FAIL, f"Quotes {len(refs)} GRNs — each invoice must bill exactly one GRN"
            elif refs[0] in all_grns and text(all_grns[refs[0]].get("PO_No")) == po_no:
                st, note = WARN, f"{refs[0]} is not included in this match"
            else:
                st, note = FAIL, f"{refs[0]} is not a GRN for this PO"
            self.add("Pairs", self._t("Invoice bills one GRN", d), None, d["id"], ", ".join(refs) or None, st, note,
                     [] if st == PASS else [self._f(d, "GRN_No")])

        for g in grns:
            by = billed.get(g["id"], [])
            if len(by) == 1:
                st, note, flds = PASS, "", []
            elif by:
                st, note = FAIL, f"Billed by {', '.join(by)} — double billing"
                flds = [("invoice", "GRN_No", x) for x in by[1:]] + [self._f(g, "GRN_No")]
            else:
                st, note, flds = WARN, "No invoice bills this GRN yet", [self._f(g, "GRN_No")]
            self.add("Pairs", self._t("GRN billed by exactly one invoice", g), None, ", ".join(by) or None, g["id"],
                     st, note, flds)

        if not (self.multi["invoice"] or self.multi["grn"]):
            return   # a single pair is already compared line by line at PO level
        P = self._index(po["lines"], ["Line_No"], "Qty")
        qt, pt = self.s["qty_tolerance_pct"], self.s["price_tolerance_pct"]
        for d in invs:
            g = self.pair_of.get(d["id"])
            if g is None:
                continue
            tag = f" · {d['id']} ↔ {g['id']}"
            iv, gv = num(d["header"].get("Total_Supply_Value")), num(g["header"].get("Total_Value"))
            if iv is not None and gv is not None:
                if self._close(iv, gv):
                    st, note = PASS, ""
                elif iv > gv:
                    st, note = FAIL, f"Invoiced {fmt(iv - gv)} more than the value received on {g['id']}"
                else:
                    st, note = WARN, f"Invoiced {fmt(gv - iv)} less than the value received on {g['id']}"
                self.add("Pairs", "Invoice value vs its GRN" + tag, None, iv, gv, st, note,
                         [] if st == PASS else [self._f(d, "Total_Supply_Value"), self._f(g, "Total_Value")])

            I = self._index(d["lines"], ["PO_Line_No", "Line_No"], "Qty")
            G = self._index(g["lines"], ["PO_Line_No", "Line_No"], "Received_Qty")
            issues = []
            for k in sorted(dict.fromkeys(list(I) + list(G)), key=lambda k: (num(k) is None, num(k) or 0, k)):
                i, r, p = I.get(k), G.get(k), P.get(k)
                if i and not p:
                    issues.append((FAIL, f"line {k} is not on the PO"))
                if i and not r:
                    issues.append((FAIL, f"line {k} invoiced {fmt(i['qty'])} but not received on {g['id']}"))
                elif r and not i:
                    issues.append((WARN, f"line {k} received {fmt(r['qty'])} but not invoiced"))
                elif not self._close(i["qty"], r["qty"], qt):
                    st = FAIL if i["qty"] > r["qty"] else WARN
                    issues.append((st, f"line {k} invoiced {fmt(i['qty'])} vs received {fmt(r['qty'])}"))
                if i and p and p["price"] is not None:
                    bad = [pr for pr in i["prices"] if not self._close(pr, p["price"], pt)]
                    if bad:
                        issues.append((FAIL, f"line {k} invoice price {fmt(bad[0])} ≠ PO price {fmt(p['price'])}"))
            st = worst(s for s, _ in issues) if issues else PASS
            note = "; ".join(m for _, m in issues)
            self.add("Pairs", "Invoice lines vs its GRN" + tag, None, d["id"], g["id"], st, note[:1].upper() + note[1:],
                     [] if st == PASS else [self._f(d, "Total_Supply_Value"), self._f(g, "Total_Value")])

    def _pairs_table(self, po, invs, grns) -> list[dict]:
        """One row per invoice and the GRN it bills (plus GRNs no invoice bills), with its own verdict."""
        if not (invs and grns and self._has_grn_refs(invs)):
            return []

        def findings(ids):
            # checks about this invoice / GRN only (no other invoice or GRN involved)
            notes, sts = [], []
            for c in self.checks:
                if c.status not in (FAIL, WARN) or not c.fields:
                    continue
                cids = {did for _, _, did in c.fields}
                if cids & ids and cids <= ids | {po["id"]}:
                    name = c.check.split(" · ")[0]
                    notes.append(f"{name}: {c.note}" if c.note else name)
                    sts.append(c.status)
            return notes, sts

        verdict = {FAIL: "MISMATCH", WARN: "MATCHED WITH WARNINGS"}
        rows, billed = [], set()
        for d in invs:
            g = self.pair_of.get(d["id"])
            ids = {d["id"]} | ({g["id"]} if g else set())
            notes, sts = findings(ids)
            st = worst(sts) if sts else PASS
            if g:
                billed.add(g["id"])
            rows.append({"invoice": d["id"], "inv_date": d["header"].get("Invoice_Date"),
                         "inv_value": num(d["header"].get("Total_Supply_Value")),
                         "grn": g["id"] if g else (", ".join(self._grn_refs(d)) or None),
                         "grn_date": g["header"].get("GRN_Date") if g else None,
                         "grn_value": num(g["header"].get("Total_Value")) if g else None,
                         "status": st, "verdict": verdict.get(st, "MATCHED"), "issues": notes})
        for g in grns:
            if g["id"] in billed:
                continue
            notes, sts = findings({g["id"]})
            rows.append({"invoice": None, "inv_date": None, "inv_value": None,
                         "grn": g["id"], "grn_date": g["header"].get("GRN_Date"),
                         "grn_value": num(g["header"].get("Total_Value")),
                         "status": worst(sts + [WARN]), "verdict": "NOT INVOICED", "issues": notes})
        return rows

    # ---------------------------------------------------------- lines
    @staticmethod
    def _index(lines: list[dict], key_fields, qty_field, doc_key=None) -> dict:
        """Aggregate lines (from one or more documents) per PO line."""
        out: dict[str, dict] = {}
        for r in lines:
            k = next((r.get(f) for f in key_fields if r.get(f) is not None), None)
            if k is None:
                k = r.get("Item_Code") or f"row{len(out) + 1}"
            n = num(k)
            k = str(int(n)) if n is not None and float(n).is_integer() else str(k)
            a = out.setdefault(k, {"item": r.get("Item_Code"), "desc": r.get("Description"), "items": [],
                                   "qty": 0.0, "bags": 0.0, "total": 0.0, "prices": [], "rows": 0,
                                   "calc_errors": 0, "by_doc": {}})
            q, p, t = num(r.get(qty_field)), num(r.get("Unit_Price")), num(r.get("Line_Total"))
            a["qty"] += q or 0
            a["bags"] += num(r.get("No_Of_Bags")) or 0
            a["total"] += t or 0
            a["rows"] += 1
            if r.get("Item_Code") and r["Item_Code"] not in a["items"]:
                a["items"].append(r["Item_Code"])
            if doc_key:
                did = r.get(doc_key)
                a["by_doc"][did] = a["by_doc"].get(did, 0.0) + (q or 0)
            if p is not None:
                a["prices"].append(p)
            if q is not None and p is not None and t is not None and abs(q * p - t) > 0.01:
                a["calc_errors"] += 1
        for a in out.values():
            a["price"] = a["prices"][0] if a["prices"] else None
        return out

    def _line_checks(self, po, invs, grns) -> list[dict]:
        P = self._index(po["lines"], ["Line_No"], "Qty")
        I = self._index([ln for d in invs for ln in d["lines"]], ["PO_Line_No", "Line_No"], "Qty",
                        "Invoice_No") if invs else None
        G = self._index([ln for d in grns for ln in d["lines"]], ["PO_Line_No", "Line_No"], "Received_Qty",
                        "GRN_No") if grns else None
        qt, pt = self.s["qty_tolerance_pct"], self.s["price_tolerance_pct"]
        rcv = "received on all GRNs" if self.multi["grn"] else "received"

        keys = list(P.keys())
        for src in (G, I):
            for k in (src or {}):
                if k not in keys:
                    keys.append(k)
        keys.sort(key=lambda k: (num(k) is None, num(k) or 0, k))

        rows = []
        for k in keys:
            p, i, g = P.get(k), (I or {}).get(k), (G or {}).get(k)
            issues: list[tuple[str, str]] = []
            cells: dict[str, str] = {}   # cell-level status for the UI

            def flag(st, msg, *cell_names):
                issues.append((st, msg))
                for c in cell_names:
                    if _RANK[st] > _RANK.get(cells.get(c, PASS), 1):
                        cells[c] = st

            if not p:
                flag(FAIL, "Line not on the PO (unordered item)", "po_qty")
            if G is not None and not g and p:
                flag(FAIL, "Not received — line missing on " + ("all selected GRNs" if self.multi["grn"] else "GRN"),
                     "grn_qty")
            if I is not None and not i and p:
                flag(WARN, "Not invoiced — line missing on " + ("all selected invoices" if self.multi["invoice"]
                                                               else "invoice"), "inv_qty")

            # item code consistency (across every document carrying this line)
            codes = {text(c) for x in (p, i, g) if x for c in x["items"]}
            if len(codes) > 1:
                flag(FAIL, "Item code differs between documents", "item")

            # quantities (cumulative across all selected invoices / GRNs)
            if p and g:
                if not self._close(g["qty"], p["qty"], qt):
                    if g["qty"] > p["qty"]:
                        flag(FAIL, f"Over-received by {fmt(g['qty'] - p['qty'])}", "grn_qty")
                    else:
                        flag(WARN, f"Short/partial receipt: {fmt(p['qty'] - g['qty'])} outstanding", "grn_qty")
            if i and g:
                if not self._close(i["qty"], g["qty"], qt):
                    if i["qty"] > g["qty"]:
                        flag(FAIL, f"Invoiced {fmt(i['qty'] - g['qty'])} more than {rcv}", "inv_qty")
                    else:
                        flag(WARN, f"Under-invoiced by {fmt(g['qty'] - i['qty'])} vs {rcv}", "inv_qty")
            elif i and p and G is None:
                if not self._close(i["qty"], p["qty"], qt):
                    flag(FAIL if i["qty"] > p["qty"] else WARN,
                         f"Invoice qty {fmt(i['qty'])} vs PO qty {fmt(p['qty'])}", "inv_qty")
            if i and p and g is None and G is not None:
                flag(FAIL, "Invoiced but nothing received for this line", "inv_qty")

            # unit prices: every invoice / GRN line must carry the PO price
            if p and p["price"] is not None:
                for x, name, cell in ((i, "Invoice", "inv_price"), (g, "GRN", "grn_price")):
                    bad = [pr for pr in (x["prices"] if x else []) if not self._close(pr, p["price"], pt)]
                    if bad:
                        flag(FAIL, f"{name} price {fmt(bad[0])} ≠ PO price {fmt(p['price'])}", cell)
                        x["price"] = bad[0]

            # bags
            if p and g and p["bags"] and g["bags"] and p["bags"] != g["bags"] and not cells.get("grn_qty"):
                flag(WARN, "Bag count differs between PO and GRN", "grn_qty")

            # arithmetic
            for name, x, cell in (("PO", p, "po_total"), ("Invoice", i, "inv_total"), ("GRN", g, "grn_total")):
                if x and x["calc_errors"]:
                    flag(FAIL, f"{name} line total ≠ qty × price", cell)

            # where the cumulative quantities came from
            sources = []
            for name, x, many in (("GRN", g, self.multi["grn"]), ("Invoice", i, self.multi["invoice"])):
                if x and many:
                    sources.append(f"{name}: " + " + ".join(f"{d} ({fmt(q)})" for d, q in x["by_doc"].items()))

            status = worst(s for s, _ in issues) if issues else PASS
            ref = p or i or g
            rows.append({
                "line": k, "item": ref.get("item"), "desc": ref.get("desc"),
                "po_qty": p["qty"] if p else None, "grn_qty": g["qty"] if g else None,
                "inv_qty": i["qty"] if i else None,
                "po_price": p["price"] if p else None, "grn_price": g["price"] if g else None,
                "inv_price": i["price"] if i else None,
                "po_total": p["total"] if p else None, "grn_total": g["total"] if g else None,
                "inv_total": i["total"] if i else None,
                "status": status, "issues": [m for _, m in issues], "cells": cells, "sources": sources,
                "has_grn": G is not None, "has_inv": I is not None,
            })
        return rows

    # ---------------------------------------------------------- UI field highlighting
    def _highlights(self, lines) -> dict:
        """{doc_type: {doc_id: {field: status}}, "lines": {po_line: status}}"""
        out: dict[str, dict] = {"po": {}, "invoice": {}, "grn": {}}
        for c in self.checks:
            if c.status not in (FAIL, WARN):
                continue
            for doc, f, did in c.fields:
                per_doc = out[doc].setdefault(did, {})
                cur = per_doc.get(f)
                if cur is None or _RANK[c.status] > _RANK[cur]:
                    per_doc[f] = c.status
        out["lines"] = {ln["line"]: ln["status"] for ln in lines}
        return out


def three_way_match(dataset: dict, po_no=None, invoice_nos=None, grn_nos=None, settings=None) -> dict:
    """Match a PO against one or more invoices and GRNs (each a document number or a list of them)."""
    return ThreeWayMatcher(dataset, settings).run(po_no, invoice_nos, grn_nos)


def linked_documents(dataset: dict) -> list[dict]:
    """For each PO: which invoices and GRNs reference it (used for the dashboard and auto-selection)."""
    invoices = (dataset.get("invoice") or {}).get("header", [])
    grns = (dataset.get("grn") or {}).get("header", [])
    rows = []
    for po in (dataset.get("po") or {}).get("header", []):
        pn = text(po.get("PO_No"))
        inv = [i["Invoice_No"] for i in invoices if text(i.get("PO_No")) == pn]
        gr = [g["GRN_No"] for g in grns if text(g.get("PO_No")) == pn]
        rows.append({"po": po.get("PO_No"), "supplier": po.get("Supplier_Name"), "date": po.get("PO_Date"),
                     "value": po.get("Total_Value"), "status": po.get("Status"),
                     "invoices": inv, "grns": gr})
    return rows


class _Links:
    """Lookups for the PO links page."""

    def __init__(self, dataset: dict):
        self.ds = dataset
        self.inv_h = (dataset.get("invoice") or {}).get("header", [])
        self.grn_h = (dataset.get("grn") or {}).get("header", [])
        self.line_counts: dict[tuple, int] = {}
        for doc, key in (("invoice", "Invoice_No"), ("grn", "GRN_No")):
            for ln in (dataset.get(doc) or {}).get("detail", []):
                self.line_counts[(doc, ln.get(key))] = self.line_counts.get((doc, ln.get(key)), 0) + 1

    def inv_row(self, i):
        return {"id": i.get("Invoice_No"), "date": i.get("Invoice_Date"), "ref": i.get("Supplier_Ref_No"),
                "value": num(i.get("Total_Supply_Value")), "net": num(i.get("Net_Total")),
                "status": i.get("Status"), "po_no": i.get("PO_No"),
                "lines": self.line_counts.get(("invoice", i.get("Invoice_No")), 0)}

    def grn_row(self, g):
        return {"id": g.get("GRN_No"), "date": g.get("GRN_Date"), "warehouse": g.get("Warehouse"),
                "value": num(g.get("Total_Value")), "status": g.get("Status"), "po_no": g.get("PO_No"),
                "lines": self.line_counts.get(("grn", g.get("GRN_No")), 0)}

    @staticmethod
    def pct(part, whole):
        return round(part / whole * 100, 1) if whole else None

    def po_summary(self, po: dict) -> dict:
        """One PO with its linked invoices / GRNs, cumulative values and quick match status."""
        pn = text(po.get("PO_No"))
        invs = sorted((self.inv_row(i) for i in self.inv_h if text(i.get("PO_No")) == pn),
                      key=lambda r: str(r["date"] or ""))
        grns = sorted((self.grn_row(g) for g in self.grn_h if text(g.get("PO_No")) == pn),
                      key=lambda r: str(r["date"] or ""))
        value = num(po.get("Total_Value"))
        invoiced = round(sum(r["value"] or 0 for r in invs), 2)
        received = round(sum(r["value"] or 0 for r in grns), 2)
        res = three_way_match(self.ds, po.get("PO_No"), [r["id"] for r in invs], [r["id"] for r in grns])
        flags = []
        if len(invs) > 1:
            flags.append("multi-inv")
        if len(grns) > 1:
            flags.append("multi-grn")
        if not invs:
            flags.append("no-inv")
        if not grns:
            flags.append("no-grn")
        if invs and grns and len(invs) != len(grns):
            flags.append("count-diff")
        return {
            "po": po.get("PO_No"), "date": po.get("PO_Date"), "supplier": po.get("Supplier_Name"),
            "delivery_date": po.get("Delivery_Date"), "status": po.get("Status"), "value": value,
            "invoices": invs, "grns": grns,
            "invoiced": invoiced, "received": received,
            "inv_pct": self.pct(invoiced, value), "grn_pct": self.pct(received, value),
            "quick": res["overall"], "fails": res["counts"]["FAIL"], "warns": res["counts"]["WARN"],
            "flags": flags,
        }


def po_link_overview(dataset: dict) -> dict:
    """
    Every PO with the full details of the invoices and GRNs that reference it, cumulative
    invoiced / received values, and a quick match status over all of them. Also lists invoices
    and GRNs whose PO number matches no PO in the data set.
    """
    lk = _Links(dataset)
    pos = [lk.po_summary(po) for po in (dataset.get("po") or {}).get("header", [])]
    po_keys = {text(p["po"]) for p in pos}
    orphans = [{"doc": "invoice", **lk.inv_row(i)} for i in lk.inv_h if text(i.get("PO_No")) not in po_keys] + \
              [{"doc": "grn", **lk.grn_row(g)} for g in lk.grn_h if text(g.get("PO_No")) not in po_keys]
    stats = {
        "pos": len(pos),
        "invoices": len(lk.inv_h), "grns": len(lk.grn_h),
        "multi-inv": sum("multi-inv" in p["flags"] for p in pos),
        "multi-grn": sum("multi-grn" in p["flags"] for p in pos),
        "no-inv": sum("no-inv" in p["flags"] for p in pos),
        "no-grn": sum("no-grn" in p["flags"] for p in pos),
        "count-diff": sum("count-diff" in p["flags"] for p in pos),
        "mismatch": sum(p["quick"] == "MISMATCH" for p in pos),
        "orphans": len(orphans),
    }
    return {"pos": pos, "orphans": orphans, "stats": stats}
