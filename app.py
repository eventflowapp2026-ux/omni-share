#!/usr/bin/env python3
"""
Enhanced Secure Share Portal - QR Code Generator & Secure File Sharing
"""

import os
import base64
import time
import secrets
import string
from flask import Flask, render_template, request, jsonify, Response, redirect
from flask import send_from_directory
import qrcode
from io import BytesIO
from PIL import Image
import threading
import mimetypes

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# Configuration
MAX_STORAGE_DURATION = 96 * 3600  # 96 hours (4 days)
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
STORAGE_DIR = "shared_files"
MAX_QR_DATA_SIZE = 2500  # Conservative limit for QR version 40

# Ensure storage directory exists
os.makedirs(STORAGE_DIR, exist_ok=True)

# Add CORS headers to all responses
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# In-memory storage for metadata (files are stored on disk)
shared_items = {}
shortened_urls = {}  # Add this for URL shortener

def generate_secret_code(length=12):
    """Generate a readable secret code"""
    chars = string.ascii_uppercase + string.digits
    # Remove ambiguous characters
    chars = chars.replace('I', '').replace('1', '').replace('0', '').replace('O', '')
    return ''.join(secrets.choice(chars) for _ in range(length))

def generate_short_code(length=6):
    """Generate a short URL code"""
    chars = string.ascii_lowercase + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))

def save_file_to_disk(code, file_data, filename):
    """Save file to disk and return file path"""
    # Create a safe filename
    import re
    safe_name = re.sub(r'[^\w\.-]', '_', filename)
    safe_filename = f"{code}_{safe_name}"
    file_path = os.path.join(STORAGE_DIR, safe_filename)
    
    # Write file to disk
    with open(file_path, 'wb') as f:
        f.write(file_data)
    
    return file_path

def load_file_from_disk(file_path):
    """Load file from disk"""
    if os.path.exists(file_path):
        with open(file_path, 'rb') as f:
            return f.read()
    return None

def delete_file_from_disk(file_path):
    """Delete file from disk"""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            return True
    except Exception as e:
        print(f"Error deleting file {file_path}: {e}")
    return False

def cleanup_expired():
    """Remove expired shared items, shortened URLs, and delete files from disk"""
    current_time = time.time()
    
    # Clean shared items
    expired_items = []
    for code, item in shared_items.items():
        if current_time - item['timestamp'] > MAX_STORAGE_DURATION:
            expired_items.append(code)
            # Delete file from disk if it exists
            if 'file_path' in item and item['file_path']:
                delete_file_from_disk(item['file_path'])
    
    for code in expired_items:
        del shared_items[code]
    
    # Clean shortened URLs
    expired_urls = [code for code, item in shortened_urls.items()
                    if current_time - item['timestamp'] > MAX_STORAGE_DURATION]
    for code in expired_urls:
        del shortened_urls[code]
    
    # Also clean up any orphaned files in storage directory
    if expired_items:
        cleanup_orphaned_files()

def cleanup_orphaned_files():
    """Clean up files in storage directory that don't have entries in shared_items"""
    try:
        for filename in os.listdir(STORAGE_DIR):
            file_path = os.path.join(STORAGE_DIR, filename)
            if os.path.isfile(file_path):
                # Extract code from filename (format: CODE_original_filename)
                parts = filename.split('_', 1)
                if len(parts) >= 1:
                    code = parts[0]
                    # Check if this code exists in shared_items
                    if code not in shared_items:
                        # File is orphaned, delete it
                        delete_file_from_disk(file_path)
    except Exception as e:
        print(f"Error cleaning orphaned files: {e}")

def start_cleanup_scheduler():
    """Start a background thread to periodically clean up expired files"""
    def cleanup_task():
        while True:
            time.sleep(3600)  # Run every hour
            cleanup_expired()
            print(f"Cleanup completed at {time.ctime()}")
    
    # Start the cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_task, daemon=True)
    cleanup_thread.start()
    print("Cleanup scheduler started")

# Start cleanup scheduler when app starts
start_cleanup_scheduler()

def format_file_size(bytes):
    """Format file size in human-readable format"""
    if bytes < 1024:
        return f"{bytes} bytes"
    elif bytes < 1024 * 1024:
        return f"{bytes/1024:.1f} KB"
    else:
        return f"{bytes/(1024*1024):.1f} MB"

def make_qr_png(data: str, logo_data=None, logo_size_percent=25, add_bg=False) -> str:
    """Generate PNG QR code from string data with optional logo overlay"""
    try:
        # Determine appropriate QR version based on data size
        data_length = len(data.encode('utf-8'))
        
        # Calculate minimum version needed
        # Approximate capacity per version (binary mode, H error correction)
        version_capacities = {
            1: 72, 2: 128, 3: 184, 4: 240, 5: 296,
            6: 352, 7: 408, 8: 464, 9: 520, 10: 576,
            11: 640, 12: 704, 13: 768, 14: 832, 15: 896,
            16: 960, 17: 1024, 18: 1088, 19: 1152, 20: 1216,
            21: 1280, 22: 1344, 23: 1408, 24: 1472, 25: 1536,
            26: 1600, 27: 1664, 28: 1728, 29: 1792, 30: 1856,
            31: 1920, 32: 1984, 33: 2048, 34: 2112, 35: 2176,
            36: 2240, 37: 2304, 38: 2368, 39: 2432, 40: 2500
        }
        
        # Find appropriate version
        version = 1
        for v in range(1, 41):
            if data_length <= version_capacities[v]:
                version = v
                break
        else:
            # If data is too large even for version 40
            return f'<p class="text-red-600 bg-red-50 p-3 rounded">Data too large for QR code (max 2500 bytes)</p>'
        
        qr = qrcode.QRCode(
            version=version,  # Auto-select version based on data size
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
        
        # Add version info for debugging
        version_info = ""
        if version > 20:  # Only show warning for large versions
            version_info = f'<p class="text-xs text-orange-500 mt-1">Using QR version {version} (high capacity)</p>'
        
        return f'''
        <div class="bg-gray-50 p-4 rounded-lg">
            <img src="data:image/png;base64,{img_b64}" alt="QR Code" class="qr-img mx-auto">
            <p class="text-sm text-green-600 mt-3 font-medium">✓ QR Code Generated Successfully!</p>
            <p class="text-xs text-gray-500 mt-1">Scan this QR code with your phone's camera</p>
            {version_info}
        </div>
        '''
    except Exception as e:
        return f'<p class="text-red-600 bg-red-50 p-3 rounded">Error generating QR code: {str(e)}</p>'

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/view")
def view_content():
    """Page for viewing shared content"""
    return render_template("view.html")

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
        file_size = len(data)
        
        if file_size > MAX_QR_DATA_SIZE:
            # File is too large for direct QR encoding
            # Create a share and generate QR with download link instead
            return handle_large_file_for_qr(file, data, logo_size, add_bg)
        
        # File is small enough for direct QR encoding
        # Create data URL
        mime_type = mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
        b64 = base64.b64encode(data).decode()
        data_url = f"data:{mime_type};base64,{b64}"
        
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
        
        print(f"Generating QR with data URL for file: {file.filename} ({file_size} bytes)")
        
        # Generate QR code
        return make_qr_png(data_url, logo_data, logo_size, add_bg)
        
    except Exception as e:
        print(f"Error processing file: {e}")
        return f'<p class="text-red-600 bg-red-50 p-3 rounded">Error processing file: {str(e)}</p>'

def handle_large_file_for_qr(file, data, logo_size, add_bg):
    """Handle files too large for direct QR encoding"""
    try:
        # Create a share for the file
        cleanup_expired()
        
        # Generate unique code for the file
        share_code = generate_secret_code()
        while share_code in shared_items:
            share_code = generate_secret_code()
        
        # Save file to disk
        file_path = save_file_to_disk(share_code, data, file.filename)
        
        # Store the file metadata
        shared_items[share_code] = {
            'type': 'file',
            'content': base64.b64encode(data).decode(),
            'timestamp': time.time(),
            'password': '',  # No password for QR file shares
            'filename': file.filename,
            'size': len(data),
            'file_path': file_path
        }
        
        # Create download URL
        download_url = f"{request.host_url}download/{share_code}"
        
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
        
        print(f"File too large for direct QR. Created share: {share_code} for {file.filename}")
        
        # Generate QR code with the download URL
        qr_html = make_qr_png(download_url, logo_data, logo_size, add_bg)
        
        # Add info message and actions
        info_html = f'''
        <div class="bg-yellow-50 border-l-4 border-yellow-400 p-4 mb-4">
            <div class="flex">
                <div class="flex-shrink-0">
                    <i class="fas fa-exclamation-triangle text-yellow-400"></i>
                </div>
                <div class="ml-3">
                    <p class="text-sm text-yellow-700">
                        <span class="font-medium">Note:</span> File too large for direct QR code.
                        Generated a QR code with a download link instead.
                    </p>
                    <div class="mt-2">
                        <p class="text-xs text-yellow-600">
                            File: <span class="font-medium">{file.filename}</span> 
                            ({format_file_size(len(data))})
                        </p>
                        <p class="text-xs text-yellow-600 mt-1">
                            Share Code: <span class="font-mono bg-yellow-100 px-2 py-1 rounded">{share_code}</span>
                        </p>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="mt-4 flex flex-wrap gap-2 justify-center">
            <button onclick="copyToClipboard('{download_url}')" class="copy-btn">
                <i class="fas fa-copy mr-1"></i>Copy Download Link
            </button>
            <button onclick="copyToClipboard('{share_code}')" class="copy-btn">
                <i class="fas fa-key mr-1"></i>Copy Share Code
            </button>
            <button onclick="window.open('/view?code={share_code}', '_blank')" class="btn-secondary text-sm">
                <i class="fas fa-eye mr-1"></i>Preview File
            </button>
        </div>
        '''
        
        return info_html + qr_html
        
    except Exception as e:
        print(f"Error handling large file: {e}")
        return f'<p class="text-red-600 bg-red-50 p-3 rounded">Error handling file: {str(e)}</p>'

# URL Shortener Routes
@app.route("/shorten", methods=["POST", "OPTIONS"])
def shorten_url():
    """Shorten a URL"""
    # Handle preflight CORS requests
    if request.method == "OPTIONS":
        response = jsonify({"status": "ok"})
        response.headers.add("Access-Control-Allow-Origin", "*")
        return response
    
    try:
        cleanup_expired()
        
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
            
        original_url = data.get("url", "").strip()
        custom_code = data.get("custom_code", "").strip().lower()
        
        if not original_url:
            return jsonify({"success": False, "error": "No URL provided"}), 400
        
        if not original_url.startswith(('http://', 'https://')):
            original_url = 'https://' + original_url
        
        # Generate or use custom code
        if custom_code:
            if len(custom_code) < 4 or len(custom_code) > 12:
                return jsonify({"success": False, "error": "Custom code must be 4-12 characters"}), 400
            if not all(c.isalnum() for c in custom_code):
                return jsonify({"success": False, "error": "Custom code can only contain letters and numbers"}), 400
            if custom_code in shortened_urls:
                return jsonify({"success": False, "error": "Custom code already in use"}), 400
            short_code = custom_code
        else:
            short_code = generate_short_code()
            while short_code in shortened_urls:
                short_code = generate_short_code()
        
        # Store the shortened URL
        shortened_urls[short_code] = {
            'original_url': original_url,
            'timestamp': time.time(),
            'visits': 0
        }
        
        # Use request.host_url which should work on Render
        short_url = f"{request.host_url}{short_code}"
        
        response = jsonify({
            "success": True, 
            "short_code": short_code,
            "short_url": short_url,
            "original_url": original_url
        })
        
        response.headers.add("Access-Control-Allow-Origin", "*")
        return response
        
    except Exception as e:
        print(f"Error shortening URL: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/<short_code>")
def redirect_short_url(short_code):
    """Redirect to original URL"""
    cleanup_expired()
    
    short_code = short_code.lower()
    
    if short_code in shortened_urls:
        shortened_urls[short_code]['visits'] += 1
        return redirect(shortened_urls[short_code]['original_url'])
    else:
        return redirect("/")

@app.route("/shorten/stats", methods=["POST", "OPTIONS"])
def get_short_url_stats():
    """Get statistics for a short URL"""
    # Handle preflight CORS
    if request.method == "OPTIONS":
        response = jsonify({"status": "ok"})
        response.headers.add("Access-Control-Allow-Origin", "*")
        return response
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
            
        short_code = data.get("code", "").strip().lower()
        
        if not short_code:
            return jsonify({"success": False, "error": "No code provided"}), 400
        
        if short_code not in shortened_urls:
            return jsonify({"success": False, "error": "Invalid or expired short URL"}), 404
        
        item = shortened_urls[short_code]
        expires_at = int(item['timestamp'] + MAX_STORAGE_DURATION)
        
        response = jsonify({
            "success": True,
            "short_code": short_code,
            "original_url": item['original_url'],
            "visits": item['visits'],
            "created_at": int(item['timestamp']),
            "expires_at": expires_at,
            "short_url": f"{request.host_url}{short_code}"
        })
        
        response.headers.add("Access-Control-Allow-Origin", "*")
        return response
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

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
            file_path = None
            
        elif share_type == "file":
            if "file" not in request.files:
                return jsonify({"success": False, "error": "No file provided"})
            
            file = request.files["file"]
            if file.filename == "":
                return jsonify({"success": False, "error": "No file selected"})
            
            file_data = file.read()
            if len(file_data) > MAX_FILE_SIZE:
                return jsonify({"success": False, "error": f"File too large (max {MAX_FILE_SIZE//1024//1024}MB)"})
            
            filename = request.form.get("filename", file.filename)
            
        elif share_type == "text":
            content = request.form.get("content", "").strip()
            if not content:
                return jsonify({"success": False, "error": "No text provided"})
            file_path = None
        else:
            return jsonify({"success": False, "error": "Invalid share type"})
        
        # Generate unique code
        code = generate_secret_code()
        while code in shared_items:
            code = generate_secret_code()
        
        # Store the content
        if share_type == 'file':
            # Save file to disk
            file_path = save_file_to_disk(code, file_data, filename)
            shared_items[code] = {
                'type': share_type,
                'content': base64.b64encode(file_data).decode(),  # Keep base64 for compatibility
                'timestamp': time.time(),
                'password': password,
                'filename': filename,
                'size': len(file_data),
                'file_path': file_path  # Store the path to the file on disk
            }
        else:
            shared_items[code] = {
                'type': share_type,
                'content': content,
                'timestamp': time.time(),
                'password': password,
                'filename': None,
                'size': None,
                'file_path': None
            }
        
        print(f"Stored {share_type} with code: {code}")
        if share_type == 'file':
            print(f"File saved to: {file_path}")
        
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
            # For files, we keep the base64 in memory for quick retrieval
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
        
        # Try to read from disk first
        if 'file_path' in item and item['file_path']:
            file_data = load_file_from_disk(item['file_path'])
            if file_data:
                response = Response(
                    file_data,
                    status=200,
                    mimetype='application/octet-stream'
                )
                response.headers.set('Content-Disposition', 'attachment', 
                                   filename=item['filename'] or f'download_{code}.bin')
                return response
        
        # Fallback to base64 content (for backward compatibility)
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
    # Redirect to the new view page
    return redirect(f"/view?code={code}")

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)

@app.route("/admin/cleanup", methods=["GET"])
def manual_cleanup():
    """Manually trigger cleanup (for testing/admin)"""
    try:
        cleanup_expired()
        file_count = len([f for f in os.listdir(STORAGE_DIR) if os.path.isfile(os.path.join(STORAGE_DIR, f))]) if os.path.exists(STORAGE_DIR) else 0
        return jsonify({
            "success": True,
            "message": f"Cleanup completed. Storage directory: {STORAGE_DIR}",
            "file_count": file_count,
            "shared_items_count": len(shared_items),
            "shortened_urls_count": len(shortened_urls)
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/admin/stats", methods=["GET"])
def admin_stats():
    """Get admin statistics"""
    try:
        file_count = len([f for f in os.listdir(STORAGE_DIR) if os.path.isfile(os.path.join(STORAGE_DIR, f))]) if os.path.exists(STORAGE_DIR) else 0
        
        # Calculate total storage used
        total_size = 0
        if os.path.exists(STORAGE_DIR):
            for filename in os.listdir(STORAGE_DIR):
                file_path = os.path.join(STORAGE_DIR, filename)
                if os.path.isfile(file_path):
                    total_size += os.path.getsize(file_path)
        
        return jsonify({
            "success": True,
            "storage_dir": STORAGE_DIR,
            "file_count": file_count,
            "total_storage_used": format_file_size(total_size),
            "shared_items_count": len(shared_items),
            "shortened_urls_count": len(shortened_urls),
            "max_storage_duration_hours": MAX_STORAGE_DURATION / 3600,
            "max_file_size_mb": MAX_FILE_SIZE / (1024 * 1024)
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
