import os
import base64
import uuid
from flask import Flask, render_template, request, redirect, send_from_directory
from werkzeug.utils import secure_filename
from openai import OpenAI
from playwright.sync_api import sync_playwright
from pdf2image import convert_from_path

# --- Configuration ---
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}

# WARNING: DO NOT hardcode API keys in production code. Use environment variables.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "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")

# --- Flask App Initialization ---
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- OpenAI Client Initialization ---
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)
else:
    client = None
    print("WARNING: OPENAI_API_KEY is not set. AI processing will fail.")

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
            return render_template('index.html', filename=filename, error=f"PDF conversion error: {str(e)}. Make sure 'poppler' is installed on your system.")
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
    ai_response = None
    structured_html = None

    try:
        # ---------------------------------------------------------
        # TASK 1: Get the exact reconstruction from GPT-4o
        # ---------------------------------------------------------
        response_1 = client.chat.completions.create(
            model="gpt-4o",
            messages=[  # type: ignore
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
            model="gpt-5.4", # Calling GPT-5 as requested for the structure task
            messages=[  # type: ignore
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
            else:
                structured_html = "Error: The AI response did not contain valid HTML for Task 2."
        else:
            structured_html = "Error: AI did not return any content for Task 2."

    except Exception as e:
        if not ai_response:
            ai_response = f"An error occurred: {e}"
        else:
            structured_html = f"An error occurred during Structure Generation: {e}"

    return render_template('index.html', filename=filename, ai_response=ai_response, generated_pdf=generated_pdf_filename, structured_html=structured_html, structured_pdf=structured_pdf_filename)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serves uploaded files."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    app.run(debug=True, port=5001)
