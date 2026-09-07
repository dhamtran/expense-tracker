import base64
import requests
import json
import time
import io
import pandas as pd
import streamlit as st
from PIL import Image
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

if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0
if "camera_key" not in st.session_state:
    st.session_state.camera_key = 0
if "camera_receipts" not in st.session_state:
    st.session_state.camera_receipts = []

# 2. Permanent Cloud Storage Connection
conn = st.connection("gsheets", type=GSheetsConnection)

@st.cache_data(ttl=5) 
def load_ledger():
    try:
        df = conn.read(worksheet=0, usecols=list(range(13)), ttl=5)
        df = df.dropna(how="all")
        
        # Ensure "Receipt Link" exists and is formatted as text
        if "Receipt Link" not in df.columns:
            df["Receipt Link"] = ""
        df["Receipt Link"] = df["Receipt Link"].fillna("").astype(str)
        
        return df
    except Exception:
        return pd.DataFrame(columns=[
            "Date", "Merchant", "Original Currency", "Original Amount", 
            "Exchange Rate", "Amount (SGD)", "GST Amt (SGD)", "GST Rate", 
            "Category", "Payment Method", "Purpose/Notes", "Status", "Receipt Link"
        ])

ledger_df = load_ledger()

# Image Compression Engine
def compress_file(file_obj):
    file_bytes = file_obj.read()
    mime_type = getattr(file_obj, "type", "image/jpeg")
    filename = getattr(file_obj, "name", f"receipt_{int(time.time())}.jpg")
    
    if mime_type.startswith("image/"):
        try:
            img = Image.open(io.BytesIO(file_bytes))
            img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=75, optimize=True)
            return buffer.getvalue(), "image/jpeg", filename
        except Exception:
            pass
    return file_bytes, mime_type, filename

# New Google Drive Upload Engine (Bypasses Quota via Apps Script)
def upload_to_drive(file_bytes, filename, mime_type):
    try:
        script_url = st.secrets["APPS_SCRIPT_URL"]
        payload = {
            "base64": base64.b64encode(file_bytes).decode('utf-8'),
            "filename": filename,
            "mimeType": mime_type
        }
        
        response = requests.post(script_url, json=payload)
        result = response.json()
        
        if result.get("status") == "success":
            return result.get("link", "")
        else:
            st.error(f"⚠️ Apps Script Error: {result.get('message')}")
            return ""
            
    except Exception as e:
        st.error(f"⚠️ Drive Upload Error: {e}")
        return ""

def is_duplicate_record(new_item, existing_df, current_batch):
    new_date = str(new_item.get("Date", "")).strip()
    new_merchant = str(new_item.get("Merchant", "")).strip().lower()
    try:
        new_amount = float(new_item.get("Original Amount", 0))
    except (ValueError, TypeError):
        new_amount = 0.0

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

# 3. Tabbed Interface
tab1, tab2 = st.tabs(["📷 SmartScan", "📊 Cloud Ledger"])

with tab1:
    st.subheader("Upload or Snap Receipts")
    col_up, col_cam = st.columns(2)
    
    with col_up:
        uploaded_files = st.file_uploader(
            "Drag & drop files here", 
            type=["pdf", "png", "jpg", "jpeg"], 
            accept_multiple_files=True,
            key=f"uploader_{st.session_state.uploader_key}"
        )
        
    with col_cam:
        captured_image = st.camera_input("Take a photo", key=f"cam_{st.session_state.camera_key}")
        if captured_image is not None:
            st.session_state.camera_receipts.append(captured_image)
            st.session_state.camera_key += 1
            st.rerun()

    if st.session_state.camera_receipts:
        st.info(f"📸 {len(st.session_state.camera_receipts)} photo(s) sitting in your camera queue.")
        if st.button("Clear Camera Queue"):
            st.session_state.camera_receipts = []
            st.rerun()
            
    all_files = (uploaded_files or []) + st.session_state.camera_receipts
    
    if st.button("Process & Save to Cloud"):
        if not all_files:
            st.warning("Please upload a receipt or take a photo first.")
        else:
            try:
                api_key = st.secrets["GEMINI_API_KEY"]
                client = genai.Client(api_key=api_key)
                
                new_rows = []
                duplicates_found = 0
                
                for file_obj in all_files:
                    comp_bytes, comp_mime, comp_name = compress_file(file_obj)
                    
                    with st.spinner(f"Uploading & Scanning {comp_name}..."):
                        drive_link = upload_to_drive(comp_bytes, comp_name, comp_mime)
                        
                        prompt = """
                        Parse this receipt into JSON strictly following Singapore IRAS guidelines:
                        Rules:
                        1. Date: DD/MM/YYYY
                        2. Merchant: Full official name
                        3. Original Currency: 3-letter code
                        4. Original Amount: Total numeric amount
                        5. Exchange Rate: Rate applied to convert to SGD (SGD = 1.0)
                        6. Amount (SGD): Original Amount * Exchange Rate
                        7. GST RULE (CRITICAL COMPLIANCE): 
                           - OVERSEAS: If the receipt is from outside Singapore, the GST Rate MUST strictly be "Exempt".
                           - SINGAPORE: By IRAS law, a business can only charge GST if they print their "GST Registration Number" on the receipt. Scan the receipt for a GST No. If it is MISSING, you MUST set GST Rate to "Exempt" and GST Amt to 0.00, even if the merchant mistakenly printed a tax line. If a GST No. IS present, apply "9%" or "0%" as printed.
                        8. GST Amt (SGD): If GST Rate is "9%", calculate (Amount SGD / 1.09) * 0.09. If 0% or Exempt, return 0.00.
                        9. Category: STRICTLY ONE of ['Travel & Transport', 'Accommodation', 'Meals & Entertainment', 'Office & Supplies', 'Communication', 'Professional Fees', 'Marketing & Business Dev', 'Utilities & Premises', 'Staff & Welfare', 'Bank & Finance', 'Other Business Costs', 'Personal / Non-Deductible']
                        10. Payment Method: Cash, Card, PayNow, Bank Transfer, etc.
                        11. Purpose/Notes: Brief business purpose
                        12. Status: "Unreviewed"
                        """
                        
                        # Official schema format to prevent 400 Errors
                        official_schema = types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "Date": types.Schema(type=types.Type.STRING),
                                "Merchant": types.Schema(type=types.Type.STRING),
                                "Original Currency": types.Schema(type=types.Type.STRING),
                                "Original Amount": types.Schema(type=types.Type.NUMBER),
                                "Exchange Rate": types.Schema(type=types.Type.NUMBER),
                                "Amount (SGD)": types.Schema(type=types.Type.NUMBER),
                                "GST Amt (SGD)": types.Schema(type=types.Type.NUMBER),
                                "GST Rate": types.Schema(type=types.Type.STRING),
                                "Category": types.Schema(type=types.Type.STRING),
                                "Payment Method": types.Schema(type=types.Type.STRING),
                                "Purpose/Notes": types.Schema(type=types.Type.STRING),
                                "Status": types.Schema(type=types.Type.STRING),
                            },
                            required=["Date", "Merchant", "Original Currency", "Original Amount", "Exchange Rate", "Amount (SGD)", "GST Amt (SGD)", "GST Rate", "Category", "Payment Method", "Purpose/Notes", "Status"]
                        )
                        
                        # Enhanced Retry Engine (Catches 429 Rate Limits & 503 Overloads)
                        max_retries = 3
                        for attempt in range(max_retries):
                            try:
                                response = client.models.generate_content(
                                    model="gemini-3.6-flash",
                                    contents=[types.Part.from_bytes(data=comp_bytes, mime_type=comp_mime), prompt],
                                    config=types.GenerateContentConfig(
                                        response_mime_type="application/json", 
                                        response_schema=official_schema, 
                                        temperature=0.1
                                    )
                                )
                                extracted_data = json.loads(response.text)
                                extracted_data["Receipt Link"] = drive_link
                                
                                if is_duplicate_record(extracted_data, ledger_df, new_rows):
                                    extracted_data["Status"] = "Potential Duplicate"
                                    duplicates_found += 1
                                
                                new_rows.append(extracted_data)
                                break 
                                
                            except Exception as e:
                                err_msg = str(e)
                                if ("429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg) and attempt < max_retries - 1:
                                    st.toast("⏳ Free quota rate limit reached. Pausing 17s before retrying...", icon="⏱️")
                                    time.sleep(17) # Wait out the rate limit window
                                elif "503" in err_msg and attempt < max_retries - 1:
                                    time.sleep(3)
                                else:
                                    raise e

                    # Small delay between files to avoid rapid burst calls
                    time.sleep(2)
                
                if new_rows:
                    new_data_df = pd.DataFrame(new_rows)
                    updated_df = pd.concat([ledger_df, new_data_df], ignore_index=True)
                    conn.update(worksheet=0, data=updated_df)
                    
                    if duplicates_found > 0:
                        st.toast(f"⚠️ Saved! {duplicates_found} potential duplicate(s) flagged.", icon="⚠️")
                    else:
                        st.toast("✅ Successfully synced to your Master Google Sheet & Drive!", icon="✅")
                    
                    st.session_state.uploader_key += 1
                    st.session_state.camera_receipts = []
                    st.cache_data.clear()
                    st.rerun()

            except KeyError:
                st.error("🚨 Missing Cloud Secret. Please configure your App Secrets in the Streamlit Dashboard.")
            except Exception as e:
                st.error(f"Error processing: {e}")

with tab2:
    col_empty, col_btn = st.columns([4, 1])
    with col_btn:
        # Layout Warning Fix: Replaced use_container_width with width="stretch"
        if st.button("🔄 Refresh Data", width="stretch"):
            st.cache_data.clear()
            st.rerun()

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
        # Sanitize Receipt Link column to prevent Streamlit type-check errors
        ledger_df["Receipt Link"] = ledger_df["Receipt Link"].fillna("").astype(str)
        
        st.caption("💡 **Tip:** Edit cells directly. To delete a row, check the box on the left and click the 'Trash' icon on the top right.")
        
        # Layout Warning Fix: Replaced use_container_width with width="stretch"
        edited_ledger = st.data_editor(
            ledger_df, 
            width="stretch", 
            hide_index=True, 
            num_rows="dynamic",
            column_config={
                "Receipt Link": st.column_config.LinkColumn("Receipt Link", display_text="View Receipt 🔗")
            }
        )
        
        if st.button("💾 Save Ledger Changes to Cloud"):
            conn.update(worksheet=0, data=edited_ledger)
            st.toast("✅ Master Google Sheet updated!", icon="✅")
            st.cache_data.clear()
            st.rerun()
