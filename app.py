import json
import pandas as pd
import streamlit as st
from google import genai
from google.genai import types
from streamlit_gsheets import GSheetsConnection

# 1. UI Configuration
st.set_page_config(page_title="MOWIN Expenses", page_icon="🟢", layout="wide")

st.markdown("""
<style>
    div.stButton > button:first-child {
        background-color: #004D40; color: white; border-radius: 8px;
        padding: 10px 24px; border: none; font-weight: bold; width: 100%;
    }
    div.stButton > button:hover { background-color: #00332a; color: white; }
    .metric-card {
        background-color: #f8f9fa; border-left: 5px solid #004D40;
        padding: 20px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .metric-label { font-size: 12px; color: #6a737d; text-transform: uppercase; font-weight: 700; }
    .metric-value { font-size: 28px; color: #004D40; font-weight: 900; }
</style>
""", unsafe_allow_html=True)

st.title("🟢 MOWIN Expenses")

# Initialize File Uploader Session Key
if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0

# 2. Permanent Cloud Storage Connection
conn = st.connection("gsheets", type=GSheetsConnection)

@st.cache_data(ttl=5) 
def load_ledger():
    try:
        # Added ttl=5 here to force the Google Sheets connection to refresh
        df = conn.read(worksheet=0, usecols=list(range(12)), ttl=5)
        return df.dropna(how="all")
    except Exception:
        return pd.DataFrame(columns=[
            "Date", "Merchant", "Original Currency", "Original Amount", 
            "Exchange Rate", "Amount (SGD)", "GST Amt (SGD)", "GST Rate", 
            "Category", "Payment Method", "Purpose/Notes", "Status"
        ])

ledger_df = load_ledger()

# Helper Function: Duplicate Checking
def is_duplicate_record(new_item, existing_df, current_batch):
    new_date = str(new_item.get("Date", "")).strip()
    new_merchant = str(new_item.get("Merchant", "")).strip().lower()
    try:
        new_amount = float(new_item.get("Original Amount", 0))
    except (ValueError, TypeError):
        new_amount = 0.0

    # 1. Check against existing Google Sheet records
    if not existing_df.empty:
        for _, row in existing_df.iterrows():
            row_date = str(row.get("Date", "")).strip()
            row_merchant = str(row.get("Merchant", "")).strip().lower()
            try:
                row_amount = float(row.get("Original Amount", 0))
            except (ValueError, TypeError):
                row_amount = 0.0
            
            if new_date == row_date and new_merchant == row_merchant and abs(new_amount - row_amount) < 0.01:
                return True

    # 2. Check against items already processed in current upload batch
    for item in current_batch:
        b_date = str(item.get("Date", "")).strip()
        b_merchant = str(item.get("Merchant", "")).strip().lower()
        try:
            b_amount = float(item.get("Original Amount", 0))
        except (ValueError, TypeError):
            b_amount = 0.0
        
        if new_date == b_date and new_merchant == b_merchant and abs(new_amount - b_amount) < 0.01:
            return True

    return False

# 3. Intuitive Tabbed Interface
tab1, tab2 = st.tabs(["📷 SmartScan", "📊 Cloud Ledger"])

with tab1:
    st.subheader("Upload Receipts")
    uploaded_files = st.file_uploader(
        "Drag & drop images or PDFs here", 
        type=["pdf", "png", "jpg", "jpeg"], 
        accept_multiple_files=True,
        key=f"uploader_{st.session_state.uploader_key}"
    )
    
    if st.button("Process & Save to Cloud"):
        if not uploaded_files:
            st.warning("Please upload a receipt first.")
        else:
            try:
                api_key = st.secrets["GEMINI_API_KEY"]
                client = genai.Client(api_key=api_key)
                
                new_rows = []
                duplicates_found = 0
                
                for file in uploaded_files:
                    with st.spinner(f"Scanning {file.name}..."):
                        prompt = """
                        Parse this receipt into JSON strictly following Singapore IRAS guidelines:
                        Rules:
                        1. Date: DD/MM/YYYY
                        2. Merchant: Full official name
                        3. Original Currency: 3-letter code
                        4. Original Amount: Total numeric amount
                        5. Exchange Rate: Rate applied to convert to SGD (SGD = 1.0)
                        6. Amount (SGD): Original Amount * Exchange Rate
                        7. GST Rate: Must be "9%", "0%", or "Exempt"
                        8. GST Amt (SGD): If GST Rate is "9%", calculate (Amount SGD / 1.09) * 0.09. If 0% or Exempt, return 0.00
                        9. Category: STRICTLY ONE of ['Travel & Transport', 'Accommodation', 'Meals & Entertainment', 'Office & Supplies', 'Communication', 'Professional Fees', 'Marketing & Business Dev', 'Utilities & Premises', 'Staff & Welfare', 'Bank & Finance', 'Other Business Costs', 'Personal / Non-Deductible']
                        10. Payment Method: Cash, Card, PayNow, Bank Transfer, etc.
                        11. Purpose/Notes: Brief business purpose
                        12. Status: "Unreviewed"
                        """
                        response_schema = {
                            "type": "OBJECT",
                            "properties": {
                                "Date": {"type": "STRING"}, "Merchant": {"type": "STRING"},
                                "Original Currency": {"type": "STRING"}, "Original Amount": {"type": "NUMBER"},
                                "Exchange Rate": {"type": "NUMBER"}, "Amount (SGD)": {"type": "NUMBER"},
                                "GST Amt (SGD)": {"type": "NUMBER"}, "GST Rate": {"type": "STRING"},
                                "Category": {"type": "STRING"}, "Payment Method": {"type": "STRING"},
                                "Purpose/Notes": {"type": "STRING"}, "Status": {"type": "STRING"}
                            },
                            "required": ["Date", "Merchant", "Original Currency", "Original Amount", "Exchange Rate", "Amount (SGD)", "GST Amt (SGD)", "GST Rate", "Category", "Payment Method", "Purpose/Notes", "Status"]
                        }
                        
                        response = client.models.generate_content(
                            model="gemini-3.6-flash",
                            contents=[types.Part.from_bytes(data=file.read(), mime_type=file.type), prompt],
                            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=response_schema, temperature=0.1)
                        )
                        
                        extracted_data = json.loads(response.text)
                        
                        # Run Duplicate Check
                        if is_duplicate_record(extracted_data, ledger_df, new_rows):
                            extracted_data["Status"] = "Potential Duplicate"
                            duplicates_found += 1
                        
                        new_rows.append(extracted_data)
                
                # Append to Google Sheet permanently
                if new_rows:
                    new_data_df = pd.DataFrame(new_rows)
                    updated_df = pd.concat([ledger_df, new_data_df], ignore_index=True)
                    conn.update(worksheet=0, data=updated_df)
                    
                    if duplicates_found > 0:
                        st.toast(f"⚠️ Saved! {duplicates_found} potential duplicate(s) flagged.", icon="⚠️")
                    else:
                        st.toast("✅ Successfully synced to your master Google Sheet!", icon="✅")
                    
                    # Reset Uploader State and Refresh
                    st.session_state.uploader_key += 1
                    st.cache_data.clear()
                    st.rerun()

            except KeyError:
                st.error("🚨 Missing Cloud Secret. Please configure your App Secrets in the Streamlit Dashboard.")
            except Exception as e:
                st.error(f"Error processing: {e}")

with tab2:
    # Refresh Button Row
    col_empty, col_btn = st.columns([4, 1])
    with col_btn:
        if st.button("🔄 Refresh Data", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    # Top Dashboard Metrics
    col1, col2, col3 = st.columns(3)
    total_sgd = ledger_df["Amount (SGD)"].sum() if not ledger_df.empty else 0.0
    gst_sgd = ledger_df["GST Amt (SGD)"].sum() if not ledger_df.empty else 0.0
    
    col1.markdown(f'<div class="metric-card"><div class="metric-label">Total Spend (SGD)</div><div class="metric-value">${total_sgd:,.2f}</div></div>', unsafe_allow_html=True)
    col2.markdown(f'<div class="metric-card"><div class="metric-label">Claimable GST (SGD)</div><div class="metric-value">${gst_sgd:,.2f}</div></div>', unsafe_allow_html=True)
    col3.markdown(f'<div class="metric-card"><div class="metric-label">Total Receipts</div><div class="metric-value">{len(ledger_df)}</div></div>', unsafe_allow_html=True)
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    if ledger_df.empty:
        st.info("No records found in your Google Sheet yet.")
    else:
        st.dataframe(ledger_df, use_container_width=True, hide_index=True)
