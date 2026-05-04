import os
import base64
import uuid
from flask import Flask, render_template, request, redirect, url_for, send_from_directory
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
        return render_template('index.html', filename=filename, error="OpenAI API key not configured. Please set the OPENAI_API_KEY environment variable.")

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

    # Hardcoded strict prompt
    prompt = """
    Analyze the attached invoice. Generate a single, self-contained HTML file with inline CSS that visually replicates this document as closely as possible. 
    You MUST accurately recreate every single table, border, line, font size difference, and alignment.
    Do NOT simplify tables. If there is a table for line items, recreate it perfectly using <table>, <thead>, <tbody>, <tr>, <th>, and <td> with appropriate CSS borders and padding.
    Return ONLY the raw HTML code starting with <!DOCTYPE html> and ending with </html>. 
    Do not include any introductory text, explanatory text, or markdown blocks like ```html.
    """

    generated_pdf_filename = None
    ai_response = None
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[  # type: ignore
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}}
                    ]
                }
            ],
            temperature=0.0,
        )
        
        raw_response_content = response.choices[0].message.content
        if raw_response_content:
            html_content = raw_response_content.strip()
            
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

            ai_response = html_content.strip()
            
            # Generate the Full PDF
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
                 ai_response = "Error: The AI response did not contain valid HTML."

        else:
             ai_response = "Error: AI did not return any content."

    except Exception as e:
        ai_response = f"An error occurred: {e}"

    return render_template('index.html', filename=filename, ai_response=ai_response, generated_pdf=generated_pdf_filename)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serves uploaded files."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    app.run(debug=True)
