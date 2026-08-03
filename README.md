# ⚡ Cadre Quote Processing Agent

AI-powered Streamlit app that monitors your Outlook inbox, extracts Sales Quote PDFs from `.msg` files, parses them using **Groq AI** (Llama 3.3 70B), and saves structured data to Excel — matching your existing `SampleCadre.xlsx` schema exactly.

---

## Features

| Feature | Description |
|---|---|
| 📊 Dashboard | KPI cards, sales-by-customer chart, searchable quote table, download Excel |
| 📤 Upload | Drag-and-drop `.msg` files, preview extracted line items, save with one click |
| 📬 Inbox Monitor | Auto-polls Outlook every N seconds, marks emails read, deduplicates by quote # |
| ⚙️ Settings | Configure Groq API key, model, Outlook credentials, output path, poll interval |

---

## Quick start

### 1. Clone the repo
```bash
git clone https://github.com/YOUR_ORG/cadre-quote-agent.git
cd cadre-quote-agent
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure secrets
```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Edit .streamlit/secrets.toml with your keys
```

### 4. Run
```bash
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501)

---

## Configuration

All settings are available in the **⚙️ Settings** page of the app, or via `.streamlit/secrets.toml`:

```toml
[groq]
api_key = "gsk_..."
model   = "llama-3.3-70b-versatile"   # fastest + most accurate

[outlook]
email    = "cadre.quote@distributor-systems.com"
password = "your-app-password"         # use App Password for MFA accounts

[output]
xlsx_path     = "data/cadre_quotes.xlsx"
poll_interval = 60                     # seconds between inbox checks
```

> **Microsoft 365 + MFA**: generate an App Password at  
> [account.microsoft.com → Security → App passwords](https://account.microsoft.com/security)

---

## Excel column mapping

| Column | Source |
|---|---|
| `ReferralEmail` | Sender's `@cadrewire.com` email from MSG headers |
| `Brand` | Always `"Cadre Wire Group"` |
| `QuoteNumber` | PDF header |
| `QuoteDate` | PDF header (converted to Excel date) |
| `Company` | "Quoted For" customer name |
| `FirstName` / `LastName` | Contact on PDF |
| `ContactEmail` / `ContactPhone` | From PDF or email |
| `Address`, `City`, `State`, `ZipCode` | Customer billing address |
| `item_id` | Part number (e.g. `HS.1635F1-C48`) |
| `item_desc` | Full description + stock notes |
| `Unit Price` | Per-unit price (MFT rate kept as-is for cable) |
| `TotalSales` | Line extension |
| `QuoteValidDate` | "Quote Good Through" date |
| `CustomerNumber` | Account number (e.g. `100447`) |
| `PDF` | `Cadre Wire Group_{QuoteNumber}.pdf` |

---

## Project structure

```
cadre-quote-agent/
├── app.py                      # Streamlit entry point + sidebar nav
├── requirements.txt
├── .gitignore
├── .streamlit/
│   ├── config.toml             # Theme (Cadre blue)
│   └── secrets.toml.example    # Template — copy to secrets.toml
├── pages/
│   ├── dashboard.py            # 📊 KPIs, charts, table, download
│   ├── upload.py               # 📤 Manual .msg upload + preview
│   ├── monitor.py              # 📬 Start/stop inbox monitor
│   └── settings.py             # ⚙️  API keys, Outlook, output config
├── utils/
│   ├── extractor.py            # Groq AI extraction + row builder
│   ├── excel_io.py             # Excel read/write (openpyxl + pandas)
│   ├── monitor.py              # Background Outlook polling thread
│   └── state.py                # Streamlit session state manager
└── data/                       # Output Excel files (gitignored)
```

---

## Deploying to Streamlit Cloud

1. Push this repo to GitHub (make sure `secrets.toml` is in `.gitignore`)
2. Go to [share.streamlit.io](https://share.streamlit.io) → New app → select repo
3. Add secrets in the Streamlit Cloud dashboard under **Settings → Secrets**:

```toml
[groq]
api_key = "gsk_..."

[outlook]
email    = "cadre.quote@distributor-systems.com"
password = "your-app-password"
```

> Note: The inbox monitor background thread works on local/server deployments.  
> On Streamlit Cloud, use the **Upload** page for manual processing.

---

## Edge cases handled

- **Cable priced per MFT** — unit price stored as MFT rate, extension calculated correctly
- **Tax line items** — included as a row with `item_id = "Tax"`
- **Non-standard PDFs** (e.g. Burndy spec sheets) — agent logs a warning, saves what it can
- **Duplicate quotes** — checked before saving; user prompted to overwrite or skip
- **Multiple salespersons** — sender email auto-detected from MSG headers
- **MFA / App Passwords** — standard exchangelib credential support

---

## License
MIT
