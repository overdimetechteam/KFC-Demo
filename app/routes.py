"""Main application routes: dashboard/upload, matching workspace, preview, history, export."""
import json
import os
import uuid

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, url_for)
from werkzeug.utils import secure_filename

import io

from .auth import login_required
from .data_loader import (ALLOWED_EXTENSIONS, dataset_summary, get_document, list_documents,
                          merge_into_dataset, ordered_columns, parse_workbook)
from .db import audit, get_db
from .doc_config import DOC_TYPES
from .matching import DEFAULT_SETTINGS, as_id_list, linked_documents, po_link_overview, three_way_match
from .report import build_report
from .store import clear_dataset, load_dataset, save_dataset, upload_folder
from .vouchers import voucher_for_run

bp = Blueprint("main", __name__)

UPLOAD_SLOTS = {"po_file": "po", "invoice_file": "invoice", "grn_file": "grn", "combined_file": None}


def _dataset():
    return load_dataset(g.user["id"])


def _has_data(ds):
    return any((ds.get(d) or {}).get("header") for d in DOC_TYPES)


def _ids(value):
    """Invoice / GRN selection from JSON or a query string: a list, or a comma-separated string."""
    if isinstance(value, str):
        value = value.split(",")
    return as_id_list(value if isinstance(value, list) else None)


# ------------------------------------------------------------------ dashboard & upload
@bp.route("/")
@login_required
def dashboard():
    ds = _dataset()
    links = linked_documents(ds)
    for row in links:
        res = three_way_match(ds, row["po"], row["invoices"], row["grns"])
        row["quick"] = res["overall"]
        row["fails"] = res["counts"]["FAIL"]
    return render_template("dashboard.html", summary=dataset_summary(ds), links=links, has_data=_has_data(ds))


@bp.route("/upload", methods=["POST"])
@login_required
def upload():
    ds = _dataset()
    any_file = False
    for slot, expected in UPLOAD_SLOTS.items():
        for f in request.files.getlist(slot):
            if not f or not f.filename:
                continue
            any_file = True
            name = secure_filename(f.filename) or "upload.xlsx"
            ext = os.path.splitext(name)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                flash(f"'{f.filename}' skipped — only .xlsx / .xlsm files are supported.", "error")
                continue
            path = os.path.join(upload_folder(g.user["id"]), f"{uuid.uuid4().hex[:8]}_{name}")
            f.save(path)
            try:
                parsed, warnings = parse_workbook(path)
            except Exception as exc:  # corrupt / unreadable workbook
                flash(f"Could not read '{f.filename}': {exc}", "error")
                continue
            if expected and not any(doc == expected for doc, _ in parsed):
                flash(f"'{f.filename}' was uploaded as {DOC_TYPES[expected]['label']} "
                      f"but no {DOC_TYPES[expected]['short']} sheet was detected.", "error")
            if not parsed:
                flash(f"No PO / Invoice / GRN sheets found in '{f.filename}'.", "error")
                continue
            for m in merge_into_dataset(ds, parsed, f.filename):
                flash(m, "success")
            for w in warnings:
                flash(w, "info")
            audit(g.user["id"], "upload", f.filename)
    if not any_file:
        flash("Choose at least one Excel file to upload.", "error")
    else:
        save_dataset(g.user["id"], ds)
    return redirect(url_for("main.dashboard"))


@bp.route("/upload/sample", methods=["POST"])
@login_required
def load_sample():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sample_data",
                        "PO_Invoice_GRN_Sample_DataBase.xlsx")
    if not os.path.exists(path):
        flash("Sample workbook not found in sample_data/.", "error")
        return redirect(url_for("main.dashboard"))
    ds = _dataset()
    parsed, _ = parse_workbook(path)
    merge_into_dataset(ds, parsed, os.path.basename(path))
    save_dataset(g.user["id"], ds)
    flash("Sample PO / Invoice / GRN data loaded.", "success")
    return redirect(url_for("main.dashboard"))


@bp.route("/data/clear", methods=["POST"])
@login_required
def clear_data():
    clear_dataset(g.user["id"])
    audit(g.user["id"], "clear_data")
    flash("Uploaded data cleared.", "success")
    return redirect(url_for("main.dashboard"))


# ------------------------------------------------------------------ PO links overview
@bp.route("/links")
@login_required
def links():
    ds = _dataset()
    if not _has_data(ds):
        flash("Upload your PO, Invoice and GRN Excel data first.", "info")
        return redirect(url_for("main.dashboard"))
    return render_template("links.html", **po_link_overview(ds))


# ------------------------------------------------------------------ matching workspace
@bp.route("/match")
@login_required
def match():
    ds = _dataset()
    if not _has_data(ds):
        flash("Upload your PO, Invoice and GRN Excel data first.", "info")
        return redirect(url_for("main.dashboard"))
    columns = {d: {"header": ordered_columns(ds, d, "header"), "detail": ordered_columns(ds, d, "detail")}
               for d in DOC_TYPES}
    preselect = {"po": request.args.get("po", ""), "invoice": request.args.get("invoice", ""),
                 "grn": request.args.get("grn", "")}
    return render_template("match.html", columns=columns, preselect=preselect, defaults=DEFAULT_SETTINGS)


@bp.route("/api/documents")
@login_required
def api_documents():
    ds = _dataset()
    return jsonify({d: list_documents(ds, d) for d in DOC_TYPES})


@bp.route("/api/document/<doc>/<path:doc_id>")
@login_required
def api_document(doc, doc_id):
    if doc not in DOC_TYPES:
        abort(404)
    d = get_document(_dataset(), doc, doc_id)
    if d is None:
        return jsonify({"error": f"{DOC_TYPES[doc]['label']} '{doc_id}' not found"}), 404
    return jsonify(d)


@bp.route("/api/compare", methods=["POST"])
@login_required
def api_compare():
    body = request.get_json(silent=True) or {}
    settings = {}
    for k in DEFAULT_SETTINGS:
        try:
            if body.get("settings", {}).get(k) not in (None, ""):
                settings[k] = float(body["settings"][k])
        except (TypeError, ValueError):
            return jsonify({"error": f"Invalid value for {k}"}), 400
    po, invs, grns = (body.get("po") or None), _ids(body.get("invoice")), _ids(body.get("grn"))
    if not po:
        return jsonify({"error": "Select a Purchase Order to compare."}), 400
    if not (invs or grns):
        return jsonify({"error": "Select at least one Invoice or GRN to compare against the PO."}), 400

    result = three_way_match(_dataset(), po, invs, grns, settings)
    db = get_db()
    cur = db.execute(
        "INSERT INTO match_runs (user_id, po_no, invoice_no, grn_no, overall, fails, warns, result_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], po, result["invoice_no"], result["grn_no"], result["overall"], result["counts"]["FAIL"],
         result["counts"]["WARN"], json.dumps(result)))
    db.commit()
    run_id = cur.lastrowid
    # the match page shows the line comparison above the document cards and the header checks below them
    return jsonify({"run_id": run_id, "overall": result["overall"], "counts": result["counts"],
                    "highlights": result["highlights"],
                    "html_summary": render_template("partials/results_summary.html", r=result, run_id=run_id,
                                                    pv=voucher_for_run(run_id, g.user["id"], result)),
                    "html_checks": render_template("partials/results_checks.html", r=result, run_id=run_id)})


# ------------------------------------------------------------------ preview
def _doc_context(doc, doc_id):
    if doc not in DOC_TYPES:
        abort(404)
    ds = _dataset()
    d = get_document(ds, doc, doc_id)
    if d is None:
        abort(404)
    return dict(d=d, cfg=DOC_TYPES[doc], header_cols=ordered_columns(ds, doc, "header"),
                line_cols=ordered_columns(ds, doc, "detail"))


@bp.route("/preview/<doc>/<path:doc_id>")
@login_required
def preview(doc, doc_id):
    ctx = _doc_context(doc, doc_id)
    if request.args.get("full"):
        return render_template("preview_page.html", **ctx)
    return render_template("partials/document.html", **ctx)


@bp.route("/details/<doc>/<path:doc_id>")
@login_required
def doc_details(doc, doc_id):
    """Left panel of the preview modal: the selected document's own fields, totals and lines only."""
    return render_template("partials/doc_details.html", **_doc_context(doc, doc_id))


# ------------------------------------------------------------------ history & export
def _get_run(run_id):
    row = get_db().execute(
        "SELECT r.*, u.username FROM match_runs r JOIN users u ON u.id = r.user_id WHERE r.id = ?",
        (run_id,)).fetchone()
    if row is None or (row["user_id"] != g.user["id"] and g.user["role"] != "admin"):
        abort(404)
    return row


@bp.route("/history")
@login_required
def history():
    q = "SELECT r.id, r.po_no, r.invoice_no, r.grn_no, r.overall, r.fails, r.warns, r.created_at, u.username " \
        "FROM match_runs r JOIN users u ON u.id = r.user_id"
    args = ()
    if g.user["role"] != "admin":
        q += " WHERE r.user_id = ?"
        args = (g.user["id"],)
    rows = get_db().execute(q + " ORDER BY r.id DESC LIMIT 500", args).fetchall()
    return render_template("history.html", runs=rows)


@bp.route("/history/<int:run_id>")
@login_required
def history_view(run_id):
    row = _get_run(run_id)
    r = json.loads(row["result_json"])
    return render_template("report.html", r=r, run=row, run_id=run_id, pv=voucher_for_run(run_id, row["user_id"], r))


@bp.route("/history/<int:run_id>/export")
@login_required
def export(run_id):
    row = _get_run(run_id)
    data = build_report(json.loads(row["result_json"]), dict(row))
    fname = f"3WM_{row['po_no'] or 'PO'}_{run_id}.xlsx".replace("/", "-")
    return send_file(io.BytesIO(data), as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
