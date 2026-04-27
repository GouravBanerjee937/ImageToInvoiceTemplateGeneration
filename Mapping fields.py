import os
import base64
import uuid
import json
from flask import Flask, render_template, request, redirect, url_for, send_from_directory
from werkzeug.utils import secure_filename
from openai import OpenAI
from playwright.sync_api import sync_playwright
from pdf2image import convert_from_path

# --- Configuration ---
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}

# WARNING: DO NOT hardcode API keys in production code. Use environment variables.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# --- Flask App Initialization ---
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- OpenAI Client Initialization ---
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)
else:
    client = None
    print("WARNING: OPENAI_API_KEY is not set. AI processing will fail.")

# --- Mazu Fields Definition ---
MAZU_FIELDS_RAW = """
Header	Billing Details	Name
Header	Billing Details	GSTIN / GST Number
Header	Billing Details	PAN
Header	Billing Details	Mobile Number
Header	Billing Details	Primary Contact Person
Header	Billing Details	Email Address
Header	Billing Details	Address
Header	Billing Details	City
Header	Billing Details	State
Header	Billing Details	Pincode
Header	Billing Details	Country
Header	Shipping Details	Name
Header	Shipping Details	GSTIN / GST Number
Header	Shipping Details	Mobile Number
Header	Shipping Details	Primary Contact Person
Header	Shipping Details	Email Address
Header	Shipping Details	Address
Header	Shipping Details	City
Header	Shipping Details	State
Header	Shipping Details	Pincode
Header	Shipping Details	Country
Header	(None)	Drug Lic. No.
Header	(None)	FSSAI
Header	(None)	IE Code
Header	(None)	Notes
Header	Transporter Details	Transporter
Header	Transporter Details	Vehicle No.
Header	Transporter Details	Transporter Doc No.
Header	Transporter Details	Transporter Doc Date
Header	Transporter Details	E-Way Bill No.
Header	Transporter Details	E-Way Bill Date
Header	(None)	Bill No.
Header	(None)	Bill Date
Header	(None)	Supplier Invoice Number
Header	(None)	Supplier Invoice Date
Header	(None)	Page No
Header	(None)	Due date
Header	(None)	Payment Terms
Header	(None)	Place of Supply
Header	(None)	Time
Header	(None)	Seconds
Header	(None)	Reverse Charge
Header	(None)	TDS
Header	(None)	Optional Field 2
Header	(None)	Optional Field 3
Header	(None)	Optional Field 4
Header	(None)	Optional Field 5
Header	(None)	Adjusted Voucher No.
Header	(None)	Adjusted Voucher Qty
Items	(None)	Sr.
Items	Item Description	Item Name
Items	Item Description	Print Name
Items	Item Description	Description
Items	(None)	Alias
Items	(None)	HSN/SAC code
Items	(None)	Qty
Items	(None)	Free Qty
Items	(None)	Unit
Items	(None)	Main Qty
Items	(None)	Alternate Qty
Items	(None)	Packaging Qty
Items	(None)	MRP
Items	(None)	Unit Price(Tax Inclusive)
Items	(None)	Unit Price
Items	(None)	Disc.
Items	(None)	Dis. Amount
Items	(None)	Net Price
Items	(None)	Taxable Amount
Items	(None)	Tax %
Items	(None)	Tax Amount
Items	(None)	Tax Rate & Amount
Items	(None)	IGST Rate
Items	(None)	IGST Amount
Items	(None)	IGST Rate & Amount
Items	(None)	CGST Rate
Items	(None)	CGST Amount
Items	(None)	CGST Rate & Amount
Items	(None)	SGST Rate
Items	(None)	SGST Amount
Items	(None)	SGST Rate & Amount
Items	(None)	Cess
Items	(None)	Cess Amount
Items	(None)	Add. Cess
Items	(None)	Add. Cess Amount
Items	(None)	Amount
Items	(None)	Bill Sundry
Items	(None)	Grand Total
Footer	(None)	Amount Paid (Settlement)
Footer	(None)	Invoice Balance
Footer	(None)	Ledger Balance
Footer	(None)	Invoice Amount in Words
Footer	(None)	Terms and Conditions
Footer	Bank Details	Bank
Footer	Bank Details	Account Number
Footer	Bank Details	IFSC
Footer	Bank Details	Branch
Footer	Bank Details	Name
Footer	(None)	Tax Summary
Footer	(None)	Payment QR Code
Footer	(None)	Signature
"""
MAZU_FIELDS_LIST = []
for line in MAZU_FIELDS_RAW.strip().split('\n'):
    parts = line.split('\t')
    if len(parts) == 3:
        header, sub_header, field_name = parts
        MAZU_FIELDS_LIST.append({
            "Header": header,
            "SubHeader": sub_header if sub_header != "(None)" else None,
            "FieldName": field_name
        })
MAZU_FIELDS_JSON = json.dumps(MAZU_FIELDS_LIST, indent=2)


# --- Helper Functions ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def encode_file_to_base64(filepath):
    with open(filepath, "rb") as file:
        return base64.b64encode(file.read()).decode('utf-8')


def clean_html_response(html_content):
    html_content = html_content.strip()
    if html_content.startswith("```html"):
        html_content = html_content[7:]
    elif html_content.startswith("```"):
        html_content = html_content[3:]

    if html_content.endswith("```"):
        html_content = html_content[:-3]

    html_start_idx = html_content.find("<!DOCTYPE html>")
    if html_start_idx == -1:
        html_start_idx = html_content.find("<html")

    if html_start_idx != -1:
        html_content = html_content[html_start_idx:]

    html_end_idx = html_content.rfind("</html>")
    if html_end_idx != -1:
        html_content = html_content[:html_end_idx + 7]

    return html_content.strip()

def generate_mapped_html(template_html_with_placeholders: str) -> str:
    """
    Sends the template HTML and Mazu fields to an LLM to generate an HTML
    showing the mapping of placeholders to Mazu fields.
    """
    if not client:
        return "<p>Error: OpenAI API key not configured. Cannot perform mapping.</p>"

    mapping_prompt = f"""
    You are an expert in invoice data mapping. Your task is to analyze an HTML invoice template containing placeholders (e.g., [COMPANY_NAME], [INVOICE_DATE]) and map these placeholders to a provided list of standard Mazu fields.

    Perform the following steps:
    1.  Identify all unique placeholders in the provided HTML template.
    2.  For each identified placeholder, find the BEST matching field from the MAZU_FIELDS_LIST provided below.
    3.  The matching should be intelligent and semantic, not just exact string matching. For example, "[VENDOR_NAME]" could map to "Name" under "Header > Billing Details", or "[TOTAL_AMOUNT]" could map to "Grand Total".
    4.  If a placeholder from the HTML template does NOT have a suitable match in the MAZU_FIELDS_LIST, clearly indicate it as "UNMAPPED".
    5.  Generate a NEW HTML document that visually represents the original template. In this new HTML:
        -   Next to each placeholder, display its mapped Mazu field (e.g., "[COMPANY_NAME] -> Header > Billing Details > Name").
        -   If a placeholder is UNMAPPED, display it as "[PLACEHOLDER_NAME] -> UNMAPPED".
        -   Use clear visual cues (e.g., different background colors, bold text) to distinguish mapped fields from unmapped fields.
        -   Include a summary section at the bottom of the HTML listing all placeholders found, their mapped Mazu field, and a separate list of all UNMAPPED placeholders.
        -   After the summary, add a FINAL section titled 'Unmapped Mazu Fields' and include a simple list (<ul> and <li>) of all fields from the original MAZU_FIELDS_LIST that were NOT used in any mapping.

    MAZU_FIELDS_LIST (JSON format):
    {MAZU_FIELDS_JSON}

    HTML Template with Placeholders:
    {template_html_with_placeholders}

    Return ONLY the raw HTML code for the mapped document, starting with <!DOCTYPE html> and ending with </html>. Do not include any introductory text, explanatory text, or markdown blocks like ```html.
    """

    try:
        response = client.chat.completions.create(
            model="gpt-5.4", # Using GPT-5.4 for this mapping task
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "text", "text": mapping_prompt}]
                }
            ],
            temperature=0.0,
        )

        raw_response_content = response.choices[0].message.content
        if raw_response_content:
            return clean_html_response(raw_response_content)
        else:
            return "<p>Error: AI did not return any content for mapping.</p>"

    except Exception as e:
        return f"<p>Error during AI mapping: {e}</p>"


# --- Routes ---
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload_file():
    """Handles file uploads and automatically processes them."""
    if 'file' not in request.files:
        return redirect(request.url)
    file = request.files['file']
    if file.filename == '' or not allowed_file(file.filename):
        return redirect(request.url)

    filename = secure_filename(file.filename)
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(file_path)

    if not client:
        return render_template('index.html', filename=filename, error="OpenAI API key not configured.")

    # Handle PDF conversion to Image
    if file_path.lower().endswith('.pdf'):
        try:
            images = convert_from_path(file_path, first_page=1, last_page=1)
            if not images:
                return render_template('index.html', filename=filename, error="Failed to convert PDF to image.")

            image_filename = f"temp_{uuid.uuid4().hex[:8]}.jpg"
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
            images[0].save(image_path, 'JPEG')

            base64_data = encode_file_to_base64(image_path)
            media_type = "image/jpeg"
            os.remove(image_path)
        except Exception as e:
            return render_template('index.html', filename=filename,
                                   error=f"PDF conversion error: {str(e)}. Make sure 'poppler' is installed on your system.")
    else:
        base64_data = encode_file_to_base64(file_path)
        ext = file_path.lower().rsplit('.', 1)[1]
        if ext in ['jpg', 'jpeg']:
            media_type = "image/jpeg"
        elif ext == 'png':
            media_type = "image/png"
        elif ext == 'gif':
            media_type = "image/gif"
        else:
            media_type = "image/jpeg"

    data_url = f"data:{media_type};base64,{base64_data}"

    # CALL 1: Original highly-accurate PDF reconstruction using GPT-4o
    prompt_1 = """
    Analyze the attached invoice. Generate a single, self-contained HTML file with inline CSS that visually replicates this document as closely as possible. 
    You MUST accurately recreate every single table, border, line, font size difference, and alignment.
    Do NOT simplify tables. If there is a table for line items, recreate it perfectly using <table>, <thead>, <tbody>, <tr>, <th>, and <td> with appropriate CSS borders and padding.
    Return ONLY the raw HTML code starting with <!DOCTYPE html> and ending with </html>. 
    Do not include any introductory text, explanatory text, or markdown blocks like ```html.
    """

    # CALL 2: Structure-only template generation using GPT-5.4
    prompt_2 = """
    Analyze the attached invoice. Generate a single, self-contained HTML file with inline CSS that visually replicates this document as closely as possible. 
    You MUST accurately recreate every single table, border, line, font size difference, and alignment.
    Do NOT simplify tables. If there is a table for line items, recreate it perfectly using <table>, <thead>, <tbody>, <tr>, <th>, and <td> with appropriate CSS borders and padding.
    
    BUT, replace all specific data values with generic placeholder key names (e.g., instead of "John Doe" use "[BUYER_NAME]", instead of "123 Main St" use "[BUYER_ADDRESS]", instead of "$100.00" use "[TOTAL_AMOUNT]").
    
    Return ONLY the raw HTML code starting with <!DOCTYPE html> and ending with </html>. 
    Do not include any introductory text, explanatory text, or markdown blocks like ```html.
    """

    generated_pdf_filename = None
    structured_pdf_filename = None
    mapped_pdf_filename = None
    ai_response = None
    structured_html = None
    mapped_html = None

    try:
        # ---------------------------------------------------------
        # TASK 1: Get the exact reconstruction from GPT-4o
        # ---------------------------------------------------------
        response_1 = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_1},
                        {"type": "image_url", "image_url": {"url": data_url}}
                    ]
                }
            ],
            temperature=0.0,
        )

        if response_1.choices[0].message.content:
            ai_response = clean_html_response(response_1.choices[0].message.content)
            
            if ai_response and "<html" in ai_response.lower():
                generated_pdf_filename = f"recreated_{uuid.uuid4().hex[:8]}.pdf"
                generated_pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], generated_pdf_filename)
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    page = browser.new_page()
                    page.set_content(ai_response)
                    page.pdf(path=generated_pdf_path, format="A4")
                    browser.close()
            else:
                ai_response = "Error: The AI response did not contain valid HTML for Task 1."
        else:
            ai_response = "Error: AI did not return any content for Task 1."

        # ---------------------------------------------------------
        # TASK 2: Get the structured template from GPT-5.4
        # ---------------------------------------------------------
        response_2 = client.chat.completions.create(
            model="gpt-5.4",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_2},
                        {"type": "image_url", "image_url": {"url": data_url}}
                    ]
                }
            ],
            temperature=0.0,
        )

        if response_2.choices[0].message.content:
            structured_html = clean_html_response(response_2.choices[0].message.content)
            
            if structured_html and "<html" in structured_html.lower():
                structured_pdf_filename = f"structured_{uuid.uuid4().hex[:8]}.pdf"
                structured_pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], structured_pdf_filename)
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    page = browser.new_page()
                    page.set_content(structured_html)
                    page.pdf(path=structured_pdf_path, format="A4")
                    browser.close()

                # ---------------------------------------------------------
                # TASK 3: Get the mapped HTML from GPT-5.4
                # ---------------------------------------------------------
                mapped_html = generate_mapped_html(structured_html)
                if mapped_html and "<html" in mapped_html.lower():
                    mapped_pdf_filename = f"mapped_{uuid.uuid4().hex[:8]}.pdf"
                    mapped_pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], mapped_pdf_filename)
                    with sync_playwright() as p:
                        browser = p.chromium.launch()
                        page = browser.new_page()
                        page.set_content(mapped_html)
                        page.pdf(path=mapped_pdf_path, format="A4")
                        browser.close()
                else:
                    mapped_html = "Error: The AI response did not contain valid HTML for Task 3."
            else:
                structured_html = "Error: The AI response did not contain valid HTML for Task 2."
        else:
            structured_html = "Error: AI did not return any content for Task 2."

    except Exception as e:
        if not ai_response:
            ai_response = f"An error occurred: {e}"
        elif not structured_html:
            structured_html = f"An error occurred during Structure Generation: {e}"
        else:
            mapped_html = f"An error occurred during Mapping Generation: {e}"

    return render_template('index.html', filename=filename, ai_response=ai_response,
                           generated_pdf=generated_pdf_filename, structured_html=structured_html,
                           structured_pdf=structured_pdf_filename, mapped_html=mapped_html, mapped_pdf=mapped_pdf_filename)


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serves uploaded files."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


if __name__ == '__main__':
    app.run(debug=True, port=5001)
