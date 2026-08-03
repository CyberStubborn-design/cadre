"""
Cadre Wire Group - Quote Parser
Uses extract-msg + pymupdf/pdfminer + local Ollama AI

Ollama setup:
  1. Install and start Ollama: https://ollama.com
  2. Pull a model, for example: ollama pull llama3.1:8b
  3. Optional environment variables:
     OLLAMA_MODEL=llama3.1:8b
     OLLAMA_BASE_URL=http://localhost:11434
"""

import re
import os
import json
import tempfile
import shutil
import gc

try:
    import fitz
    def _pdf_to_text(pdf_bytes):
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        return "\n".join(page.get_text("text") for page in doc)
except ImportError:
    from pdfminer.high_level import extract_text_to_fp
    from pdfminer.layout import LAParams
    from io import BytesIO
    def _pdf_to_text(pdf_bytes):
        out = BytesIO()
        extract_text_to_fp(BytesIO(pdf_bytes), out, laparams=LAParams(), output_type="text")
        return out.getvalue().decode("utf-8", errors="ignore")

try:
    import extract_msg
except Exception:
    extract_msg = None

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:31b-cloud")
OLLAMA_TIMEOUT_SECONDS = int(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "120"))


SALESPERSON_MAP = {
    "regina deavers":   "rdeavers@cadrewire.com",
    "dara august":      "daugust@cadrewire.com",
    "andrew smith":     "Asmith@cadrewire.com",
    "industrial sales": "rdeavers@cadrewire.com",
}

HEADERS = [
    "ReferralManager", "ReferralEmail", "Brand", "QuoteNumber", "QuoteDate",
    "Company", "FirstName", "LastName", "ContactEmail", "ContactPhone",
    "Address", "County", "City", "State", "ZipCode", "Country",
    "item_id", "item_desc", "Unit Price", "TotalSales",
    "QuoteValidDate", "CustomerNumber", "manufacturer_Name", "PDF", "DemoQuote",
]

PROMPT = """You are a data extraction agent for Cadre Wire Group.
Extract ALL fields from this sales quote PDF text. Return ONLY valid JSON, no markdown, no explanation.

Return exactly this structure:
{
  "quote_number":        "string",
  "quote_date":          "MM/DD/YYYY",
  "quote_valid_through": "MM/DD/YYYY",
  "customer_number":     "string",
  "company":             "string",
  "contact_first_name":  "string",
  "contact_last_name":   "string",
  "contact_email":       "string or empty",
  "contact_phone":       "string or empty",
  "address":             "string (street only, no city/state/zip)",
  "city":                "string",
  "state":               "string (2-letter)",
  "zip_code":            "string",
  "country":             "string (default USA)",
  "salesperson":         "string (full name)",
  "line_items": [
    {
      "item_id":    "string (part number only, e.g. HS.1635F1-C48)",
      "item_desc":  "string (full description)",
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
- Include Tax as a line item: item_id="Tax", item_desc="", ordered=1, unit_price=tax_amount, extension=tax_amount
- For cable priced per MFT keep unit_price as the MFT rate (e.g. 4190.0)
- country default is "USA"
- Return ONLY the raw JSON object

PDF TEXT:
"""


def _clean(v):
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v).replace("\xa0", " ")).strip()


def _referral_email(salesperson):
    return SALESPERSON_MAP.get(_clean(salesperson).lower(), "rdeavers@cadrewire.com")


def _make_pdf_name(q):
    s = _clean(q)
    return f"Cadre Wire Group_{s}.pdf" if s else "Cadre Wire Group_unknown.pdf"


def _is_quote_pdf(filename):
    return bool(re.match(r"quote\s*\d+", filename.lower().strip()))


def _trim_pdf_text(text, max_chars=3500):
    """
    Remove boilerplate footer text to stay within model token limits.
    Keeps everything up to and including the last line item / tax row.
    """
    for marker in ["Terms and Conditions", "Terms & Conditions",
                   "TERMS AND CONDITIONS", "Thank you for",
                   "This quote is valid"]:
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx]
            break
    if len(text) > max_chars:
        text = text[:max_chars]
    return text.strip()


def _extract_json_object(raw):
    """Return the first valid JSON object from an LLM response."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"Ollama did not return JSON. Response starts with: {raw[:300]!r}")

    return json.loads(raw[start:end + 1])


def _extract_with_ollama(pdf_text):
    """Extract quote fields using a locally running Ollama model."""
    from urllib import request, error

    pdf_text = _trim_pdf_text(pdf_text)
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": PROMPT + pdf_text}],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0,
            "num_predict": 4096,
        },
    }

    req = request.Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    print(f"{OLLAMA_BASE_URL}/api/chat")

    try:
        with request.urlopen(req, timeout=OLLAMA_TIMEOUT_SECONDS) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except error.URLError as exc:
        raise RuntimeError(
            "Could not connect to Ollama. Make sure Ollama is running and the "
            "model is pulled, e.g. `ollama pull llama3.1:8b`."
        ) from exc

    raw = result.get("message", {}).get("content", "")
    if not raw:
        raise ValueError(f"Ollama returned an empty response: {result}")
    return _extract_json_object(raw)


def _extract_prices_from_text(pdf_text):
    """
    Extract {item_id: (unit_price, extension)} directly from PDF text using regex.
    Handles fitz EAC (same-line), fitz MFT (cross-line), and pdfminer formats.
    """
    U = r"(?:FT|EAC|MFT|LOT|EA|PR|C)"

    # Pattern 1: fitz EAC — item_id + qty + price + ext on same line
    p1 = re.findall(
        r"(?:^|\n)\d{1,3}\s+([A-Z][A-Z0-9./_\-]{3,})\s+[\d,]+\s+(?:FT|EAC|MFT|LOT|EA|PR|C)\s+([\d,]+\.\d+)\s+(?:FT|EAC|MFT|LOT|EA|PR|C)\s+([\d,]+\.\d{2})",
        pdf_text
    )
    if p1:
        return {iid: (float(p.replace(",","")), float(e.replace(",",""))) for iid, p, e in p1}

    # Pattern 2: fitz MFT — "qty UNIT\nprice UNIT\next"
    item_ids_fitz = re.findall(r"(?:^|\n)\d{1,3}\s+([A-Z][A-Z0-9./_\-]{3,})", pdf_text)
    triplets_fitz = re.findall(
        r"[\d,]+\s+(?:FT|EAC|MFT|LOT|EA|PR|C)\n+([\d,]+\.\d+)\s+(?:FT|EAC|MFT|LOT|EA|PR|C)\n+([\d,]+\.\d{2})",
        pdf_text
    )
    if item_ids_fitz and triplets_fitz and len(triplets_fitz) >= len(item_ids_fitz):
        prices = {}
        for i, iid in enumerate(item_ids_fitz):
            p, e = triplets_fitz[i]
            prices[iid] = (float(p.replace(",","")), float(e.replace(",","")))
        return prices

    # Pattern 3: pdfminer — item_ids and prices in interleaved column blocks
    item_ids_pm = [iid for _, iid in re.findall(r"\n\n(\d{1,3})\n\n([A-Z][A-Z0-9./_\-]{3,})\n", pdf_text)]
    triplets_pm = re.findall(
        r"[\d,]+\s+(?:FT|EAC|MFT|LOT|EA|PR|C)\n\n([\d,]+\.\d+)\n\n(?:FT|EAC|MFT|LOT|EA|PR|C)\n\n([\d,]+\.\d{2})",
        pdf_text
    )
    prices = {}
    for i, iid in enumerate(item_ids_pm):
        if i < len(triplets_pm):
            p, e = triplets_pm[i]
            prices[iid] = (float(p.replace(",","")), float(e.replace(",","")))
    return prices


def _build_rows(data, pdf_name, pdf_text=""):
    salesperson = _clean(data.get("salesperson", ""))

    # Extract prices from PDF text as ground truth (overrides AI values)
    price_map = _extract_prices_from_text(pdf_text) if pdf_text else {}

    base = {
        "ReferralManager":   "",
        "ReferralEmail":     _referral_email(salesperson),
        "Brand":             "Cadre Wire Group",
        "QuoteNumber":       _clean(data.get("quote_number", "")),
        "QuoteDate":         _clean(data.get("quote_date", "")),
        "Company":           _clean(data.get("company", "")),
        "FirstName":         _clean(data.get("contact_first_name", "")),
        "LastName":          _clean(data.get("contact_last_name", "")),
        "ContactEmail":      _clean(data.get("contact_email", "")),
        "ContactPhone":      _clean(data.get("contact_phone", "")),
        "Address":           _clean(data.get("address", "")),
        "County":            "",
        "City":              _clean(data.get("city", "")),
        "State":             _clean(data.get("state", "")),
        "ZipCode":           _clean(data.get("zip_code", "")),
        "Country":           _clean(data.get("country", "USA")),
        "QuoteValidDate":    _clean(data.get("quote_valid_through", "")),
        "CustomerNumber":    _clean(data.get("customer_number", "")),
        "manufacturer_Name": "",
        "PDF":               pdf_name,
        "DemoQuote":         "",
    }
    rows = []
    for item in data.get("line_items", []):
        item_id = _clean(item.get("item_id", ""))

        if item_id in price_map:
            unit_price  = price_map[item_id][0]
            total_sales = price_map[item_id][1]
        else:
            unit_price  = item.get("unit_price", "")
            total_sales = item.get("extension", "")

        row = {
            **base,
            "item_id":    item_id,
            "item_desc":  _clean(item.get("item_desc", "")),
            "Unit Price": unit_price,
            "TotalSales": total_sales,
        }
        rows.append({h: row.get(h, "") for h in HEADERS})
    return rows


def _parse_quote_pdf(pdf_bytes):
    text = _pdf_to_text(pdf_bytes)
    if not text.strip():
        raise ValueError("No text could be extracted from this PDF")
    data     = _extract_with_ollama(text)
    q_num    = _clean(data.get("quote_number", "unknown"))
    pdf_name = _make_pdf_name(q_num)
    rows     = _build_rows(data, pdf_name, pdf_text=text)
    if not rows:
        raise ValueError(f"No line items found in quote {q_num}")
    return rows, pdf_name


# ── Public entry points ───────────────────────────────────────────────────────

def process_pdf_file(uploaded_file):
    pdf_bytes = uploaded_file.read()
    rows, pdf_name = _parse_quote_pdf(pdf_bytes)
    return {"rows": rows, "pdfs": [(pdf_name, pdf_bytes)]}


def process_msg_file(uploaded_file):
    if extract_msg is None:
        raise RuntimeError("extract-msg is not installed")
    temp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(temp_dir, "input.msg")
    msg = None
    try:
        with open(tmp_path, "wb") as f:
            f.write(uploaded_file.read())
        msg = extract_msg.Message(tmp_path)
        rows, pdfs = [], []
        for att in msg.attachments:
            filename = att.longFilename or att.shortFilename or ""
            if not filename.lower().endswith(".pdf"):
                continue
            pdf_bytes = att.data
            if _is_quote_pdf(filename):
                pdf_rows, pdf_name = _parse_quote_pdf(pdf_bytes)
                rows.extend(pdf_rows)
                pdfs.append((pdf_name, pdf_bytes))
            else:
                pdfs.append((filename, pdf_bytes))
        if not rows:
            raise ValueError("No main quote PDF found (expected 'Quote XXXXXX.pdf')")
        return {"rows": rows, "pdfs": pdfs}
    finally:
        try:
            if msg: msg.close()
        except Exception: pass
        try: os.remove(tmp_path)
        except Exception: pass
        try: shutil.rmtree(temp_dir)
        except Exception: pass
        gc.collect()


def process_uploaded_file(uploaded_file):
    name = uploaded_file.name.lower()
    if name.endswith(".msg"):   return process_msg_file(uploaded_file)
    if name.endswith(".pdf"):   return process_pdf_file(uploaded_file)
    raise ValueError("Please upload only .msg or .pdf files")
