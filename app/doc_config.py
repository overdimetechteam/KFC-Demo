"""
Document definitions used by the loader, the UI and the matching engine.

If your Excel files use different column names, add them to COLUMN_ALIASES
(left = name in your file, right = canonical name used by the app).
Matching of column names is case/space/underscore-insensitive, so
"PO No", "po_no" and "PO-NO" all resolve to "PO_No" automatically.
"""

DOC_TYPES = {
    "po": {
        "label": "Purchase Order",
        "short": "PO",
        "title": "PURCHASE ORDER",
        "key": "PO_No",
        "date": "PO_Date",
        "header_fields": [
            "PO_No", "PO_Date", "Supplier_Code", "Supplier_Name", "Supplier_TIN",
            "Buyer_Name", "Contract_No", "Currency", "Delivery_Date", "Status",
            "Total_Value",
        ],
        "line_fields": [
            "Line_No", "Item_Code", "Description", "UOM", "No_Of_Bags", "Qty",
            "Unit_Price", "Line_Total",
        ],
        "totals": ["Total_Value"],
        "party_fields": ["Supplier_Code", "Supplier_Name", "Supplier_TIN"],
    },
    "invoice": {
        "label": "Supplier Invoice",
        "short": "Invoice",
        "title": "TAX INVOICE",
        "key": "Invoice_No",
        "date": "Invoice_Date",
        "header_fields": [
            "Invoice_No", "Supplier_Ref_No", "Invoice_Date", "Delivery_Date", "PO_No",
            "Supplier_Code", "Supplier_Name", "Supplier_TIN", "Purchaser_Name",
            "Contract_No", "Total_Supply_Value", "Discount", "VAT_Amount", "Less_Cess",
            "Net_Total", "Status",
        ],
        "line_fields": [
            "Line_No", "PO_Line_No", "Item_Code", "Description", "No_Of_Bags", "Qty",
            "Unit_Price", "Line_Total",
        ],
        "totals": ["Total_Supply_Value", "Discount", "VAT_Amount", "Less_Cess", "Net_Total"],
        "party_fields": ["Supplier_Code", "Supplier_Name", "Supplier_TIN"],
    },
    "grn": {
        "label": "Goods Received Note",
        "short": "GRN",
        "title": "GOODS RECEIVED NOTE",
        "key": "GRN_No",
        "date": "GRN_Date",
        "header_fields": [
            "GRN_No", "GRN_Date", "PO_No", "Supplier_Code", "Dispatch_No", "Warehouse",
            "Received_By", "Status", "Total_Value",
        ],
        "line_fields": [
            "Line_No", "PO_Line_No", "Item_Code", "Description", "UOM", "No_Of_Bags",
            "Received_Qty", "Unit_Price", "Line_Total",
        ],
        "totals": ["Total_Value"],
        "party_fields": ["Supplier_Code"],
    },
}

# Fields that are displayed/formatted as money or quantities
MONEY_FIELDS = {
    "Total_Value", "Total_Supply_Value", "Discount", "VAT_Amount", "Less_Cess",
    "Net_Total", "Unit_Price", "Line_Total",
}
QTY_FIELDS = {"Qty", "Received_Qty", "No_Of_Bags"}

# Extra alias names -> canonical column name
COLUMN_ALIASES = {
    "PurchaseOrderNo": "PO_No",
    "PONumber": "PO_No",
    "InvoiceNumber": "Invoice_No",
    "GRNNumber": "GRN_No",
    "Quantity": "Qty",
    "ReceivedQuantity": "Received_Qty",
    "Price": "Unit_Price",
    "Amount": "Line_Total",
    "VAT": "VAT_Amount",
    "Cess": "Less_Cess",
}


def label(field: str) -> str:
    """Human label for a column name: 'Supplier_TIN' -> 'Supplier TIN'."""
    special = {"PO_No": "PO No", "GRN_No": "GRN No", "PO_Line_No": "PO Line",
               "No_Of_Bags": "Bags", "VAT_Amount": "VAT Amount", "Line_No": "Line"}
    return special.get(field, field.replace("_", " "))
