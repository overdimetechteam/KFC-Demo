"""Payment vouchers, raised from a three-way match that passed."""
import json
import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .data_loader import get_document
from .db import audit, get_db
from .matching import as_id_list, num, three_way_match
from .store import load_dataset

bp = Blueprint("vouchers", __name__)

# a match "passes" when nothing is mismatched and the PO, GRN(s) and invoice(s) are all present
PAYABLE = ("MATCHED", "MATCHED WITH WARNINGS")


def _ids(text):
    return as_id_list((text or "").split(","))


def voucher_for_run(run_id, run_user_id, result) -> dict:
    """What the results card offers: the voucher already raised, invoices already paid elsewhere, or a create button."""
    db = get_db()
    pv = db.execute("SELECT id, voucher_no FROM payment_vouchers WHERE run_id = ?", (run_id,)).fetchone()
    if pv:
        return {"voucher": pv}
    invoices = result.get("invoice_nos") or _ids(result.get("invoice_no"))
    paid = _paid_elsewhere(run_user_id, invoices)
    return {"voucher": None, "paid": paid,
            "payable": (result.get("overall") in PAYABLE and bool(invoices) and not paid
                        and run_user_id == g.user["id"])}


def _paid_elsewhere(user_id, invoices) -> list:
    if not invoices:
        return []
    marks = ",".join("?" * len(invoices))
    return get_db().execute(
        f"SELECT pvi.invoice_no, pv.id, pv.voucher_no FROM payment_voucher_invoices pvi "
        f"JOIN payment_vouchers pv ON pv.id = pvi.voucher_id WHERE pvi.user_id = ? AND pvi.invoice_no IN ({marks})",
        (user_id, *invoices)).fetchall()


def _sum(rows, field):
    return round(sum(num(r[field]) or 0 for r in rows), 2)


@bp.route("/vouchers/create", methods=["POST"])
@login_required
def create():
    run = get_db().execute("SELECT * FROM match_runs WHERE id = ?", (request.form.get("run_id", type=int),)).fetchone()
    if run is None or run["user_id"] != g.user["id"]:
        abort(404)
    back = redirect(url_for("main.history_view", run_id=run["id"]))
    existing = get_db().execute("SELECT id FROM payment_vouchers WHERE run_id = ?", (run["id"],)).fetchone()
    if existing:
        return redirect(url_for("vouchers.view", voucher_id=existing["id"]))

    saved = json.loads(run["result_json"])
    po_no = run["po_no"]
    invoices = saved.get("invoice_nos") or _ids(run["invoice_no"])
    grns = saved.get("grn_nos") or _ids(run["grn_no"])
    if saved["overall"] not in PAYABLE:
        flash(f"Run #{run['id']} is {saved['overall']} — a payment voucher needs a passed three-way match.", "error")
        return back

    # the data may have been re-uploaded since the match: it must still pass today
    ds = load_dataset(g.user["id"])
    now = three_way_match(ds, po_no, invoices, grns, saved.get("settings"))
    if now["overall"] not in PAYABLE:
        flash(f"The documents have changed since run #{run['id']} and the match is now {now['overall']}. "
              "Run the comparison again.", "error")
        return back
    paid = _paid_elsewhere(g.user["id"], invoices)
    if paid:
        flash("Already paid: " + ", ".join(f"{p['invoice_no']} on {p['voucher_no']}" for p in paid), "error")
        return back

    po = get_document(ds, "po", po_no)
    inv_docs = [get_document(ds, "invoice", i) for i in invoices]
    grn_docs = [get_document(ds, "grn", x) for x in grns]
    ph = po["header"]
    first = inv_docs[0]["header"]
    pairs = {p["invoice"]: p["grn"] for p in now.get("pairs") or [] if p.get("invoice")}
    inv_rows = [{"no": d["id"], "date": d["header"].get("Invoice_Date"), "ref": d["header"].get("Supplier_Ref_No"),
                 "grn": pairs.get(d["id"]) or d["header"].get("GRN_No"),
                 "supply": num(d["header"].get("Total_Supply_Value")), "discount": num(d["header"].get("Discount")),
                 "vat": num(d["header"].get("VAT_Amount")), "cess": num(d["header"].get("Less_Cess")),
                 "net": num(d["header"].get("Net_Total"))} for d in inv_docs]
    detail = {
        "po": {"no": po_no, "date": ph.get("PO_Date"), "value": num(ph.get("Total_Value")),
               "buyer": ph.get("Buyer_Name"), "contract": ph.get("Contract_No")},
        "grns": [{"no": d["id"], "date": d["header"].get("GRN_Date"), "value": num(d["header"].get("Total_Value")),
                  "warehouse": d["header"].get("Warehouse")} for d in grn_docs],
        "invoices": inv_rows,
        "warnings": [f"{c['check']}: {c['note']}" if c["note"] else c["check"]
                     for c in now["header_checks"] if c["status"] == "WARN"]
                    + [f"Line {ln['line']}: {'; '.join(ln['issues'])}" for ln in now["line_checks"] if ln["status"] == "WARN"],
    }

    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO payment_vouchers (user_id, run_id, po_no, invoice_nos, grn_nos, supplier_code, supplier_name,"
            " supplier_tin, currency, supply_total, discount_total, vat_total, cess_total, net_payable, match_result,"
            " detail_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (g.user["id"], run["id"], po_no, ", ".join(invoices), ", ".join(grns),
             first.get("Supplier_Code") or ph.get("Supplier_Code"), first.get("Supplier_Name") or ph.get("Supplier_Name"),
             first.get("Supplier_TIN") or ph.get("Supplier_TIN"), ph.get("Currency"),
             _sum(inv_rows, "supply"), _sum(inv_rows, "discount"), _sum(inv_rows, "vat"), _sum(inv_rows, "cess"),
             _sum(inv_rows, "net"), now["overall"], json.dumps(detail)))
        vid = cur.lastrowid
        db.execute("UPDATE payment_vouchers SET voucher_no = ? WHERE id = ?", (f"PV-{vid:06d}", vid))
        db.executemany("INSERT INTO payment_voucher_invoices (voucher_id, user_id, invoice_no) VALUES (?, ?, ?)",
                       [(vid, g.user["id"], i) for i in invoices])
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        flash("One of these invoices was put on another payment voucher at the same time.", "error")
        return back
    audit(g.user["id"], "create_voucher", f"PV-{vid:06d} for run #{run['id']}")
    flash(f"Payment voucher PV-{vid:06d} created.", "success")
    return redirect(url_for("vouchers.view", voucher_id=vid))


def _get_voucher(voucher_id):
    v = get_db().execute(
        "SELECT v.*, u.username, u.full_name FROM payment_vouchers v JOIN users u ON u.id = v.user_id WHERE v.id = ?",
        (voucher_id,)).fetchone()
    if v is None or (v["user_id"] != g.user["id"] and g.user["role"] != "admin"):
        abort(404)
    return v


@bp.route("/vouchers/<int:voucher_id>")
@login_required
def view(voucher_id):
    v = _get_voucher(voucher_id)
    return render_template("voucher.html", v=v, d=json.loads(v["detail_json"]),
                           amount_words=amount_in_words(v["net_payable"], v["currency"]))


@bp.route("/vouchers")
@login_required
def index():
    q = "SELECT v.id, v.voucher_no, v.po_no, v.invoice_nos, v.grn_nos, v.supplier_name, v.currency, v.net_payable, " \
        "v.match_result, v.status, v.created_at, v.run_id, u.username FROM payment_vouchers v JOIN users u ON u.id = v.user_id"
    args = ()
    if g.user["role"] != "admin":
        q += " WHERE v.user_id = ?"
        args = (g.user["id"],)
    return render_template("vouchers.html", vouchers=get_db().execute(q + " ORDER BY v.id DESC LIMIT 500", args).fetchall())


# ------------------------------------------------------------------ amount in words
_ONES = ("", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
         "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen")
_TENS = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety")


def _words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return (_TENS[n // 10] + " " + _ONES[n % 10]).strip()
    if n < 1000:
        return (_ONES[n // 100] + " Hundred " + _words(n % 100)).strip()
    for size, name in ((10 ** 9, "Billion"), (10 ** 6, "Million"), (1000, "Thousand")):
        if n >= size:
            return (_words(n // size) + f" {name} " + _words(n % size)).strip()
    return ""


def amount_in_words(amount, currency=None) -> str:
    """12345.5 -> 'Twelve Thousand Three Hundred Forty Five and Cents Fifty Only'."""
    cents_total = round((amount or 0) * 100)
    whole, cents = divmod(cents_total, 100)
    text = (_words(whole) or "Zero")
    if cents:
        text += f" and Cents {_words(cents)}"
    return f"{currency + ' ' if currency else ''}{text} Only"
