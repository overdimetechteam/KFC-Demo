# Three-Way Match — PO · GRN · Invoice

A Python (Flask) web application for **three-way matching** of Purchase Orders, Goods Received Notes and
Supplier Invoices loaded from Excel. It runs locally in your browser, with login/logout.

## Features

| Requirement | How it works |
|---|---|
| Login & logout | Username/password (hashed) stored in SQLite. 8-hour sessions, CSRF protection, admin user management, change password. |
| Upload Excel files | Upload PO, Invoice and GRN workbooks separately **or** one combined workbook. Sheets are detected from their columns, so sheet names don't matter. |
| Select PO / Invoice / GRN | Choose a PO and **all** of its linked Invoices and GRNs are ticked automatically (one PO can have several of each). Untick any to leave them out. Choosing an Invoice or GRN first selects its PO for you. |
| PO Links tab | For every PO: all invoices and GRNs that reference it (number, date, supplier ref / warehouse, lines, value, status), billed and received progress against the PO value, and the quick match status. Filter tiles (several invoices, several GRNs, no invoice, no GRN, mismatch), a search box, a **Match all** button, and a list of invoices/GRNs whose PO isn't in the data. |
| Payment voucher | When a match passes (MATCHED, or MATCHED WITH WARNINGS), a **Create payment voucher** button appears next to the result. It creates a printable voucher (PV-000001, …): pay-to supplier, PO, GRNs, each invoice's supply/discount/VAT/cess/net, net payable with amount in words, any reviewed warnings, and signature lines. Each invoice can be paid on one voucher only. The **Vouchers** tab lists them. |
| Preview button | Each document opens as a formatted document (Purchase Order / Tax Invoice / GRN) in a popup, with **Open / print** for a full page or PDF. |
| Auto-populated fields | Every header field fills its own text box and the line items fill a table as soon as a document is selected. |
| Compare | 25+ header and control checks plus line-by-line comparison of quantities, prices and totals. Mismatched fields are highlighted in red, warnings in amber. |
| Extras | Adjustable tolerances, saved match history, Excel export of each result, printable report, and a dashboard with quick status for every PO. |

## Quick start

**Requirements:** Python 3.10 or newer.

### Windows
Double-click **`start_windows.bat`**. The first run creates a virtual environment and installs the packages.

### macOS / Linux
```bash
./start_mac_linux.sh
```

### Manual
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

The app opens at **http://127.0.0.1:5000**.

**Default login:** `admin` / `admin123`. Change it straight away (click your name, top right).
To set a different first admin, set the environment variables `TWM_ADMIN_USER` and `TWM_ADMIN_PASSWORD`
before the first run.

Options: `python run.py --port 8080`, `--host 0.0.0.0` (allow access from other PCs on your network), `--no-browser`.

## Using it

1. **Data** page → upload your Excel file(s), or click **Load bundled sample data**.
2. **PO Links** page → see which invoices and GRNs belong to each PO; click **Match all** to compare them.
3. **Match** page → pick a Purchase Order. Every Invoice and GRN that references it is ticked automatically.
   Untick a document to leave it out. The cards are shown in the order PO, GRN, Invoice. When several invoices or GRNs are
   ticked, the tabs above the fields switch which document's header is shown; the line table lists the lines of all of them.
4. Click **Preview** on any card to open a large window for that document only. The left side lists its own header
   fields, totals and line items. The right side shows its printable sheet (**Open / print** for a full page).
   A PO preview shows only the PO, a GRN preview only the GRN, and an invoice preview only the invoice.
5. Click **Compare documents**. The verdict and the line-item comparison appear above the document cards; the
   header/control checks appear below them (click **Show checks** to expand).
   Use **Export to Excel** or **Printable report** to share the result.
6. If the result is **MATCHED** or **MATCHED WITH WARNINGS**, click **Create payment voucher**. Before creating
   it, the app reruns the match on the current data, and refuses invoices that are already on a voucher.
   The voucher opens ready to print, and the result then links to it. **Vouchers** lists all vouchers.
7. **History** lists every comparison run. Admins can see all users' runs.

## Excel format

Each document type has a header sheet and a detail (lines) sheet. The bundled
`sample_data/PO_Invoice_GRN_Sample_DataBase.xlsx` shows the expected layout:

| Sheet | Key columns |
|---|---|
| PO_Header | PO_No, PO_Date, Supplier_Code, Supplier_Name, Supplier_TIN, Buyer_Name, Contract_No, Currency, Delivery_Date, Status, Total_Value |
| PO_Detail | PO_No, Line_No, Item_Code, Description, UOM, No_Of_Bags, Qty, Unit_Price, Line_Total |
| Invoice_Header | Invoice_No, Supplier_Ref_No, Invoice_Date, Delivery_Date, PO_No, Supplier_Code, Supplier_Name, Supplier_TIN, Purchaser_Name, Contract_No, Total_Supply_Value, Discount, VAT_Amount, Less_Cess, Net_Total, Status |
| Invoice_Detail | Invoice_No, Line_No, PO_No, PO_Line_No, Item_Code, Description, No_Of_Bags, Qty, Unit_Price, Line_Total |
| GRN_Header | GRN_No, GRN_Date, PO_No, Supplier_Code, Dispatch_No, Warehouse, Received_By, Status, Total_Value |
| GRN_Detail | GRN_No, Line_No, PO_No, PO_Line_No, Item_Code, Description, UOM, No_Of_Bags, Received_Qty, Unit_Price, Line_Total |

- Column names are matched without regard to case, spaces or underscores (`PO No` = `PO_No`).
  For other names, add aliases in `app/doc_config.py` → `COLUMN_ALIASES`.
- Invoice and GRN lines are matched to PO lines using `PO_Line_No`.
- Extra columns are kept and shown in the UI and preview.

## What is checked

A PO is compared against **all selected invoices and GRNs together**. Partial deliveries and partial billing are
supported: one PO delivered on several GRNs and billed on several invoices matches when the totals agree.
- Line quantities and values are summed per PO line across every selected invoice and GRN. The results show the
  breakdown by document, for example `GRN: GRN-1008 (150) + GRN-1009 (100)`.
- PO-level value checks compare the **cumulative** invoiced and received values with the PO.
- Document-level checks (PO reference, dates, header total = lines, VAT/net, duplicate reference) run for each
  invoice and GRN, tagged with the document number.
- An invoice's delivery date is matched to the GRN with the same date, and its invoice date is checked against that GRN.
- Linked invoices or GRNs that were left out of the comparison are flagged as a warning.

**GRN ↔ Invoice pairs.** Each invoice bills exactly one GRN, named in the invoice's `GRN_No` column, and each GRN
is billed by exactly one invoice. So a PO must have **as many invoices as GRNs**.
- *Invoice count = GRN count*: unequal counts are a **mismatch**.
- *Invoice bills one GRN*: the invoice must quote exactly one GRN of this PO.
- *GRN billed by exactly one invoice*: two invoices quoting the same GRN is double billing (mismatch). A GRN no
  invoice quotes is shown as **NOT INVOICED**.
- Each invoice is compared with its own GRN: value, and quantity and price per line.
- The results show a **GRN ↔ Invoice pairs** table with a verdict for each pair, so you can see which delivery
  and invoice caused a PO-level mismatch. It's also exported as a sheet in the Excel report.
- Without a `GRN_No` column on the invoices, the count rule still applies, but pairs can't be formed.

**Line level** (per PO line): line missing on the GRN or invoice, items that aren't on the PO, item code differences,
over-receipt, short or partial receipt, invoiced quantity above the received quantity, invoice and GRN unit price
against the PO price, and line total = qty × price.

**Header / control checks:**
- Invoice and GRN quote the PO number.
- Supplier code, name and TIN agree.
- Buyer and contract number agree.
- Dates: invoice and GRN are not dated before the PO, delivery is on time against the PO delivery date,
  the invoice delivery date equals the GRN date, and the invoice is not dated before the GRN.
- Header totals equal the sum of their lines.
- Invoice vs PO, GRN vs PO and invoice vs GRN values agree.
- VAT = rate × (supply − discount), and net = supply − discount + VAT − cess.
- Duplicate supplier invoice references, linked invoices/GRNs left out of the match (with cumulative over-billing),
  and missing GRN or invoice.

**Overall result:** `MATCHED`, `MATCHED WITH WARNINGS`, `INCOMPLETE` (a document is missing) or `MISMATCH`.
Tolerances (quantity %, price %, amount rounding, VAT rate) can be changed under **Tolerances** on the Match page.
The defaults are in `app/matching.py` → `DEFAULT_SETTINGS`.

### Scenarios in the sample data
Every PO has 2 or 3 invoices and the same number of GRNs, with one invoice per GRN. The `PO_Tally` and
`README_Scenarios` sheets in the workbook list them; the app skips those two sheets when loading.

| PO | Pairs | Outcome |
|---|---|---|
| PO-1234, 1235, 1238, 1239, 1242, 1246 | 2 | Clean match |
| PO-1248 | 3 | Clean match: 3 deliveries, 3 invoices |
| PO-1236 | 2 | Invoice 2 bills 100, GRN 2 received 50 |
| PO-1237 | 2 | Invoice 2 unit price 28 vs PO price 25 |
| PO-1240 | 2 | Invoice 2 header total ≠ sum of its lines |
| PO-1241 | 2 | Invoice 2 bills a line that isn't on the PO |
| PO-1243 | 2 | Invoice 2 VAT at 15% instead of 18% |
| PO-1244 | 2 | Invoice 1 dated before the PO |
| PO-1245 | 2 | Invoice 2 from a different supplier |
| PO-1247 | 3 | GRN 3 over-received (600 against 500 ordered) |
| PO-1249 | 2 | Invoice 2 bills 350, GRN 2 received 300 (PO over-billed) |
| PO-1250 | 2 | GRN 2 delivered after the PO delivery date (warning) |

## Project structure

```
three_way_match/
├── run.py                  # entry point (uses waitress if installed)
├── requirements.txt
├── start_windows.bat / start_mac_linux.sh
├── app/
│   ├── __init__.py         # app factory, config, template filters
│   ├── auth.py             # login, logout, CSRF, users, password change
│   ├── db.py               # SQLite schema: users, match_runs, audit_log
│   ├── data_loader.py      # Excel parsing and sheet/column detection
│   ├── doc_config.py       # field definitions and column aliases
│   ├── matching.py         # three-way matching engine
│   ├── report.py           # Excel export
│   ├── routes.py           # pages and JSON API
│   ├── store.py            # per-user uploaded dataset storage
│   ├── templates/          # Jinja2 pages, document preview, results
│   └── static/             # CSS and JavaScript (no CDN, works offline)
├── sample_data/            # sample workbook
├── tests/                  # pytest suite: python -m pytest -q
└── instance/               # created at runtime: database, uploads, secret key
```

## Notes
- Data is kept per user under `instance/`. Delete that folder to reset everything, including users.
- For deployment on a shared server, set a fixed `SECRET_KEY` environment variable and put the app behind HTTPS.
