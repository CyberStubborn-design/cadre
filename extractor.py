"""
Quote data extraction using Groq API (llama-3.3-70b-versatile).
Parses PDF text → structured JSON → Excel rows.
"""

import re
import json
import logging
from io import BytesIO
from datetime import datetime

log = logging.getLogger("cadre.extractor")

# ── Column schema (matches SampleCadre.xlsx exactly) ─────────────────────────
COLUMNS = [
    "ReferralManager", "ReferralEmail", "Brand", "QuoteNumber", "QuoteDate",
    "Company", "FirstName", "LastName", "ContactEmail", "ContactPhone",
    "Address", "County", "City", "State", "ZipCode", "Country",
    "item_id", "item_desc", "Unit Price", "TotalSales",
    "QuoteValidDate", "CustomerNumber", "manufacturer_Name", "PDF", "DemoQuote",
]

EXTRACTION_PROMPT = """You are a data extraction agent for Cadre Wire Group.
Extract ALL fields from this sales quote PDF text. Return ONLY valid JSON — no markdown, no explanation.

Return exactly this JSON structure:
{
  "quote_number":        "string",
  "quote_date":          "MM/DD/YYYY",
  "quote_valid_through": "MM/DD/YYYY",
  "customer_number":     "string (account number, e.g. 100447)",
  "company":             "string (customer company name)",
  "contact_first_name":  "string",
  "contact_last_name":   "string",
  "contact_email":       "string or null",
  "contact_phone":       "string or null",
  "address":             "string (street only)",
  "city":                "string",
  "state":               "string (2-letter)",
  "zip_code":            "string",
  "country":             "string (default USA)",
  "salesperson":         "string (full name)",
  "quote_description":   "string or null",
  "line_items": [
    {
      "item_id":    "string (part number)",
      "item_desc":  "string (full description including stock notes)",
      "ordered":    number,
      "unit_price": number,
      "extension":  number
    }
  ],
  "product_total": number,
  "tax":           number,
  "grand_total":   number
}

Rules:
- Include Tax as a line item with item_id="Tax", item_desc="", ordered=1, unit_price=tax_amount, extension=tax_amount
- For cable priced per MFT (per thousand feet), keep unit_price as the MFT price (e.g. 4190.0 for 4,190.00/MFT)
- If a field is missing, use null for strings and 0 for numbers
- Return ONLY the raw JSON object — no ```json fences, no preamble

PDF TEXT:
"""

SALESPERSON_EMAIL = {
    "regina deavers":   "rdeavers@cadrewire.com",
    "dara august":      "daugust@cadrewire.com",
    "andrew smith":     "Asmith@cadrewire.com",
    "industrial sales": None,
}


# ── PDF text extraction ───────────────────────────────────────────────────────
def pdf_bytes_to_text(pdf_bytes: bytes) -> str:
    try:
        from pdfminer.high_level import extract_text_to_fp
        from pdfminer.layout import LAParams
        output = BytesIO()
        extract_text_to_fp(BytesIO(pdf_bytes), output, laparams=LAParams(), output_type="text")
        return output.getvalue().decode("utf-8", errors="ignore")
    except Exception as e:
        log.warning("pdfminer failed: %s", e)
        return ""


# ── MSG → PDF extraction ──────────────────────────────────────────────────────
def extract_pdf_from_msg(msg_bytes: bytes) -> bytes | None:
    pdf_start = msg_bytes.find(b"%PDF-")
    if pdf_start == -1:
        return None
    eof_pos = msg_bytes.rfind(b"%%EOF", pdf_start)
    return msg_bytes[pdf_start: eof_pos + 5] if eof_pos > 0 else msg_bytes[pdf_start:]


def extract_sender_email(msg_bytes: bytes) -> str | None:
    hits = re.findall(rb"From:\s.*?<([a-zA-Z0-9._%+\-]+@cadrewire\.com)>", msg_bytes)
    if hits:
        return hits[0].decode()
    hits = re.findall(rb"([a-zA-Z0-9._%+\-]+@cadrewire\.com)", msg_bytes)
    return hits[0].decode() if hits else None


# ── Groq AI extraction ────────────────────────────────────────────────────────
def extract_quote_with_groq(pdf_text: str, api_key: str, model: str = "llama-3.3-70b-versatile") -> dict:
    from groq import Groq
    client = Groq(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": EXTRACTION_PROMPT + pdf_text}],
        temperature=0.0,
        max_tokens=4096,
    )
    raw = response.choices[0].message.content.strip()
    # Strip any accidental markdown fences
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    return json.loads(raw)


# ── Date helper ───────────────────────────────────────────────────────────────
def _to_date(date_str: str):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%m/%d/%Y").date()
    except Exception:
        return date_str


# ── Build Excel rows from extracted data ──────────────────────────────────────
def build_rows(data: dict, sender_email: str | None, quote_number: str) -> list[dict]:
    salesperson_name = (data.get("salesperson") or "").strip().lower()
    referral_email   = sender_email or SALESPERSON_EMAIL.get(salesperson_name)

    base = {
        "ReferralManager":   None,
        "ReferralEmail":     referral_email,
        "Brand":             "Cadre Wire Group",
        "QuoteNumber":       data.get("quote_number"),
        "QuoteDate":         _to_date(data.get("quote_date", "")),
        "Company":           data.get("company"),
        "FirstName":         data.get("contact_first_name"),
        "LastName":          data.get("contact_last_name"),
        "ContactEmail":      data.get("contact_email"),
        "ContactPhone":      data.get("contact_phone"),
        "Address":           data.get("address"),
        "County":            None,
        "City":              data.get("city"),
        "State":             data.get("state"),
        "ZipCode":           data.get("zip_code"),
        "Country":           data.get("country", "USA"),
        "QuoteValidDate":    _to_date(data.get("quote_valid_through", "")),
        "CustomerNumber":    data.get("customer_number"),
        "manufacturer_Name": None,
        "PDF":               f"Cadre Wire Group_{quote_number}.pdf",
        "DemoQuote":         None,
    }

    rows = []
    for item in data.get("line_items", []):
        row = {
            **base,
            "item_id":    item.get("item_id"),
            "item_desc":  item.get("item_desc"),
            "Unit Price": item.get("unit_price"),
            "TotalSales": item.get("extension"),
        }
        rows.append(row)
    return rows


# ── Validation ────────────────────────────────────────────────────────────────
def validate_rows(data: dict, rows: list[dict]) -> list[str]:
    issues = []
    if not data.get("quote_number"):
        issues.append("Could not find a quote number in this document")
    product_rows = [r for r in rows if r.get("item_id") != "Tax"]
    if not product_rows:
        issues.append("No line items found — this PDF may not be a standard Cadre quote")
    extracted_total = sum(r.get("TotalSales") or 0 for r in product_rows)
    pdf_total = data.get("product_total") or 0
    if pdf_total and abs(extracted_total - pdf_total) > 0.10:
        issues.append(f"Total mismatch: extracted ${extracted_total:.2f} vs PDF ${pdf_total:.2f}")
    return issues


# ── Full pipeline: msg bytes → result dict ────────────────────────────────────
def process_msg_bytes(msg_bytes: bytes, api_key: str, model: str = "llama-3.3-70b-versatile") -> dict:
    """
    Full pipeline for an Outlook .msg file.
    Returns: {rows, data, issues, sender_email, pdf_text}
    """
    sender_email = extract_sender_email(msg_bytes)
    pdf_bytes    = extract_pdf_from_msg(msg_bytes)

    if not pdf_bytes:
        return {
            "rows": [], "data": {}, "sender_email": sender_email, "pdf_text": "",
            "issues": ["No PDF attachment found inside this .msg file"],
        }

    pdf_text = pdf_bytes_to_text(pdf_bytes)
    if not pdf_text.strip():
        return {
            "rows": [], "data": {}, "sender_email": sender_email, "pdf_text": "",
            "issues": ["PDF found but no text could be extracted (may be a scanned image)"],
        }

    try:
        data = extract_quote_with_groq(pdf_text, api_key, model)
    except Exception as e:
        return {
            "rows": [], "data": {}, "sender_email": sender_email, "pdf_text": pdf_text,
            "issues": [f"Groq AI extraction failed: {e}"],
        }

    q_num  = data.get("quote_number", "UNKNOWN")
    rows   = build_rows(data, sender_email, q_num)
    issues = validate_rows(data, rows)

    return {
        "rows":         rows,
        "data":         data,
        "issues":       issues,
        "sender_email": sender_email,
        "pdf_text":     pdf_text,
    }
