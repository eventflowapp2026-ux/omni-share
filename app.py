#!/usr/bin/env python3
"""
Enhanced Secure Share Portal - QR Code Generator & Secure File Sharing
"""

import os
import base64
import time
import secrets
import string
from flask import Flask, render_template, request, jsonify, Response
from flask import send_from_directory
import qrcode
from io import BytesIO
from PIL import Image

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# In-memory storage (use database in production)
shared_items = {}
MAX_STORAGE_DURATION = 24 * 3600  # 24 hours in seconds
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

def generate_secret_code(length=12):
    """Generate a readable secret code"""
    chars = string.ascii_uppercase + string.digits
    # Remove ambiguous characters
    chars = chars.replace('I', '').replace('1', '').replace('0', '').replace('O', '')
    return ''.join(secrets.choice(chars) for _ in range(length))

def cleanup_expired():
    """Remove expired shared items"""
    current_time = time.time()
    expired = [code for code, item in shared_items.items() 
               if current_time - item['timestamp'] > MAX_STORAGE_DURATION]
    for code in expired:
        del shared_items[code]

def make_qr_png(data: str, logo_data=None, logo_size_percent=25, add_bg=False) -> str:
    """Generate PNG QR code from string data with optional logo overlay"""
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,  # Higher error correction for logo
            box_size=10,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        
        # Create QR code image
        qr_img = qr.make_image(fill_color="black", back_color="white").convert('RGB')
        
        # Add logo if provided
        if logo_data:
            try:
                # Open logo image
                logo_img = Image.open(BytesIO(logo_data))
                
                # Convert to RGBA if needed
                if logo_img.mode != 'RGBA':
                    logo_img = logo_img.convert('RGBA')
                
                # Calculate logo size (percentage of QR code width)
                qr_width, qr_height = qr_img.size
                logo_width = int(qr_width * logo_size_percent / 100)
                logo_height = int(logo_width * logo_img.height / logo_img.width)
                
                # Resize logo
                logo_img = logo_img.resize((logo_width, logo_height), Image.Resampling.LANCZOS)
                
                # Add white background if requested
                if add_bg:
                    bg_size = (int(logo_width * 1.1), int(logo_height * 1.1))
                    bg_img = Image.new('RGBA', bg_size, (255, 255, 255, 255))
                    # Center logo on background
                    bg_img.paste(logo_img, 
                               ((bg_size[0] - logo_width) // 2, 
                                (bg_size[1] - logo_height) // 2),
                               logo_img)
                    logo_img = bg_img
                
                # Calculate position to center logo
                position = ((qr_width - logo_img.width) // 2,
                           (qr_height - logo_img.height) // 2)
                
                # Paste logo onto QR code
                if logo_img.mode == 'RGBA':
                    # Use alpha channel for transparency
                    qr_img.paste(logo_img, position, logo_img)
                else:
                    qr_img.paste(logo_img, position)
                    
            except Exception as logo_err:
                print(f"Logo error (proceeding without logo): {logo_err}")
                # Continue without logo if there's an error
        
        # Convert to bytes
        bio = BytesIO()
        qr_img.save(bio, format="PNG", optimize=True)
        bio.seek(0)
        
        img_b64 = base64.b64encode(bio.getvalue()).decode()
        return f'''
        <div class="bg-gray-50 p-4 rounded-lg">
            <img src="data:image/png;base64,{img_b64}" alt="QR Code" class="qr-img mx-auto">
            <p class="text-sm text-green-600 mt-3 font-medium">✓ QR Code Generated Successfully!</p>
            <p class="text-xs text-gray-500 mt-1">Scan this QR code with your phone's camera</p>
        </div>
        '''
    except Exception as e:
        return f'<p class="text-red-600 bg-red-50 p-3 rounded">Error generating QR code: {str(e)}</p>'

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/qr/url", methods=["POST"])
def qr_from_url():
    url = request.form.get("url", "").strip()
    logo_size = int(request.form.get("logo_size", 25))
    add_bg = request.form.get("logo_bg") == "1"
    
    print(f"Generating QR for URL: {url}, logo_size: {logo_size}%, add_bg: {add_bg}")
    
    if not url:
        return "<p class='text-red-600 bg-red-50 p-3 rounded'>Please enter a URL.</p>"
    
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    # Handle logo upload
    logo_data = None
    if 'logo' in request.files:
        logo_file = request.files['logo']
        if logo_file and logo_file.filename:
            try:
                logo_data = logo_file.read()
                if len(logo_data) > 1 * 1024 * 1024:  # 1MB limit for logo
                    return "<p class='text-red-600 bg-red-50 p-3 rounded'>Logo file too large (>1 MB).</p>"
            except Exception as e:
                print(f"Error reading logo: {e}")
    
    return make_qr_png(url, logo_data, logo_size, add_bg)

@app.route("/qr/file", methods=["POST"])
def qr_from_file():
    print("File upload received")
    
    if "file" not in request.files:
        return "<p class='text-red-600 bg-red-50 p-3 rounded'>No file selected.</p>"
    
    file = request.files["file"]
    
    if file.filename == "":
        return "<p class='text-red-600 bg-red-50 p-3 rounded'>No file selected.</p>"

    try:
        # Get options
        logo_size = int(request.form.get("logo_size", 25))
        add_bg = request.form.get("logo_bg") == "1"
        
        # Read file data
        data = file.read()
        if len(data) > 2 * 1024 * 1024:
            return "<p class='text-red-600 bg-red-50 p-3 rounded'>File too large (>2 MB).</p>"

        b64 = base64.b64encode(data).decode()
        mime = file.mimetype or "application/octet-stream"
        data_url = f"data:{mime};base64,{b64}"
        
        # Handle logo upload
        logo_data = None
        if 'logo' in request.files:
            logo_file = request.files['logo']
            if logo_file and logo_file.filename:
                try:
                    logo_data = logo_file.read()
                    if len(logo_data) > 1 * 1024 * 1024:
                        return "<p class='text-red-600 bg-red-50 p-3 rounded'>Logo file too large (>1 MB).</p>"
                except Exception as e:
                    print(f"Error reading logo: {e}")
        
        print(f"Generated data URL for file: {file.filename}")
        return make_qr_png(data_url, logo_data, logo_size, add_bg)
        
    except Exception as e:
        return f'<p class="text-red-600 bg-red-50 p-3 rounded">Error processing file: {str(e)}</p>'

@app.route("/share", methods=["POST"])
def share_content():
    """Store content and return a secret code"""
    try:
        cleanup_expired()
        
        share_type = request.form.get("type")
        password = request.form.get("password", "").strip()
        
        if share_type == "link":
            content = request.form.get("content", "").strip()
            if not content:
                return jsonify({"success": False, "error": "No URL provided"})
            if not content.startswith(('http://', 'https://')):
                content = 'https://' + content
            
        elif share_type == "file":
            if "file" not in request.files:
                return jsonify({"success": False, "error": "No file provided"})
            
            file = request.files["file"]
            if file.filename == "":
                return jsonify({"success": False, "error": "No file selected"})
            
            content = file.read()
            if len(content) > MAX_FILE_SIZE:
                return jsonify({"success": False, "error": f"File too large (max {MAX_FILE_SIZE//1024//1024}MB)"})
            
            filename = request.form.get("filename", file.filename)
            
        elif share_type == "text":
            content = request.form.get("content", "").strip()
            if not content:
                return jsonify({"success": False, "error": "No text provided"})
        else:
            return jsonify({"success": False, "error": "Invalid share type"})
        
        # Generate unique code
        code = generate_secret_code()
        while code in shared_items:
            code = generate_secret_code()
        
        # Store the content
        shared_items[code] = {
            'type': share_type,
            'content': content if share_type != 'file' else base64.b64encode(content).decode(),
            'timestamp': time.time(),
            'password': password,
            'filename': filename if share_type == 'file' else None,
            'size': len(content) if share_type == 'file' else None
        }
        
        print(f"Stored {share_type} with code: {code}")
        return jsonify({"success": True, "code": code})
        
    except Exception as e:
        print(f"Error sharing content: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/retrieve", methods=["POST"])
def retrieve_content():
    """Retrieve content using a secret code"""
    try:
        cleanup_expired()
        
        data = request.get_json()
        code = data.get("code", "").strip().upper()
        password = data.get("password", "")
        
        if not code:
            return jsonify({"success": False, "error": "No code provided"})
        
        if code not in shared_items:
            return jsonify({"success": False, "error": "Invalid or expired code"})
        
        item = shared_items[code]
        
        # Check password if set
        if item['password'] and item['password'] != password:
            return jsonify({"success": False, "requires_password": True, "error": "Incorrect password"})
        
        # Prepare response based on type
        response = {
            "success": True,
            "type": item['type'],
            "expires": int(item['timestamp'] + MAX_STORAGE_DURATION)
        }
        
        if item['type'] == 'link':
            response["content"] = item['content']
        elif item['type'] == 'file':
            response["content"] = item['content']  # base64 encoded
            response["filename"] = item['filename']
            response["size"] = item['size']
        elif item['type'] == 'text':
            response["content"] = item['content']
        
        return jsonify(response)
        
    except Exception as e:
        print(f"Error retrieving content: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/download/<code>")
def download_file(code):
    """Download a shared file"""
    try:
        cleanup_expired()
        
        code = code.upper()
        if code not in shared_items or shared_items[code]['type'] != 'file':
            return "File not found or expired", 404
        
        item = shared_items[code]
        file_data = base64.b64decode(item['content'])
        
        response = Response(
            file_data,
            status=200,
            mimetype='application/octet-stream'
        )
        response.headers.set('Content-Disposition', 'attachment', 
                           filename=item['filename'] or f'download_{code}.bin')
        
        return response
        
    except Exception as e:
        print(f"Error downloading file: {e}")
        return "Error downloading file", 500

@app.route("/retrieve/<code>")
def retrieve_page(code):
    """Direct page for retrieving content with a code"""
    return render_template("index.html")

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)

# Change the run section to:
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
