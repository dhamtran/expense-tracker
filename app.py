import io
import json
import pandas as pd
import streamlit as st
from google import genai
from google.genai import types

# Page Config
st.set_page_config(
    page_title="IRAS Expense Tracker", page_icon="🧾", layout="wide"
)

st.title("🧾 IRAS-Compliant Expense Parser")
st.caption("Custom Expensify replacement powered by Gemini 2.5")

# Sidebar - API Key Management
with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Gemini API Key", type="password")
    st.markdown("---")
    st.markdown("### Approved Categories")
    categories = [
        "Travel & Transport",
        "Accommodation",
        "Meals & Entertainment",
        "Office & Supplies",
        "Communication",
        "Professional Fees",
        "Marketing & Business Dev",
        "Utilities & Premises",
        "Staff & Welfare",
        "Bank & Finance",
        "Other Business Costs",
        "Personal / Non-Deductible",
    ]
    st.write(", ".join(categories))

# Initialize Ledger Session State
if "ledger" not in st.session_state:
    st.session_state.ledger = pd.DataFrame(
        columns=[
            "Date",
            "Merchant",
            "Original Currency",
            "Original Amount",
            "Exchange Rate",
            "Amount (SGD)",
            "GST Amt (SGD)",
            "GST Rate",
            "Category",
            "Payment Method",
            "Purpose/Notes",
            "Status",
        ]
    )


# Function to process receipt with Gemini API
def parse_receipt(file_bytes, mime_type, api_key):
    client = genai.Client(api_key=api_key)

    prompt = """
    Parse this receipt and extract structured financial data strictly following Singapore IRAS guidelines:
    
    Rules:
    1. Date: DD/MM/YYYY format.
    2. Merchant: Full official merchant name.
    3. Original Currency: 3-letter currency code (e.g., SGD, USD, EUR, GBP).
    4. Original Amount: Total numeric amount paid.
    5. Exchange Rate: Rate applied to convert to SGD on transaction date. If SGD, use 1.0.
    6. Amount (SGD) = Original Amount * Exchange Rate.
    7. GST Rate: Must be "9%" (Singapore Standard), "0%" (Overseas/No GST), or "Exempt".
    8. GST Amt (SGD): If GST Rate is "9%", calculate (Amount SGD / 1.09) * 0.09 rounded to 2 decimal places. If 0% or Exempt, return 0.00.
    9. Category: STRICTLY choose ONE from this list:
       ['Travel & Transport', 'Accommodation', 'Meals & Entertainment', 'Office & Supplies', 'Communication', 'Professional Fees', 'Marketing & Business Dev', 'Utilities & Premises', 'Staff & Welfare', 'Bank & Finance', 'Other Business Costs', 'Personal / Non-Deductible']
    10. Payment Method: Cash, Card, PayNow, Bank Transfer, etc.
    11. Purpose/Notes: Concise business purpose description.
    12. Status: "Unreviewed"
    """

    response_schema = {
        "type": "OBJECT",
        "properties": {
            "Date": {"type": "STRING"},
            "Merchant": {"type": "STRING"},
            "Original Currency": {"type": "STRING"},
            "Original Amount": {"type": "NUMBER"},
            "Exchange Rate": {"type": "NUMBER"},
            "Amount (SGD)": {"type": "NUMBER"},
            "GST Amt (SGD)": {"type": "NUMBER"},
            "GST Rate": {"type": "STRING"},
            "Category": {"type": "STRING"},
            "Payment Method": {"type": "STRING"},
            "Purpose/Notes": {"type": "STRING"},
            "Status": {"type": "STRING"},
        },
        "required": [
            "Date",
            "Merchant",
            "Original Currency",
            "Original Amount",
            "Exchange Rate",
            "Amount (SGD)",
            "GST Amt (SGD)",
            "GST Rate",
            "Category",
            "Payment Method",
            "Purpose/Notes",
            "Status",
        ],
    }

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
            prompt,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=0.1,
        ),
    )

    return json.loads(response.text)


# Upload Section
uploaded_files = st.file_uploader(
    "Upload Receipt (PDF or Image)",
    type=["pdf", "png", "jpg", "jpeg"],
    accept_multiple_files=True,
)

if uploaded_files:
    if not api_key:
        st.warning("Please enter your Gemini API Key in the sidebar to proceed.")
    else:
        if st.button("Process Receipts"):
            for file in uploaded_files:
                with st.spinner(f"Parsing {file.name}..."):
                    try:
                        bytes_data = file.read()
                        extracted_data = parse_receipt(
                            bytes_data, file.type, api_key
                        )

                        # Duplicate Checking Strategy
                        is_duplicate = False
                        if not st.session_state.ledger.empty:
                            duplicate_match = st.session_state.ledger[
                                (
                                    st.session_state.ledger["Date"]
                                    == extracted_data["Date"]
                                )
                                & (
                                    st.session_state.ledger["Merchant"]
                                    == extracted_data["Merchant"]
                                )
                                & (
                                    st.session_state.ledger["Original Amount"]
                                    == extracted_data["Original Amount"]
                                )
                            ]
                            if not duplicate_match.empty:
                                is_duplicate = True

                        if is_duplicate:
                            extracted_data["Status"] = "Potential Duplicate"

                        # Append to DataFrame
                        new_row = pd.DataFrame([extracted_data])
                        st.session_state.ledger = pd.concat(
                            [st.session_state.ledger, new_row],
                            ignore_index=True,
                        )
                        st.success(f"Successfully processed {file.name}")

                    except Exception as e:
                        st.error(f"Error processing {file.name}: {e}")

# Interactive Data Table & Verification
st.markdown("---")
st.subheader("📊 Expense Ledger")

if not st.session_state.ledger.empty:
    # Editable Dataframe
    edited_df = st.data_editor(
        st.session_state.ledger,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "GST Rate": st.column_config.SelectboxColumn(
                options=["9%", "0%", "Exempt"]
            ),
            "Category": st.column_config.SelectboxColumn(options=categories),
            "Status": st.column_config.SelectboxColumn(
                options=["Unreviewed", "Reviewed", "Potential Duplicate"]
            ),
        },
    )

    # Sync back edits
    st.session_state.ledger = edited_df

    # Summary Statistics
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(
            "Total Amount (SGD)",
            f"${st.session_state.ledger['Amount (SGD)'].sum():,.2f}",
        )
    with col2:
        st.metric(
            "Total Claimable GST (SGD)",
            f"${st.session_state.ledger['GST Amt (SGD)'].sum():,.2f}",
        )
    with col3:
        st.metric("Total Records", len(st.session_state.ledger))

    # Export Section
    st.markdown("### Export Data")
    csv_data = st.session_state.ledger.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="📥 Download Monthly CSV Export",
        data=csv_data,
        file_name="iras_expense_export.csv",
        mime="text/csv",
    )
else:
    st.info("No expense data recorded yet. Upload receipts above to begin.")