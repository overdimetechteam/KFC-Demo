"""Run with:  python -m pytest -q"""
import copy
import io
import os
import re
import shutil
import tempfile

import pytest

from app import create_app
from app.data_loader import classify_sheet, merge_into_dataset, parse_workbook
from app.matching import linked_documents, po_link_overview, three_way_match

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "sample_data", "PO_Invoice_GRN_Sample_DataBase.xlsx")


@pytest.fixture(scope="module")
def ds():
    parsed, _ = parse_workbook(SAMPLE)
    d = {}
    merge_into_dataset(d, parsed, "sample.xlsx")
    return d


def _linked(ds, po):
    row = next(r for r in linked_documents(ds) if r["po"] == po)
    return row["invoices"], row["grns"]


def _match_all(ds, po, settings=None):
    """Match a PO against every invoice and GRN that references it."""
    invs, grns = _linked(ds, po)
    return three_way_match(ds, po, invs, grns, settings)


def _fail_checks(r):
    return [c["check"] for c in r["header_checks"] if c["status"] == "FAIL"]


def _pair(r, invoice):
    return next(p for p in r["pairs"] if p["invoice"] == invoice)


# ------------------------------------------------------------------ engine
def test_sheets_detected(ds):
    assert len(ds["po"]["header"]) == 17 and len(ds["po"]["detail"]) == 24
    assert len(ds["invoice"]["header"]) == 36 and len(ds["invoice"]["detail"]) == 41
    assert len(ds["grn"]["header"]) == 36 and len(ds["grn"]["detail"]) == 40


def test_invoice_sheet_with_grn_column_is_not_a_grn_sheet():
    # Invoice_Header carries a GRN_No reference column; it must still be read as invoices
    assert classify_sheet("Invoice_Header", ["Invoice_No", "PO_No", "GRN_No", "Supplier_Code"]) == ("invoice", "header")
    assert classify_sheet("Sheet1", ["Invoice_No", "PO_No", "GRN_No"]) == ("invoice", "header")
    assert classify_sheet("GRN_Detail", ["GRN_No", "PO_No", "PO_Line_No", "Received_Qty"]) == ("grn", "detail")


def test_every_po_has_equal_invoice_and_grn_counts(ds):
    for row in linked_documents(ds):
        assert len(row["invoices"]) >= 2 and len(row["invoices"]) == len(row["grns"]), row


@pytest.mark.parametrize("po,overall", [
    ("PO-1234", "MATCHED"), ("PO-1235", "MATCHED"), ("PO-1238", "MATCHED"), ("PO-1239", "MATCHED"),
    ("PO-1242", "MATCHED"), ("PO-1246", "MATCHED"), ("PO-1248", "MATCHED"), ("PO-1250", "MATCHED WITH WARNINGS"),
    ("PO-1236", "MISMATCH"), ("PO-1237", "MISMATCH"), ("PO-1240", "MISMATCH"), ("PO-1241", "MISMATCH"),
    ("PO-1243", "MISMATCH"), ("PO-1244", "MISMATCH"), ("PO-1245", "MISMATCH"),
    ("PO-1247", "MISMATCH"), ("PO-1249", "MISMATCH"),
])
def test_po_results(ds, po, overall):
    assert _match_all(ds, po)["overall"] == overall


@pytest.mark.parametrize("po,expect", [
    ("PO-1237", "Invoice price 28"),       # price variance
    ("PO-1236", "more than received"),     # billed > received
    ("PO-1241", "not on the PO"),          # unordered line
    ("PO-1247", "Over-received"),          # over-receipt
])
def test_line_mismatches(ds, po, expect):
    r = _match_all(ds, po)
    assert any(expect in m for ln in r["line_checks"] for m in ln["issues"])


@pytest.mark.parametrize("po,check", [
    ("PO-1243", "VAT"),
    ("PO-1240", "Invoice header total = sum of lines"),
    ("PO-1244", "Invoice date on/after PO date"),
    ("PO-1245", "Supplier code"),
])
def test_header_mismatches(ds, po, check):
    assert any(c.startswith(check) for c in _fail_checks(_match_all(ds, po))), _fail_checks(_match_all(ds, po))


@pytest.mark.parametrize("po,bad_invoice", [
    ("PO-1236", "26OCT_DY01_000114"), ("PO-1237", "26OCT_LP01_000022"), ("PO-1240", "26OCT_LP01_000024"),
    ("PO-1241", "26OCT_CA01_000056"), ("PO-1243", "26OCT_LP01_000026"), ("PO-1244", "26SEP_CA01_000057"),
    ("PO-1245", "26OCT_LP01_000027"), ("PO-1249", "26OCT_DY01_000121"),
])
def test_bad_pair_is_singled_out(ds, po, bad_invoice):
    # the invoice/GRN pair with the problem is a MISMATCH, the other pair on the same PO is clean
    r = _match_all(ds, po)
    assert _pair(r, bad_invoice)["verdict"] == "MISMATCH"
    assert all(p["verdict"] == "MATCHED" for p in r["pairs"] if p["invoice"] != bad_invoice), r["pairs"]


def test_pairs_follow_invoice_grn_reference(ds):
    r = _match_all(ds, "PO-1248")
    assert [(p["grn"], p["invoice"]) for p in r["pairs"]] == [
        ("GRN-1030", "26OCT_CA01_000062"), ("GRN-1031", "26OCT_CA01_000063"), ("GRN-1032", "26OCT_CA01_000064")]
    assert all(p["verdict"] == "MATCHED" for p in r["pairs"])
    assert "Invoice count = GRN count" not in _fail_checks(r)


def test_every_invoice_bills_its_own_grn(ds):
    for row in linked_documents(ds):
        r = three_way_match(ds, row["po"], row["invoices"], row["grns"])
        assert len({p["grn"] for p in r["pairs"]}) == len(r["pairs"]) == len(row["grns"]), r["pairs"]
        assert all(p["invoice"] for p in r["pairs"])


def test_double_billed_grn_and_unbilled_grn(ds):
    # invoice 2 of PO-1246 quoting GRN-1025 again (and repeating invoice 1's supplier reference):
    # GRN-1025 is billed twice and GRN-1026 never
    d = copy.deepcopy(ds)
    inv = next(h for h in d["invoice"]["header"] if h["Invoice_No"] == "26OCT_LP01_000029")
    inv.update(GRN_No="GRN-1025", Supplier_Ref_No="INLP000048")
    invs, grns = _linked(d, "PO-1246")
    r = three_way_match(d, "PO-1246", invs, grns)
    assert r["overall"] == "MISMATCH"
    assert any(c.startswith("Supplier invoice reference is unique") for c in _fail_checks(r))
    assert "GRN billed by exactly one invoice · GRN-1025" in _fail_checks(r)
    unbilled = next(p for p in r["pairs"] if p["invoice"] is None)
    assert unbilled["grn"] == "GRN-1026" and unbilled["verdict"] == "NOT INVOICED"
    assert any("double billing" in m for m in _pair(r, "26OCT_LP01_000029")["issues"])


def test_unequal_counts_are_a_mismatch(ds):
    # both invoices of PO-1242 against only one of its GRNs
    invs, grns = _linked(ds, "PO-1242")
    r = three_way_match(ds, "PO-1242", invs, grns[:1])
    assert r["overall"] == "MISMATCH"
    assert "Invoice count = GRN count" in _fail_checks(r)
    left_out = next(c for c in r["header_checks"] if c["check"] == "GRNs for this PO not in this match")
    assert left_out["grn"] == grns[1] and left_out["status"] == "WARN"


def test_missing_grn_is_flagged(ds):
    d = copy.deepcopy(ds)
    d["grn"]["header"] = [g for g in d["grn"]["header"] if g["PO_No"] != "PO-1238"]
    invs, _ = _linked(d, "PO-1238")
    r = three_way_match(d, "PO-1238", invs, [])
    assert "Goods receipt exists" in _fail_checks(r)


def test_split_deliveries_are_summed(ds):
    # PO-1242: 250 ordered, delivered and billed in two parts (150 + 100)
    r = _match_all(ds, "PO-1242")
    assert r["grn_nos"] == ["GRN-1017", "GRN-1018"] and r["grn_no"] == "GRN-1017, GRN-1018"
    line = r["line_checks"][0]
    assert line["grn_qty"] == 250 and line["inv_qty"] == 250
    assert line["sources"][0] == "GRN: GRN-1017 (150) + GRN-1018 (100)"
    # per-GRN document checks run for each GRN
    assert any(c["check"] == "GRN date on/after PO date · GRN-1018" for c in r["header_checks"])


def test_multiple_invoices_are_cumulated(ds):
    # PO-1249: 6,000 PO, invoice 2 bills 350 where GRN 2 received 300
    r = _match_all(ds, "PO-1249")
    assert "Total invoiced (2 invoices) vs PO value" in _fail_checks(r)
    assert r["line_checks"][0]["inv_qty"] == 650 and r["line_checks"][0]["grn_qty"] == 600
    # field highlights are reported per document
    assert r["highlights"]["invoice"]["26OCT_DY01_000121"]["Total_Supply_Value"] == "FAIL"


def test_po_link_overview(ds):
    o = po_link_overview(ds)
    by_po = {p["po"]: p for p in o["pos"]}
    assert [g["id"] for g in by_po["PO-1242"]["grns"]] == ["GRN-1017", "GRN-1018"]
    assert by_po["PO-1242"]["received"] == 2500 and by_po["PO-1242"]["grn_pct"] == 100
    assert by_po["PO-1242"]["quick"] == "MATCHED"
    assert o["stats"]["multi-grn"] == 17 and o["stats"]["multi-inv"] == 17
    assert o["stats"]["count-diff"] == 0 and o["stats"]["no-inv"] == 0 and o["stats"]["no-grn"] == 0
    assert o["orphans"] == []


def test_price_tolerance(ds):
    r = _match_all(ds, "PO-1237", {"price_tolerance_pct": 15})
    assert not any("price" in m for m in r["line_checks"][0]["issues"])


# ------------------------------------------------------------------ web
@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.mkdtemp()
    app = create_app({"TESTING": True, "DATABASE": os.path.join(tmp, "t.sqlite3")})
    app.instance_path = tmp
    from app import db
    db.init_db(app)
    with app.test_client() as c:
        yield c
    shutil.rmtree(tmp, ignore_errors=True)


def _csrf(html):
    return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)


def _login(c):
    tok = _csrf(c.get("/login").get_data(as_text=True))
    return c.post("/login", data={"username": "admin", "password": "admin123", "_csrf": tok})


def test_requires_login(client):
    assert client.get("/").status_code == 302
    assert client.get("/api/documents").status_code == 401


def test_bad_login(client):
    tok = _csrf(client.get("/login").get_data(as_text=True))
    r = client.post("/login", data={"username": "admin", "password": "nope", "_csrf": tok})
    assert b"Incorrect username or password" in r.data


def test_full_flow(client):
    assert _login(client).status_code == 302
    page = client.get("/").get_data(as_text=True)
    tok = _csrf(page)
    with open(SAMPLE, "rb") as fh:
        r = client.post("/upload", data={"_csrf": tok, "combined_file": (io.BytesIO(fh.read()), "data.xlsx")},
                        content_type="multipart/form-data", follow_redirects=True)
    assert b"PO-1234" in r.data

    docs = client.get("/api/documents").get_json()
    assert len(docs["po"]) == 17
    d = client.get("/api/document/po/PO-1234").get_json()
    assert d["header"]["Supplier_Code"] == "S001" and len(d["lines"]) == 5

    links = client.get("/links").get_data(as_text=True)
    assert "PO links" in links and "GRN-1018" in links and "26OCT_LP01_000029" in links and "counts tally" in links
    assert "counts differ" not in links

    # preview modal details: only the selected document, never the other document types
    det = client.get("/details/grn/GRN-1018").get_data(as_text=True)
    assert "GRN-1018" in det and "Goods Received Note" in det and "Line items" in det
    assert "GRN-1017" not in det and "26OCT_DY01_" not in det and "Supplier Invoice" not in det
    det = client.get("/details/po/PO-1242").get_data(as_text=True)
    assert "Purchase Order" in det and "GRN-" not in det and "26OCT_DY01_" not in det
    det = client.get("/details/invoice/26OCT_DY01_000118").get_data(as_text=True)
    assert "Supplier Invoice" in det and "Net Total" in det and "GRN-1017" not in det
    assert client.get("/details/grn/NOPE").status_code == 404
    assert client.get("/related/grn/GRN-1018").status_code == 404

    assert b"PURCHASE ORDER" in client.get("/preview/po/PO-1234").data
    assert b"TAX INVOICE" in client.get("/preview/invoice/26SEP_DY01_000111?full=1").data

    r = client.post("/api/compare", json={"po": "PO-1237", "invoice": ["26SEP_LP01_000021", "26OCT_LP01_000022"],
                                          "grn": ["GRN-1007", "GRN-1008"]}, headers={"X-CSRF-Token": tok})
    body = r.get_json()
    assert body["overall"] == "MISMATCH" and "Invoice price" in body["html_summary"]
    assert "GRN ↔ Invoice pairs" in body["html_summary"] and "counts tally" in body["html_summary"]
    assert "Header &amp; control checks" in body["html_checks"]

    # one PO against several GRNs and invoices, sent as lists
    r = client.post("/api/compare", json={"po": "PO-1242", "invoice": ["26OCT_DY01_000117", "26OCT_DY01_000118"],
                                          "grn": ["GRN-1017", "GRN-1018"]}, headers={"X-CSRF-Token": tok})
    multi = r.get_json()
    assert multi["overall"] == "MATCHED" and "GRN-1017 (150) + GRN-1018 (100)" in multi["html_summary"]
    assert b"GRN-1017, GRN-1018" in client.get(f"/history/{multi['run_id']}").data

    x = client.get(f"/history/{body['run_id']}/export")
    assert x.status_code == 200 and x.data[:2] == b"PK"
    assert b"MISMATCH" in client.get("/history").data

    # CSRF required
    assert client.post("/api/compare", json={"po": "PO-1234"}).status_code == 400

    client.post("/logout", data={"_csrf": tok})
    assert client.get("/").status_code == 302


def _compare(client, tok, po, invoices, grns):
    return client.post("/api/compare", json={"po": po, "invoice": invoices, "grn": grns},
                       headers={"X-CSRF-Token": tok}).get_json()


def test_payment_voucher(client, ds):
    from app.vouchers import amount_in_words
    _login(client)
    tok = _csrf(client.get("/").get_data(as_text=True))
    client.post("/upload/sample", data={"_csrf": tok})

    # a passed match offers the button; a mismatch does not
    invs, grns = _linked(ds, "PO-1248")
    ok = _compare(client, tok, "PO-1248", invs, grns)
    assert ok["overall"] == "MATCHED" and "Create payment voucher" in ok["html_summary"]
    bad = _compare(client, tok, "PO-1237", *_linked(ds, "PO-1237"))
    assert bad["overall"] == "MISMATCH" and "Create payment voucher" not in bad["html_summary"]
    r = client.post("/vouchers/create", data={"_csrf": tok, "run_id": bad["run_id"]}, follow_redirects=True)
    assert b"needs a passed three-way match" in r.data

    # create the voucher: net payable is the sum of the invoices' net totals
    r = client.post("/vouchers/create", data={"_csrf": tok, "run_id": ok["run_id"]}, follow_redirects=True)
    page = r.get_data(as_text=True)
    net = sum(h["Net_Total"] for h in ds["invoice"]["header"] if h["Invoice_No"] in invs)
    assert "PAYMENT VOUCHER" in page and "PV-000001" in page and f"{net:,.2f}" in page
    assert all(i in page for i in invs + grns) and amount_in_words(net, "LKR") in page

    # the run now links to its voucher, and clicking create again does not raise a second one
    assert "View payment voucher PV-000001" in client.get(f"/history/{ok['run_id']}").get_data(as_text=True)
    client.post("/vouchers/create", data={"_csrf": tok, "run_id": ok["run_id"]})
    assert client.get("/vouchers").get_data(as_text=True).count("PV-0000") == 1

    # the same invoices cannot be paid again from a new match run
    again = _compare(client, tok, "PO-1248", invs, grns)
    assert "Create payment voucher" not in again["html_summary"] and "Already paid" in again["html_summary"]
    r = client.post("/vouchers/create", data={"_csrf": tok, "run_id": again["run_id"]}, follow_redirects=True)
    assert b"Already paid" in r.data

    # MATCHED WITH WARNINGS also passes; its warnings are printed on the voucher
    warn = _compare(client, tok, "PO-1250", *_linked(ds, "PO-1250"))
    assert warn["overall"] == "MATCHED WITH WARNINGS" and "Create payment voucher" in warn["html_summary"]
    page = client.post("/vouchers/create", data={"_csrf": tok, "run_id": warn["run_id"]},
                       follow_redirects=True).get_data(as_text=True)
    assert "PV-000002" in page and "Late delivery" in page

    # vouchers belong to the user who raised them
    assert client.get("/vouchers/999").status_code == 404
    assert client.post("/vouchers/create", data={"_csrf": tok, "run_id": 999}).status_code == 404


def test_amount_in_words():
    from app.vouchers import amount_in_words
    assert amount_in_words(57000, "LKR") == "LKR Fifty Seven Thousand Only"
    assert amount_in_words(23088.5) == "Twenty Three Thousand Eighty Eight and Cents Fifty Only"
    assert amount_in_words(1_200_015.07) == "One Million Two Hundred Thousand Fifteen and Cents Seven Only"
