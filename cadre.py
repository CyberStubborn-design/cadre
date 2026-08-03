"""
Cadre Wire Group - Quote Parser
Uses extract-msg + pymupdf/pdfminer + Groq AI (cloud)

Groq setup:
  1. Get an API key: https://console.groq.com/keys
  2. Add it to .streamlit/secrets.toml:
       [groq]
       api_key = "gsk_..."
       model   = "llama-3.3-70b-versatile"
  3. Or set env vars: GROQ_API_KEY, GROQ_MODEL
"""

import re
import os
import json
import tempfile
import shutil
import gc

try:
    import pdfplumber
    def _pdf_to_text(pdf_bytes):
        from io import BytesIO
        with pdfplumber.open(BytesIO(pdf_bytes)) as doc:
            return "\n".join(page.extract_text() or "" for page in doc.pages)
except ImportError:
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

import time

GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_TIMEOUT_SECONDS = int(os.environ.get("GROQ_TIMEOUT_SECONDS", "120"))
GROQ_MAX_RETRIES = 4


def _get_groq_credentials():
    """Read the Groq API key/model from Streamlit secrets, falling back to env vars."""
    api_key = None
    model = None
    try:
        import streamlit as st
        api_key = st.secrets.get("groq", {}).get("api_key")
        model = st.secrets.get("groq", {}).get("model")
    except Exception:
        pass
    api_key = api_key or os.environ.get("GROQ_API_KEY", "")
    model = model or GROQ_MODEL
    if not api_key:
        raise RuntimeError(
            "Groq API key not found. Add it to .streamlit/secrets.toml under "
            "[groq] api_key = \"gsk_...\", or set the GROQ_API_KEY environment variable."
        )
    return api_key, model


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


def _trim_pdf_text(text, max_chars=8000):
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
        raise ValueError(f"Groq did not return JSON. Response starts with: {raw[:300]!r}")

    return json.loads(raw[start:end + 1])


def _extract_with_groq(pdf_text):
    """Extract quote fields using the Groq cloud API (llama-3.3-70b-versatile by default)."""
    from groq import Groq

    api_key, model = _get_groq_credentials()
    pdf_text = _trim_pdf_text(pdf_text)
    client = Groq(api_key=api_key, timeout=GROQ_TIMEOUT_SECONDS)

    last_exc = None
    for attempt in range(GROQ_MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": PROMPT + pdf_text}],
                temperature=0,
                max_tokens=4096,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content
            if not raw:
                raise ValueError("Groq returned an empty response")
            return _extract_json_object(raw)
        except Exception as exc:
            last_exc = exc
            status = getattr(exc, "status_code", None)
            is_rate_limit = status == 429 or "rate_limit" in str(exc).lower()
            if is_rate_limit and attempt < GROQ_MAX_RETRIES - 1:
                time.sleep(2 ** attempt)  # exponential backoff: 1s, 2s, 4s, 8s
                continue
            break

    raise RuntimeError(f"Groq extraction failed: {last_exc}") from last_exc


def _extract_prices_from_text(pdf_text):
    """
    Extract {item_id: (unit_price, extension)} directly from PDF text using regex.
    Handles fitz EAC (same-line), fitz MFT (cross-line), and pdfminer formats.
    Kept only as a fallback for older/other PDF layouts — see
    _parse_line_items_deterministic() for the primary, more reliable parser.
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


# ── Deterministic line-item parser (primary path — no AI/truncation risk) ────
#
# On a properly-ordered extraction (pdfplumber), every Cadre quote line looks like:
#   "<line#> <item_id> <qty> <UNIT> <price><UNIT2> <extension-or-'Canceled'>"
# e.g. "1 COP3.06.GREEN 40 FT 1,970.00000MFT 78.80"
#      "6 FUSE.GMT-DUMMY 20 EAC 2.50000EAC Canceled"   (order canceled — no extension)
_ITEM_LINE_RE = re.compile(
    r'^(?P<line>\d{1,3})\s+(?P<item_id>\S+)\s+(?P<qty>[\d,]+)\s+(?P<qty_unit>[A-Za-z]{1,5})\s+'
    r'(?P<price>[\d,]+\.\d+)(?P<price_unit>[A-Za-z]{1,5})\s+(?P<ext>Canceled|[\d,]+\.\d{1,2})\s*$'
)

# Lines that show up between/around a line item's description due to page breaks —
# skipped when stitching a multi-line description back together.
_BOILERPLATE_SKIP_RES = [
    re.compile(r'^Page\s+\d+\s+of\s+\d+'),
    re.compile(r'^Quote$'),
    re.compile(r'^Quote\s+\S+\s+Date\s'),
    re.compile(r'^Customer\s+\S+$'),
    re.compile(r'^Contact\s'),
    re.compile(r'^Salesperson\s'),
    re.compile(r'^Line\s+Item\s+Ordered\s+Price\s+Extension$'),
]
_TOTALS_STOP_RES = [re.compile(r'^Product\b'), re.compile(r'^Tax\b'), re.compile(r'^Total\b')]

_TOTALS_RE = {
    "product": re.compile(r'^Product\s+([\d,]+\.\d{2})'),
    "tax":     re.compile(r'^Tax\s+([\d,]+\.\d{2})'),
    "total":   re.compile(r'^Total\s+([\d,]+\.\d{2})'),
}


def _parse_line_items_deterministic(pdf_text):
    """
    Parse every line item directly from the PDF text via regex — guarantees the
    row count matches the PDF exactly (including canceled lines, which carry no
    extension). Returns [] if the text doesn't match this layout, so callers can
    fall back to the AI-provided line items.
    """
    lines = [ln.strip() for ln in pdf_text.split("\n")]
    n = len(lines)
    items = []

    for i, ln in enumerate(lines):
        m = _ITEM_LINE_RE.match(ln)
        if not m:
            continue

        ext_raw = m.group("ext")
        canceled = ext_raw.lower() == "canceled"

        desc_parts = []
        j = i + 1
        while j < n:
            l2 = lines[j]
            if not l2:
                j += 1
                continue
            if _ITEM_LINE_RE.match(l2) or any(p.match(l2) for p in _TOTALS_STOP_RES):
                break
            if any(p.match(l2) for p in _BOILERPLATE_SKIP_RES):
                j += 1
                continue
            desc_parts.append(l2)
            j += 1

        desc = " ".join(desc_parts).strip()
        if canceled:
            desc = f"{desc} [CANCELED]".strip() if desc else "[CANCELED]"

        items.append({
            "item_id":    m.group("item_id"),
            "item_desc":  desc,
            "unit_price": float(m.group("price").replace(",", "")),
            "extension":  0.0 if canceled else float(ext_raw.replace(",", "")),
        })

    return items


def _parse_totals_deterministic(pdf_text):
    """Parse Product/Tax/Total straight from the PDF text. Returns {} if not found."""
    totals = {}
    for ln in pdf_text.split("\n"):
        ln = ln.strip()
        for key, pattern in _TOTALS_RE.items():
            m = pattern.match(ln)
            if m:
                totals[key] = float(m.group(1).replace(",", ""))
    return totals


def _build_rows(data, pdf_name, pdf_text=""):
    salesperson = _clean(data.get("salesperson", ""))

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

    # Primary path: parse every line item directly out of the PDF text. This
    # guarantees the row count matches the PDF (including canceled lines,
    # which the AI can drop since they have no numeric extension) and is
    # immune to the AI's input-length truncation.
    det_items = _parse_line_items_deterministic(pdf_text) if pdf_text else []

    if det_items:
        line_items = det_items
        tax_amount = _parse_totals_deterministic(pdf_text).get("tax", data.get("tax"))
    else:
        # Fallback: unfamiliar layout — use the AI's own line_items, with the
        # older regex price map overriding its unit_price/extension where it matches.
        price_map = _extract_prices_from_text(pdf_text) if pdf_text else {}
        line_items = []
        for item in data.get("line_items", []):
            item_id = _clean(item.get("item_id", ""))
            if item_id in price_map:
                unit_price, total_sales = price_map[item_id]
            else:
                unit_price, total_sales = item.get("unit_price", ""), item.get("extension", "")
            line_items.append({
                "item_id": item_id,
                "item_desc": item.get("item_desc", ""),
                "unit_price": unit_price,
                "extension": total_sales,
            })
        # The AI prompt asks it to include Tax as its own line item already —
        # don't double it up with the tax_amount block below.
        tax_amount = None if any(_clean(i["item_id"]).lower() == "tax" for i in line_items) else data.get("tax")

    rows = []
    for item in line_items:
        row = {
            **base,
            "item_id":    _clean(item.get("item_id", "")),
            "item_desc":  _clean(item.get("item_desc", "")),
            "Unit Price": item.get("unit_price", ""),
            "TotalSales": item.get("extension", ""),
        }
        rows.append({h: row.get(h, "") for h in HEADERS})

    # Tax as its own row (skip if the AI already included one and we have no
    # deterministic items to dedupe against — det_items path never includes Tax).
    if tax_amount not in (None, ""):
        rows.append({
            **{h: base.get(h, "") for h in HEADERS},
            "item_id": "Tax", "item_desc": "", "Unit Price": tax_amount, "TotalSales": tax_amount,
        })

    return rows


def _parse_quote_pdf(pdf_bytes):
    text = _pdf_to_text(pdf_bytes)
    if not text.strip():
        raise ValueError("No text could be extracted from this PDF")
    data     = _extract_with_groq(text)
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
