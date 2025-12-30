"""
RENTAL MOBIL FLASK APPLICATION - WITH MIDTRANS INTEGRATION (TANPA CHAT & REPORT)
File lengkap: app.py (REVISI - TANPA CHAT DAN REPORT)
"""

import os
import sys
import uuid
import bcrypt
import mysql.connector
from mysql.connector import Error
from datetime import datetime, timedelta
from decimal import Decimal
from functools import wraps
import json
import requests
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_from_directory
from flask_session import Session
from werkzeug.utils import secure_filename
import logging
from dotenv import load_dotenv
import base64
import midtransclient

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ============================================
# CONFIGURATION
# ============================================

class Config:
    # Flask Configuration
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'rental-mobil-secret-key-2024-change-in-production'
    
    # Database Configuration
    DB_HOST = os.environ.get('DB_HOST', 'cloudc.mysql.database.azure.com')
    DB_USER = os.environ.get('DB_USER', 'cloudc')
    DB_PASSWORD = os.environ.get('DB_PASSWORD', 'Vallenlistrik1')
    DB_NAME = os.environ.get('DB_NAME', 'rental_mobil')
    
    # File Upload Configuration
    UPLOAD_FOLDER = 'static/uploads'
    CAR_UPLOAD_FOLDER = 'static/uploads/cars'
    PROFILE_UPLOAD_FOLDER = 'static/uploads/profiles'
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    
    # Session Configuration
    PERMANENT_SESSION_LIFETIME = timedelta(days=1)
    SESSION_TYPE = 'filesystem'
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    
    # Midtrans Configuration
    MIDTRANS_MERCHANT_ID = os.environ.get('MIDTRANS_MERCHANT_ID', 'G922785536')
    MIDTRANS_SERVER_KEY = os.environ.get('MIDTRANS_SERVER_KEY', 'Mid-server-dfRkZmytSoucD3XLAPV2iEqa')
    MIDTRANS_CLIENT_KEY = os.environ.get('MIDTRANS_CLIENT_KEY', 'Mid-client-KMUkkAkMpLMRF8Ya')
    MIDTRANS_SANDBOX = os.environ.get('MIDTRANS_SANDBOX', 'True').lower() == 'true'
    MIDTRANS_API_URL = 'https://app.sandbox.midtrans.com/snap/v1/transactions' if MIDTRANS_SANDBOX else 'https://app.midtrans.com/snap/v1/transactions'
    
    # Application Settings
    ITEMS_PER_PAGE = 10
    BOOKING_ADVANCE_DAYS = 1
    MAX_RENTAL_DAYS = 30
    
    # WhatsApp Contact (Ganti dengan nomor admin yang sesuai)
    WHATSAPP_ADMIN_NUMBER = os.environ.get('WHATSAPP_ADMIN_NUMBER', '6281234567890')
    
    @staticmethod
    def init_app(app):
        for folder in [app.config['UPLOAD_FOLDER'], 
                      app.config['CAR_UPLOAD_FOLDER'], 
                      app.config['PROFILE_UPLOAD_FOLDER']]:
            os.makedirs(folder, exist_ok=True)

# ============================================
# FLASK APP INITIALIZATION
# ============================================

app = Flask(__name__)
app.config.from_object(Config)

Session(app)
Config.init_app(app)

# ============================================
# HELPER FUNCTIONS
# ============================================

def get_db_connection():
    """Create database connection"""
    try:
        connection = mysql.connector.connect(
            host=app.config['DB_HOST'],
            user=app.config['DB_USER'],
            password=app.config['DB_PASSWORD'],
            database=app.config['DB_NAME'],
            autocommit=False
        )
        return connection
    except Error as e:
        logger.error(f"Error connecting to MySQL: {e}")
        return None

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def save_file(file, folder):
    """Save uploaded file with unique filename"""
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4().hex}_{filename}"
        filepath = os.path.join(folder, unique_filename)
        file.save(filepath)
        return unique_filename
    return None

def verify_password(password_hash, password_input):
    """Verify password with dual hash support"""
    try:
        if password_hash.startswith('$2y$'):
            password_hash = '$2b$' + password_hash[4:]
        return bcrypt.checkpw(password_input.encode('utf-8'), password_hash.encode('utf-8'))
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        # Fallback untuk hash lama
        try:
            return check_password_hash(password_hash, password_input)
        except:
            return False

def login_required(f):
    """Decorator for requiring login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Silakan login terlebih dahulu', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator for requiring admin role"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Silakan login terlebih dahulu', 'warning')
            return redirect(url_for('login'))
        
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT role FROM users WHERE id = %s", (session['user_id'],))
            user = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if user and user['role'] == 'admin':
                return f(*args, **kwargs)
        
        flash('Akses ditolak. Hanya admin yang dapat mengakses halaman ini.', 'danger')
        return redirect(url_for('index'))
    return decorated_function

def calculate_total_price(price_per_day, start_date, end_date):
    """Calculate total rental price"""
    try:
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')
        days = (end - start).days + 1
        if days < 1:
            return 0
        return Decimal(price_per_day) * days
    except:
        return 0

def validate_nik(nik):
    """Validate NIK format"""
    return nik.isdigit() and len(nik) == 16

def generate_booking_code():
    """Generate unique booking code"""
    return f"RENT-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

def generate_midtrans_token(order_data):
    """Generate Midtrans Snap token for pop-up payment"""
    try:
        # Create Snap API instance
        snap = midtransclient.Snap(
            is_production=not app.config['MIDTRANS_SANDBOX'],
            server_key=app.config['MIDTRANS_SERVER_KEY']
        )
        
        # Prepare customer details
        customer_details = {
            "first_name": order_data.get('first_name', 'Customer'),
            "last_name": order_data.get('last_name', ''),
            "email": order_data.get('email', ''),
            "phone": order_data.get('phone', '081234567890')
        }
        
        # Prepare transaction details
        transaction_details = {
            "order_id": order_data['order_id'],
            "gross_amount": int(order_data['gross_amount'])
        }
        
        # Prepare item details
        item_details = order_data.get('item_details', [])
        
        # Build API parameter
        param = {
            "transaction_details": transaction_details,
            "credit_card": {
                "secure": True
            },
            "customer_details": customer_details
        }
        
        # Add item details if available
        if item_details:
            param["item_details"] = item_details
        
        # Create transaction
        transaction = snap.create_transaction(param)
        transaction_token = transaction['token']
        
        logger.info(f"Midtrans token generated for order {order_data['order_id']}")
        return transaction_token
        
    except Exception as e:
        logger.error(f"Error generating Midtrans token: {e}")
        return None

def get_midtrans_auth_header():
    """Generate Authorization header for Midtrans API"""
    server_key = app.config['MIDTRANS_SERVER_KEY']
    auth_string = base64.b64encode(f"{server_key}:".encode()).decode()
    return f"Basic {auth_string}"

def terbilang(n):
    """Convert number to Indonesian words"""
    try:
        n = int(n)
    except:
        n = 0
    
    angka = ["", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan", "sepuluh", "sebelas"]
    
    if n < 12:
        return angka[n]
    elif n < 20:
        return terbilang(n - 10) + " belas"
    elif n < 100:
        return terbilang(n // 10) + " puluh " + terbilang(n % 10)
    elif n < 200:
        return "seratus " + terbilang(n - 100)
    elif n < 1000:
        return terbilang(n // 100) + " ratus " + terbilang(n % 100)
    elif n < 2000:
        return "seribu " + terbilang(n - 1000)
    elif n < 1000000:
        return terbilang(n // 1000) + " ribu " + terbilang(n % 1000)
    elif n < 1000000000:
        return terbilang(n // 1000000) + " juta " + terbilang(n % 1000000)
    else:
        return "angka terlalu besar"

def format_rupiah(n):
    """Format number to Rupiah currency"""
    try:
        return f"Rp {int(n):,}".replace(',', '.')
    except:
        return "Rp 0"

def insert_midtrans_log(order_id, transaction_data):
    """Insert Midtrans notification log"""
    try:
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            
            transaction_status = transaction_data.get('transaction_status')
            fraud_status = transaction_data.get('fraud_status')
            transaction_id = transaction_data.get('transaction_id')
            payment_type = transaction_data.get('payment_type')
            bank = transaction_data.get('bank')
            va_number = transaction_data.get('va_number')
            gross_amount = transaction_data.get('gross_amount')
            
            cursor.execute("""
                INSERT INTO midtrans_logs 
                (order_id, transaction_id, transaction_status, fraud_status, 
                 payment_type, bank, va_number, gross_amount, response_data)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                order_id,
                transaction_id,
                transaction_status,
                fraud_status,
                payment_type,
                bank,
                va_number,
                gross_amount,
                json.dumps(transaction_data)
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Midtrans log inserted for order {order_id}")
            return True
    except Exception as e:
        logger.error(f"Error inserting Midtrans log: {e}")
    return False

# ============================================
# FIXED VERSION: update_payment_status FUNCTION
# ============================================

def update_payment_status(order_id, transaction_data):
    """Update payment status based on Midtrans notification - FIXED VERSION"""
    try:
        conn = get_db_connection()
        if not conn:
            logger.error("Database connection failed")
            return False
            
        cursor = conn.cursor(dictionary=True)
        
        transaction_status = transaction_data.get('transaction_status')
        transaction_id = transaction_data.get('transaction_id')
        payment_type = transaction_data.get('payment_type')
        bank = transaction_data.get('bank')
        va_number = transaction_data.get('va_number')
        settlement_time = transaction_data.get('settlement_time')
        gross_amount = transaction_data.get('gross_amount')
        
        logger.info(f"Updating payment status for order {order_id}: {transaction_status}")
        
        # Check if order exists
        cursor.execute("SELECT id, mobil_id, user_id, total_harga FROM pesanan WHERE kode_pesanan = %s", (order_id,))
        order = cursor.fetchone()
        
        if not order:
            logger.error(f"Order {order_id} not found in database")
            cursor.close()
            conn.close()
            return False
        
        logger.info(f"Found order: ID={order['id']}, User={order['user_id']}, Mobil={order['mobil_id']}")
        
        # Handle different transaction statuses
        if transaction_status in ['settlement', 'capture']:
            # Pembayaran BERHASIL
            new_payment_status = 'paid'
            new_order_status = 'dikonfirmasi'
            
            # Handle settlement time
            payment_date = None
            if settlement_time:
                try:
                    payment_date = datetime.strptime(settlement_time, '%Y-%m-%d %H:%M:%S')
                except:
                    try:
                        payment_date = datetime.strptime(settlement_time, '%Y-%m-%d %H:%M:%S.%f')
                    except:
                        payment_date = datetime.now()
            else:
                payment_date = datetime.now()
            
            logger.info(f"Payment SUCCESS for {order_id}, payment date: {payment_date}")
            
            # Update pesanan
            cursor.execute("""
                UPDATE pesanan 
                SET status_pembayaran = %s,
                    status = %s,
                    midtrans_order_id = %s,
                    midtrans_transaction_status = %s,
                    payment_type = %s,
                    bank = %s,
                    va_number = %s,
                    transaction_time = %s,
                    settlement_time = %s,
                    tanggal_pembayaran = %s,
                    updated_at = NOW()
                WHERE kode_pesanan = %s
            """, (
                new_payment_status,
                new_order_status,
                transaction_id,
                transaction_status,
                payment_type,
                bank,
                va_number,
                payment_date,
                payment_date if transaction_status == 'settlement' else None,
                payment_date,
                order_id
            ))
            
            # Update mobil status
            cursor.execute("UPDATE mobil SET status = 'disewa', updated_at = NOW() WHERE id = %s", 
                          (order['mobil_id'],))
            
            # Insert into pembayaran table (using INSERT IGNORE to avoid duplicates)
            cursor.execute("""
                INSERT IGNORE INTO pembayaran 
                (pesanan_id, jumlah, metode, status, transaction_id, 
                 payment_type, bank, va_number, tanggal_pembayaran, created_at, updated_at)
                VALUES (%s, %s, 'midtrans', 'success', %s, %s, %s, %s, %s, NOW(), NOW())
            """, (
                order['id'],
                order['total_harga'],
                transaction_id,
                payment_type,
                bank,
                va_number,
                payment_date
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Database updated successfully for order {order_id}")
            
            return True
            
        elif transaction_status == 'pending':
            # Pembayaran PENDING
            cursor.execute("""
                UPDATE pesanan 
                SET status_pembayaran = 'pending',
                    midtrans_order_id = %s,
                    midtrans_transaction_status = %s,
                    payment_type = %s,
                    bank = %s,
                    va_number = %s,
                    updated_at = NOW()
                WHERE kode_pesanan = %s
            """, (transaction_id, transaction_status, payment_type, bank, va_number, order_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Payment PENDING updated for order {order_id}")
            return True
            
        elif transaction_status in ['deny', 'cancel', 'expire', 'failure']:
            # Pembayaran GAGAL
            cursor.execute("""
                UPDATE pesanan 
                SET status_pembayaran = 'failed',
                    status = 'dibatalkan',
                    midtrans_order_id = %s,
                    midtrans_transaction_status = %s,
                    updated_at = NOW()
                WHERE kode_pesanan = %s
            """, (transaction_id, transaction_status, order_id))
            
            # Kembalikan status mobil
            cursor.execute("UPDATE mobil SET status = 'tersedia', updated_at = NOW() WHERE id = %s", 
                          (order['mobil_id'],))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Payment FAILED updated for order {order_id}")
            return True
        
        else:
            logger.warning(f"Unknown transaction status: {transaction_status}")
            conn.rollback()
            cursor.close()
            conn.close()
            return False
        
    except Exception as e:
        logger.error(f"Error updating payment status: {e}", exc_info=True)
        if conn:
            conn.rollback()
            cursor.close()
            conn.close()
        return False

# ============================================
# ROUTES - PUBLIC PAGES
# ============================================

@app.route('/')
def index():
    """Landing page"""
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT * FROM mobil 
            WHERE status = 'tersedia' 
            ORDER BY created_at DESC 
            LIMIT 6
        """)
        featured_cars = cursor.fetchall()
        
        cursor.execute("SELECT COUNT(*) as total_cars FROM mobil WHERE status = 'tersedia'")
        stats = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        return render_template('index.html', 
                             featured_cars=featured_cars,
                             stats=stats,
                             whatsapp_number=app.config['WHATSAPP_ADMIN_NUMBER'])
    
    return render_template('index.html', whatsapp_number=app.config['WHATSAPP_ADMIN_NUMBER'])

@app.route('/about')
def about():
    """About page"""
    return render_template('about.html', whatsapp_number=app.config['WHATSAPP_ADMIN_NUMBER'])

@app.route('/contact')
def contact():
    """Contact page"""
    return render_template('contact.html', whatsapp_number=app.config['WHATSAPP_ADMIN_NUMBER'])

@app.route('/faq')
def faq():
    """FAQ page"""
    return render_template('faq.html', whatsapp_number=app.config['WHATSAPP_ADMIN_NUMBER'])

# ============================================
# ROUTES - AUTHENTICATION
# ============================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """User login"""
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
            user = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if user and verify_password(user['password'], password):
                if user['status'] != 'active':
                    flash('Akun Anda dinonaktifkan. Hubungi admin.', 'danger')
                    return redirect(url_for('login'))
                
                session['user_id'] = user['id']
                session['user_name'] = user['nama']
                session['user_role'] = user['role']
                session['user_email'] = user['email']
                session.permanent = True
                
                conn = get_db_connection()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE users SET last_login = NOW() WHERE id = %s", (user['id'],))
                    conn.commit()
                    cursor.close()
                    conn.close()
                
                flash('Login berhasil!', 'success')
                
                if user['role'] == 'admin':
                    return redirect(url_for('admin_dashboard'))
                else:
                    return redirect(url_for('user_dashboard'))
            else:
                flash('Email atau password salah', 'danger')
    
    return render_template('auth/login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    """User registration"""
    if request.method == 'POST':
        nama = request.form.get('nama')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        nik = request.form.get('nik')
        no_telepon = request.form.get('no_telepon')
        alamat = request.form.get('alamat')
        tanggal_lahir = request.form.get('tanggal_lahir')
        
        errors = []
        if password != confirm_password:
            errors.append('Password dan konfirmasi password tidak cocok')
        
        if not validate_nik(nik):
            errors.append('NIK harus 16 digit angka')
        
        if len(password) < 6:
            errors.append('Password minimal 6 karakter')
        
        if errors:
            for error in errors:
                flash(error, 'danger')
            return redirect(url_for('register'))
        
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT id FROM users WHERE email = %s OR nik = %s", (email, nik))
            existing_user = cursor.fetchone()
            
            if existing_user:
                flash('Email atau NIK sudah terdaftar', 'danger')
                cursor.close()
                conn.close()
                return redirect(url_for('register'))
            
            hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            
            cursor.execute("""
                INSERT INTO users (nama, email, password, nik, no_telepon, alamat, tanggal_lahir, role, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'customer', 'active')
            """, (nama, email, hashed_password, nik, no_telepon, alamat, tanggal_lahir))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            flash('Registrasi berhasil! Silakan login.', 'success')
            return redirect(url_for('login'))
    
    return render_template('auth/register.html')

@app.route('/logout')
def logout():
    """User logout"""
    session.clear()
    flash('Anda telah logout', 'info')
    return redirect(url_for('index'))

# ============================================
# ROUTES - CUSTOMER DASHBOARD
# ============================================

@app.route('/dashboard')
@login_required
def user_dashboard():
    """Customer dashboard"""
    user_id = session['user_id']
    
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        
        cursor.execute("""
            SELECT p.*, m.merk, m.model, m.gambar 
            FROM pesanan p 
            JOIN mobil m ON p.mobil_id = m.id 
            WHERE p.user_id = %s 
            ORDER BY p.tanggal_pemesanan DESC 
            LIMIT 5
        """, (user_id,))
        recent_orders = cursor.fetchall()
        
        cursor.execute("""
            SELECT 
                COUNT(CASE WHEN status = 'dikonfirmasi' THEN 1 END) as active_orders,
                COUNT(CASE WHEN status_pembayaran = 'paid' THEN 1 END) as paid_orders,
                SUM(CASE WHEN status_pembayaran = 'paid' THEN total_harga ELSE 0 END) as total_spent
            FROM pesanan 
            WHERE user_id = %s
        """, (user_id,))
        stats = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        return render_template('user/dashboard.html', 
                             user=user, 
                             recent_orders=recent_orders,
                             stats=stats,
                             whatsapp_number=app.config['WHATSAPP_ADMIN_NUMBER'])
    
    return redirect(url_for('index'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def user_profile():
    """Customer profile"""
    user_id = session['user_id']
    
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        
        if request.method == 'POST':
            nama = request.form.get('nama')
            no_telepon = request.form.get('no_telepon')
            alamat = request.form.get('alamat')
            tanggal_lahir = request.form.get('tanggal_lahir')
            
            foto_profil = None
            if 'foto_profil' in request.files:
                file = request.files['foto_profil']
                if file and file.filename != '':
                    foto_profil = save_file(file, app.config['PROFILE_UPLOAD_FOLDER'])
            
            update_query = "UPDATE users SET nama = %s, no_telepon = %s, alamat = %s, tanggal_lahir = %s"
            params = [nama, no_telepon, alamat, tanggal_lahir]
            
            if foto_profil:
                update_query += ", foto_profil = %s"
                params.append(foto_profil)
            
            update_query += " WHERE id = %s"
            params.append(user_id)
            
            cursor.execute(update_query, tuple(params))
            conn.commit()
            session['user_name'] = nama
            
            flash('Profil berhasil diperbarui!', 'success')
            return redirect(url_for('user_profile'))
        
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        return render_template('user/profile.html', user=user)
    
    return redirect(url_for('index'))

# ============================================
# ROUTES - CAR CATALOG
# ============================================

@app.route('/catalog')
def catalog():
    """Car catalog with filters"""
    tipe = request.args.get('tipe')
    min_harga = request.args.get('min_harga')
    max_harga = request.args.get('max_harga')
    transmisi = request.args.get('transmisi')
    min_kapasitas = request.args.get('min_kapasitas')
    search = request.args.get('search')
    page = request.args.get('page', 1, type=int)
    
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        
        query = "FROM mobil WHERE status = 'tersedia'"
        params = []
        
        if tipe and tipe != 'all':
            query += " AND tipe = %s"
            params.append(tipe)
        
        if min_harga:
            query += " AND harga_per_hari >= %s"
            params.append(min_harga)
        
        if max_harga:
            query += " AND harga_per_hari <= %s"
            params.append(max_harga)
        
        if transmisi and transmisi != 'all':
            query += " AND transmisi = %s"
            params.append(transmisi)
        
        if min_kapasitas:
            query += " AND kapasitas >= %s"
            params.append(min_kapasitas)
        
        if search:
            query += " AND (merk LIKE %s OR model LIKE %s OR plat_nomor LIKE %s)"
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
        
        count_query = f"SELECT COUNT(*) as total {query}"
        cursor.execute(count_query, tuple(params))
        total = cursor.fetchone()['total']
        
        offset = (page - 1) * app.config['ITEMS_PER_PAGE']
        car_query = f"SELECT * {query} ORDER BY harga_per_hari ASC LIMIT %s OFFSET %s"
        params.extend([app.config['ITEMS_PER_PAGE'], offset])
        
        cursor.execute(car_query, tuple(params))
        cars = cursor.fetchall()
        
        cursor.execute("SELECT DISTINCT tipe FROM mobil WHERE status = 'tersedia'")
        car_types = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        total_pages = (total + app.config['ITEMS_PER_PAGE'] - 1) // app.config['ITEMS_PER_PAGE']
        
        return render_template('user/catalog.html', 
                             cars=cars, 
                             car_types=car_types,
                             filters=request.args,
                             page=page,
                             total_pages=total_pages,
                             total=total,
                             whatsapp_number=app.config['WHATSAPP_ADMIN_NUMBER'])
    
    return render_template('user/catalog.html', cars=[], car_types=[], whatsapp_number=app.config['WHATSAPP_ADMIN_NUMBER'])

@app.route('/car/<int:car_id>')
def car_detail(car_id):
    """Car detail page"""
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT * FROM mobil WHERE id = %s", (car_id,))
        car = cursor.fetchone()
        
        if not car:
            flash('Mobil tidak ditemukan', 'danger')
            return redirect(url_for('catalog'))
        
        cursor.execute("""
            SELECT * FROM mobil 
            WHERE tipe = %s AND id != %s AND status = 'tersedia' 
            LIMIT 4
        """, (car['tipe'], car_id))
        similar_cars = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        return render_template('user/car_detail.html', 
                             car=car, 
                             similar_cars=similar_cars,
                             whatsapp_number=app.config['WHATSAPP_ADMIN_NUMBER'])
    
    return redirect(url_for('catalog'))

# ============================================
# ROUTES - BOOKING & PAYMENT (MIDTRANS INTEGRATION)
# ============================================

@app.route('/booking/<int:car_id>', methods=['GET', 'POST'])
@login_required
def booking(car_id):
    """Create booking"""
    user_id = session['user_id']
    
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM mobil WHERE id = %s", (car_id,))
        car = cursor.fetchone()
        
        if not car or car['status'] != 'tersedia':
            flash('Mobil tidak tersedia untuk disewa', 'danger')
            return redirect(url_for('catalog'))
        
        if request.method == 'POST':
            tanggal_mulai = request.form.get('tanggal_mulai')
            tanggal_selesai = request.form.get('tanggal_selesai')
            lokasi_penjemputan = request.form.get('lokasi_penjemputan')
            catatan = request.form.get('catatan')
            
            try:
                start_date = datetime.strptime(tanggal_mulai, '%Y-%m-%d')
                end_date = datetime.strptime(tanggal_selesai, '%Y-%m-%d')
                durasi_hari = (end_date - start_date).days + 1
                
                if durasi_hari < 1:
                    flash('Durasi sewa minimal 1 hari', 'danger')
                    return redirect(url_for('booking', car_id=car_id))
                
                if durasi_hari > app.config['MAX_RENTAL_DAYS']:
                    flash(f'Maksimal sewa adalah {app.config["MAX_RENTAL_DAYS"]} hari', 'danger')
                    return redirect(url_for('booking', car_id=car_id))
                
                total_harga = car['harga_per_hari'] * durasi_hari
                kode_pesanan = generate_booking_code()
                
                cursor.execute("""
                    INSERT INTO pesanan (
                        kode_pesanan, user_id, mobil_id, tanggal_mulai, tanggal_selesai,
                        durasi_hari, total_harga, lokasi_penjemputan, catatan,
                        metode_pembayaran, status_pembayaran, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'midtrans', 'pending', 'pending')
                """, (
                    kode_pesanan, user_id, car_id, tanggal_mulai, tanggal_selesai,
                    durasi_hari, total_harga, lokasi_penjemputan, catatan
                ))
                
                cursor.execute("UPDATE mobil SET status = 'disewa' WHERE id = %s", (car_id,))
                conn.commit()
                
                logger.info(f"Booking created: {kode_pesanan} for user {user_id}")
                flash('Pesanan berhasil dibuat! Silakan lakukan pembayaran.', 'success')
                return redirect(url_for('payment', order_id=kode_pesanan))
                
            except Exception as e:
                conn.rollback()
                logger.error(f"Error creating booking: {e}")
                flash('Terjadi kesalahan saat membuat pesanan', 'danger')
        
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        tomorrow = (datetime.now() + timedelta(days=app.config['BOOKING_ADVANCE_DAYS'])).strftime('%Y-%m-%d')
        next_week = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')
        
        return render_template('user/booking.html', 
                             car=car, 
                             user=user,
                             min_date=tomorrow,
                             default_end_date=next_week)
    
    return redirect(url_for('catalog'))

@app.route('/payment/<string:order_id>')
@login_required
def payment(order_id):
    """Payment page with Midtrans Snap Pop-up"""
    user_id = session['user_id']
    
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT p.*, m.merk, m.model, m.harga_per_hari, u.nama, u.email, u.no_telepon
            FROM pesanan p 
            JOIN mobil m ON p.mobil_id = m.id 
            JOIN users u ON p.user_id = u.id 
            WHERE p.kode_pesanan = %s AND p.user_id = %s
        """, (order_id, user_id))
        
        order = cursor.fetchone()
        
        if not order:
            flash('Pesanan tidak ditemukan', 'danger')
            return redirect(url_for('user_orders'))
        
        if order['status_pembayaran'] == 'paid':
            flash('Pesanan ini sudah dibayar', 'info')
            return redirect(url_for('order_detail', order_code=order_id))
        
        # Generate Midtrans token if not exists
        if not order.get('midtrans_token'):
            customer_data = {
                "first_name": order['nama'].split()[0] if order['nama'] else 'Customer',
                "last_name": ' '.join(order['nama'].split()[1:]) if len(order['nama'].split()) > 1 else '',
                "email": order['email'],
                "phone": order['no_telepon'] or '081234567890'
            }
            
            order_data = {
                'order_id': order['kode_pesanan'],
                'gross_amount': int(order['total_harga']),
                'first_name': customer_data['first_name'],
                'last_name': customer_data['last_name'],
                'email': customer_data['email'],
                'phone': customer_data['phone'],
                'item_details': [
                    {
                        'id': str(order['mobil_id']),
                        'price': int(order['harga_per_hari']),
                        'quantity': order['durasi_hari'],
                        'name': f"{order['merk']} {order['model']} Rental"
                    }
                ]
            }
            
            snap_token = generate_midtrans_token(order_data)
            
            if snap_token:
                cursor.execute("""
                    UPDATE pesanan 
                    SET midtrans_token = %s,
                        updated_at = NOW()
                    WHERE kode_pesanan = %s
                """, (snap_token, order_id))
                conn.commit()
                order['midtrans_token'] = snap_token
                
                logger.info(f"Midtrans token generated for order {order_id}")
            else:
                flash('Gagal membuat token pembayaran. Coba lagi.', 'danger')
                cursor.close()
                conn.close()
                return redirect(url_for('user_orders'))
        
        cursor.close()
        conn.close()
        
        return render_template('user/payment.html', 
                             order=order, 
                             midtrans_client_key=app.config['MIDTRANS_CLIENT_KEY'],
                             midtrans_token=order['midtrans_token'],
                             midtrans_environment='sandbox' if app.config['MIDTRANS_SANDBOX'] else 'production')
    
    return redirect(url_for('user_orders'))

# ============================================
# FIXED VERSION: MIDTRANS NOTIFICATION HANDLER
# ============================================

@app.route('/payment/notification', methods=['POST'])
def payment_notification():
    """Handle Midtrans payment notification (webhook) - FIXED VERSION"""
    try:
        notification = request.get_json()
        logger.info(f"Midtrans notification received: {notification}")
        
        # Extract important data
        order_id = notification.get('order_id')
        transaction_status = notification.get('transaction_status')
        
        if not order_id:
            logger.error("No order_id in notification")
            return jsonify({'status': 'error', 'message': 'No order_id provided'}), 400
        
        logger.info(f"Processing notification for order: {order_id}, status: {transaction_status}")
        
        # First update the payment status in database
        update_success = update_payment_status(order_id, notification)
        
        # Then insert log for tracking
        if update_success:
            insert_midtrans_log(order_id, notification)
            logger.info(f"Notification processed successfully for order {order_id}")
        else:
            logger.error(f"Failed to update payment status for order {order_id}")
        
        return jsonify({
            'status': 'success' if update_success else 'error',
            'message': 'Notification processed successfully' if update_success else 'Failed to process notification',
            'order_id': order_id,
            'transaction_status': transaction_status
        })
        
    except Exception as e:
        logger.error(f"Error processing Midtrans notification: {str(e)}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/payment/success')
@login_required
def payment_success():
    """Page setelah pembayaran sukses"""
    order_id = request.args.get('order_id')
    if not order_id:
        flash('Parameter tidak valid', 'danger')
        return redirect(url_for('user_orders'))
    
    # Check if payment is really successful
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT status_pembayaran FROM pesanan 
            WHERE kode_pesanan = %s AND user_id = %s
        """, (order_id, session['user_id']))
        
        order = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if order and order['status_pembayaran'] == 'paid':
            flash('Pembayaran berhasil! Pesanan Anda sedang diproses.', 'success')
        else:
            flash('Pembayaran sedang diproses. Status akan diperbarui dalam beberapa saat.', 'info')
    
    return redirect(url_for('order_detail', order_code=order_id))

@app.route('/payment/failed')
@login_required
def payment_failed():
    """Page untuk error pembayaran"""
    order_id = request.args.get('order_id')
    if order_id:
        flash('Pembayaran gagal. Silakan coba lagi atau hubungi customer service.', 'danger')
    return redirect(url_for('user_orders'))

# ============================================
# NEW ROUTE: MANUAL PAYMENT SYNC
# ============================================

@app.route('/api/sync-payment/<string:order_id>', methods=['POST'])
@login_required
def sync_payment_status(order_id):
    """Manual sync payment status from Midtrans"""
    try:
        user_id = session['user_id']
        
        # Check if user owns this order
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT id FROM pesanan WHERE kode_pesanan = %s AND user_id = %s", 
                          (order_id, user_id))
            order = cursor.fetchone()
            
            if not order:
                return jsonify({'success': False, 'message': 'Order not found'}), 404
            
            # Get status from Midtrans API
            server_key = app.config['MIDTRANS_SERVER_KEY']
            auth_string = base64.b64encode(f"{server_key}:".encode()).decode()
            
            headers = {
                'Accept': 'application/json',
                'Authorization': f'Basic {auth_string}',
                'Content-Type': 'application/json'
            }
            
            url = f"https://api.sandbox.midtrans.com/v2/{order_id}/status"
            
            try:
                response = requests.get(url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Call update function
                    success = update_payment_status(order_id, data)
                    
                    if success:
                        return jsonify({
                            'success': True,
                            'message': 'Payment status synced successfully',
                            'midtrans_status': data.get('transaction_status'),
                            'order_id': order_id
                        })
                    else:
                        return jsonify({
                            'success': False,
                            'message': 'Failed to update database'
                        })
                
                elif response.status_code == 404:
                    return jsonify({
                        'success': False,
                        'message': 'Order not found in Midtrans'
                    })
                
                else:
                    return jsonify({
                        'success': False,
                        'message': f'Midtrans API error: {response.status_code}'
                    })
                    
            except requests.exceptions.RequestException as e:
                logger.error(f"Error calling Midtrans API: {e}")
                return jsonify({
                    'success': False,
                    'message': 'Failed to connect to Midtrans API'
                })
        
        return jsonify({'success': False, 'message': 'Database error'}), 500
        
    except Exception as e:
        logger.error(f"Error in sync_payment_status: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

# ============================================
# ROUTES - PAYMENT SUCCESS & INVOICE
# ============================================

@app.route('/payment/success/<string:order_id>')
@login_required
def payment_success_page(order_id):
    """Halaman setelah pembayaran sukses"""
    user_id = session['user_id']
    
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.*, m.merk, m.model, m.plat_nomor, u.nama, u.email
            FROM pesanan p
            JOIN mobil m ON p.mobil_id = m.id
            JOIN users u ON p.user_id = u.id
            WHERE p.kode_pesanan = %s AND p.user_id = %s
        """, (order_id, user_id))
        
        order = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not order:
            flash('Pesanan tidak ditemukan', 'danger')
            return redirect(url_for('user_orders'))
        
        if order['status_pembayaran'] != 'paid':
            flash('Pembayaran belum lunas atau sedang diproses', 'warning')
            return redirect(url_for('order_detail', order_code=order_id))
        
        return render_template('user/payment_success.html', order=order)
    
    return redirect(url_for('user_orders'))

@app.route('/invoice/<string:order_id>')
@login_required
def invoice(order_id):
    """Halaman invoice/struk"""
    user_id = session['user_id']
    
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.*, m.*, u.nama, u.email, u.no_telepon, u.alamat
            FROM pesanan p
            JOIN mobil m ON p.mobil_id = m.id
            JOIN users u ON p.user_id = u.id
            WHERE p.kode_pesanan = %s AND p.user_id = %s
        """, (order_id, user_id))
        
        order = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not order:
            flash('Pesanan tidak ditemukan', 'danger')
            return redirect(url_for('user_orders'))
        
        if order['status_pembayaran'] != 'paid':
            flash('Invoice hanya tersedia untuk pembayaran yang sudah lunas', 'warning')
            return redirect(url_for('order_detail', order_code=order_id))
        
        # Get current date for invoice
        tanggal_sekarang = datetime.now().strftime('%d %B %Y')
        tanggal_cetak = datetime.now().strftime('%d-%m-%Y %H:%M:%S')
        
        return render_template('user/invoice.html',
                             order=order,
                             tanggal_sekarang=tanggal_sekarang,
                             tanggal_cetak=tanggal_cetak,
                             terbilang=terbilang,
                             format_rupiah=format_rupiah)
    
    return redirect(url_for('user_orders'))

@app.route('/payment/history')
@login_required
def payment_history():
    """Riwayat pembayaran user"""
    user_id = session['user_id']
    page = request.args.get('page', 1, type=int)
    
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        
        # Get total count
        cursor.execute("""
            SELECT COUNT(*) as total 
            FROM pesanan 
            WHERE user_id = %s AND status_pembayaran = 'paid'
        """, (user_id,))
        total = cursor.fetchone()['total']
        
        # Get total amount
        cursor.execute("""
            SELECT 
                COALESCE(SUM(total_harga), 0) as total_amount,
                COUNT(DISTINCT mobil_id) as cars_count
            FROM pesanan 
            WHERE user_id = %s AND status_pembayaran = 'paid'
        """, (user_id,))
        stats = cursor.fetchone()
        
        # Get payments
        offset = (page - 1) * app.config['ITEMS_PER_PAGE']
        
        cursor.execute("""
            SELECT p.*, m.merk, m.model, m.gambar
            FROM pesanan p
            JOIN mobil m ON p.mobil_id = m.id
            WHERE p.user_id = %s AND p.status_pembayaran = 'paid'
            ORDER BY COALESCE(p.tanggal_pembayaran, p.tanggal_pemesanan) DESC
            LIMIT %s OFFSET %s
        """, (user_id, app.config['ITEMS_PER_PAGE'], offset))
        
        payments = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        total_pages = (total + app.config['ITEMS_PER_PAGE'] - 1) // app.config['ITEMS_PER_PAGE']
        
        return render_template('user/payment_history.html',
                             payments=payments,
                             page=page,
                             total_pages=total_pages,
                             total=total,
                             total_amount=stats['total_amount'] if stats else 0,
                             cars_count=stats['cars_count'] if stats else 0,
                             config=app.config)
    
    return redirect(url_for('user_dashboard'))

# ============================================
# MANUAL PAYMENT STATUS CHECK (FIXED)
# ============================================

@app.route('/api/check-payment/<string:order_id>', methods=['POST'])
@login_required
def check_payment_status(order_id):
    """Manual check payment status from Midtrans"""
    try:
        user_id = session['user_id']
        
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor(dictionary=True)
            
            # Get order details
            cursor.execute("""
                SELECT p.*, m.merk, m.model
                FROM pesanan p 
                JOIN mobil m ON p.mobil_id = m.id
                WHERE p.kode_pesanan = %s AND p.user_id = %s
            """, (order_id, user_id))
            
            order = cursor.fetchone()
            
            if not order:
                cursor.close()
                conn.close()
                return jsonify({'success': False, 'message': 'Order not found'}), 404
            
            # Jika sudah paid, return langsung
            if order['status_pembayaran'] == 'paid':
                cursor.close()
                conn.close()
                return jsonify({
                    'success': True,
                    'message': 'Payment already successful',
                    'status': 'paid',
                    'order_id': order_id
                })
            
            # Jika ada midtrans_order_id, check status dari Midtrans API
            if order.get('midtrans_order_id'):
                server_key = app.config['MIDTRANS_SERVER_KEY']
                auth_string = base64.b64encode(f"{server_key}:".encode()).decode()
                
                headers = {
                    'Accept': 'application/json',
                    'Authorization': f'Basic {auth_string}',
                    'Content-Type': 'application/json'
                }
                
                url = f"https://api.sandbox.midtrans.com/v2/{order['midtrans_order_id']}/status"
                
                try:
                    response = requests.get(url, headers=headers, timeout=10)
                    
                    if response.status_code == 200:
                        data = response.json()
                        remote_status = data.get('transaction_status')
                        
                        logger.info(f"Midtrans status for {order_id}: {remote_status}")
                        
                        # Update database berdasarkan status dari Midtrans
                        if remote_status in ['settlement', 'capture']:
                            # Call our update function
                            success = update_payment_status(order_id, data)
                            
                            if success:
                                return jsonify({
                                    'success': True,
                                    'message': 'Payment status updated to PAID',
                                    'new_status': 'paid',
                                    'midtrans_status': remote_status
                                })
                        
                        elif remote_status == 'pending':
                            cursor.close()
                            conn.close()
                            return jsonify({
                                'success': True,
                                'message': 'Payment still pending',
                                'status': 'pending',
                                'midtrans_status': remote_status
                            })
                        
                        else:  # failed, expired, etc
                            success = update_payment_status(order_id, data)
                            
                            if success:
                                return jsonify({
                                    'success': True,
                                    'message': 'Payment failed or expired',
                                    'new_status': 'failed',
                                    'midtrans_status': remote_status
                                })
                    
                    else:
                        logger.error(f"Midtrans API error: {response.status_code}")
                        cursor.close()
                        conn.close()
                        return jsonify({
                            'success': False,
                            'message': f'Midtrans API error: {response.status_code}',
                            'current_status': order['status_pembayaran']
                        })
                    
                except requests.exceptions.RequestException as e:
                    logger.error(f"Error calling Midtrans API: {e}")
            
            cursor.close()
            conn.close()
            
            return jsonify({
                'success': False,
                'message': 'Could not check payment status',
                'current_status': order['status_pembayaran']
            })
        
        return jsonify({'success': False, 'message': 'Database error'}), 500
        
    except Exception as e:
        logger.error(f"Error in check_payment_status: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

# ============================================
# FORCE UPDATE PAYMENT STATUS (ADMIN ONLY) - IMPROVED
# ============================================

@app.route('/api/force-update-payment/<string:order_id>', methods=['POST'])
@admin_required
def force_update_payment(order_id):
    """Force update payment status (admin only)"""
    try:
        new_status = request.json.get('status')
        payment_date = request.json.get('payment_date')
        
        if new_status not in ['paid', 'pending', 'failed']:
            return jsonify({'success': False, 'message': 'Invalid status'}), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({'success': False, 'message': 'Database error'}), 500
            
        cursor = conn.cursor(dictionary=True)
        
        # Get order details
        cursor.execute("SELECT id, mobil_id, user_id, total_harga FROM pesanan WHERE kode_pesanan = %s", (order_id,))
        order = cursor.fetchone()
        
        if not order:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'message': 'Order not found'}), 404
        
        # Parse payment date
        payment_date_obj = None
        if payment_date:
            try:
                payment_date_obj = datetime.strptime(payment_date, '%Y-%m-%d %H:%M:%S')
            except:
                payment_date_obj = datetime.now()
        else:
            payment_date_obj = datetime.now()
        
        # Update payment status
        cursor.execute("""
            UPDATE pesanan 
            SET status_pembayaran = %s,
                status = CASE 
                    WHEN %s = 'paid' THEN 'dikonfirmasi'
                    WHEN %s = 'failed' THEN 'dibatalkan'
                    ELSE status
                END,
                tanggal_pembayaran = CASE 
                    WHEN %s = 'paid' THEN %s
                    ELSE NULL
                END,
                updated_at = NOW()
            WHERE kode_pesanan = %s
        """, (new_status, new_status, new_status, new_status, payment_date_obj, order_id))
        
        # Update mobil status
        if new_status == 'paid':
            cursor.execute("UPDATE mobil SET status = 'disewa', updated_at = NOW() WHERE id = %s", 
                          (order['mobil_id'],))
        elif new_status == 'failed':
            cursor.execute("UPDATE mobil SET status = 'tersedia', updated_at = NOW() WHERE id = %s", 
                          (order['mobil_id'],))
        
        # Insert payment record if paid
        if new_status == 'paid':
            cursor.execute("""
                INSERT INTO pembayaran (pesanan_id, jumlah, metode, status, tanggal_pembayaran, created_at)
                SELECT id, total_harga, 'manual_admin', 'success', %s, NOW()
                FROM pesanan 
                WHERE kode_pesanan = %s
                AND NOT EXISTS (
                    SELECT 1 FROM pembayaran pb 
                    WHERE pb.pesanan_id = %s 
                    AND pb.status = 'success'
                )
            """, (payment_date_obj, order_id, order['id']))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info(f"Payment status manually updated to {new_status} for order {order_id} by admin")
        
        return jsonify({
            'success': True,
            'message': f'Payment status updated to {new_status}',
            'order_id': order_id
        })
        
    except Exception as e:
        logger.error(f"Error in force_update_payment: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

# ============================================
# NEW ROUTE: UPDATE PAYMENT STATUS (ADMIN UI)
# ============================================

@app.route('/admin/order/<string:order_code>/update-payment', methods=['POST'])
@admin_required
def admin_order_update_payment(order_code):
    """Update payment status from admin UI"""
    try:
        payment_status = request.form.get('payment_status')
        status = request.form.get('status')
        
        if not payment_status or not status:
            flash('Status pembayaran dan status pesanan harus diisi', 'danger')
            return redirect(url_for('admin_orders'))
        
        conn = get_db_connection()
        if not conn:
            flash('Database error', 'danger')
            return redirect(url_for('admin_orders'))
            
        cursor = conn.cursor(dictionary=True)
        
        # Get order details
        cursor.execute("SELECT id, mobil_id, user_id FROM pesanan WHERE kode_pesanan = %s", (order_code,))
        order = cursor.fetchone()
        
        if not order:
            flash('Pesanan tidak ditemukan', 'danger')
            return redirect(url_for('admin_orders'))
        
        # Update payment status and order status
        cursor.execute("""
            UPDATE pesanan 
            SET status_pembayaran = %s,
                status = %s,
                updated_at = NOW()
            WHERE kode_pesanan = %s
        """, (payment_status, status, order_code))
        
        # Update mobil status based on payment
        if payment_status == 'paid':
            cursor.execute("UPDATE mobil SET status = 'disewa', updated_at = NOW() WHERE id = %s", 
                          (order['mobil_id'],))
        elif payment_status in ['failed', 'pending']:
            cursor.execute("UPDATE mobil SET status = 'tersedia', updated_at = NOW() WHERE id = %s", 
                          (order['mobil_id'],))
        
        # Insert payment record if paid
        if payment_status == 'paid':
            cursor.execute("""
                INSERT INTO pembayaran (pesanan_id, jumlah, metode, status, tanggal_pembayaran, created_at)
                SELECT id, total_harga, 'manual_admin', 'success', NOW(), NOW()
                FROM pesanan 
                WHERE kode_pesanan = %s
                AND NOT EXISTS (
                    SELECT 1 FROM pembayaran pb 
                    WHERE pb.pesanan_id = %s 
                    AND pb.status = 'success'
                )
            """, (order_code, order['id']))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        flash('Status pembayaran dan pesanan berhasil diperbarui', 'success')
        
    except Exception as e:
        logger.error(f"Error updating payment status: {e}")
        flash('Terjadi kesalahan saat memperbarui status', 'danger')
    
    return redirect(url_for('admin_orders'))

# ============================================
# NEW ROUTE: ADMIN ORDER DETAIL WITH PAYMENT CONTROL
# ============================================

@app.route('/admin/order/<string:order_code>')
@admin_required
def admin_order_detail(order_code):
    """Admin order detail with payment control"""
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.*, u.nama as customer_name, u.email, u.no_telepon,
                   m.merk, m.model, m.plat_nomor, m.gambar as car_image,
                   pb.status as payment_record_status, pb.tanggal_pembayaran as payment_record_date
            FROM pesanan p 
            JOIN users u ON p.user_id = u.id 
            JOIN mobil m ON p.mobil_id = m.id 
            LEFT JOIN pembayaran pb ON p.id = pb.pesanan_id AND pb.status = 'success'
            WHERE p.kode_pesanan = %s
        """, (order_code,))
        
        order = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not order:
            flash('Pesanan tidak ditemukan', 'danger')
            return redirect(url_for('admin_orders'))
        
        return render_template('admin/orders/detail.html', order=order)
    
    return redirect(url_for('admin_orders'))

# ============================================
# ROUTES - ORDERS
# ============================================

@app.route('/orders')
@login_required
def user_orders():
    """User's order history"""
    user_id = session['user_id']
    status = request.args.get('status', 'all')
    page = request.args.get('page', 1, type=int)
    
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        
        # Build query based on status
        if status == 'all':
            query = """
                FROM pesanan p 
                JOIN mobil m ON p.mobil_id = m.id 
                WHERE p.user_id = %s
            """
            params = [user_id]
        else:
            query = """
                FROM pesanan p 
                JOIN mobil m ON p.mobil_id = m.id 
                WHERE p.user_id = %s AND p.status_pembayaran = %s
            """
            params = [user_id, status]
        
        count_query = f"SELECT COUNT(*) as total {query}"
        cursor.execute(count_query, tuple(params))
        total = cursor.fetchone()['total']
        
        # Get counts for each status
        cursor.execute("""
            SELECT 
                SUM(CASE WHEN status_pembayaran = 'paid' THEN 1 ELSE 0 END) as paid,
                SUM(CASE WHEN status_pembayaran = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status_pembayaran = 'failed' THEN 1 ELSE 0 END) as failed
            FROM pesanan WHERE user_id = %s
        """, (user_id,))
        counts = cursor.fetchone()
        
        # Build main query
        offset = (page - 1) * app.config['ITEMS_PER_PAGE']
        order_query = f"""
            SELECT p.*, m.merk, m.model, m.gambar {query}
            ORDER BY p.tanggal_pemesanan DESC 
            LIMIT %s OFFSET %s
        """
        params.extend([app.config['ITEMS_PER_PAGE'], offset])
        
        cursor.execute(order_query, tuple(params))
        orders = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        total_pages = (total + app.config['ITEMS_PER_PAGE'] - 1) // app.config['ITEMS_PER_PAGE']
        
        return render_template('user/orders.html', 
                             orders=orders, 
                             page=page,
                             total_pages=total_pages,
                             total=total,
                             status=status,
                             counts=counts)
    
    return redirect(url_for('user_dashboard'))

@app.route('/order/<string:order_code>')
@login_required
def order_detail(order_code):
    """Order detail"""
    user_id = session['user_id']
    
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.*, m.*, u.nama as customer_nama, u.no_telepon, u.email,
                   pb.status as payment_record_status, pb.tanggal_pembayaran as payment_record_date
            FROM pesanan p 
            JOIN mobil m ON p.mobil_id = m.id 
            JOIN users u ON p.user_id = u.id 
            LEFT JOIN pembayaran pb ON p.id = pb.pesanan_id AND pb.status = 'success'
            WHERE p.kode_pesanan = %s AND p.user_id = %s
        """, (order_code, user_id))
        
        order = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not order:
            flash('Pesanan tidak ditemukan', 'danger')
            return redirect(url_for('user_orders'))
        
        return render_template('user/order_detail.html', order=order)
    
    return redirect(url_for('user_orders'))

# ============================================
# ADMIN DASHBOARD ROUTE (LENGKAP)
# ============================================

@app.route('/admin')
@admin_required
def admin_dashboard():
    """Admin dashboard"""
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT COUNT(*) as count FROM users WHERE role = 'customer' AND status = 'active'")
        total_customers = cursor.fetchone()['count']
        
        cursor.execute("SELECT COUNT(*) as count FROM mobil")
        total_cars = cursor.fetchone()['count']
        
        cursor.execute("SELECT COUNT(*) as count FROM pesanan WHERE status = 'dikonfirmasi'")
        active_rentals = cursor.fetchone()['count']
        
        cursor.execute("""
            SELECT COALESCE(SUM(total_harga), 0) as revenue 
            FROM pesanan 
            WHERE status_pembayaran = 'paid' 
            AND DATE(tanggal_pemesanan) = CURDATE()
        """)
        today_revenue = cursor.fetchone()['revenue']
        
        cursor.execute("""
            SELECT p.*, u.nama as customer_name, m.merk, m.model 
            FROM pesanan p 
            JOIN users u ON p.user_id = u.id 
            JOIN mobil m ON p.mobil_id = m.id 
            ORDER BY p.tanggal_pemesanan DESC 
            LIMIT 10
        """)
        recent_orders = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        return render_template('admin/dashboard.html',
                             total_customers=total_customers,
                             total_cars=total_cars,
                             active_rentals=active_rentals,
                             today_revenue=today_revenue,
                             recent_orders=recent_orders)
    
    return render_template('admin/dashboard.html')

# ============================================
# API ROUTES - ADMIN (LENGKAP)
# ============================================

@app.route('/admin/users')
@admin_required
def admin_users():
    """Admin user management"""
    page = request.args.get('page', 1, type=int)
    role = request.args.get('role', 'all')
    status = request.args.get('status', 'active')
    search = request.args.get('search', '')
    
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        
        query = "FROM users WHERE 1=1"
        params = []
        
        if role != 'all':
            query += " AND role = %s"
            params.append(role)
        
        if status != 'all':
            query += " AND status = %s"
            params.append(status)
        
        if search:
            query += " AND (nama LIKE %s OR email LIKE %s OR nik LIKE %s OR no_telepon LIKE %s)"
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%"])
        
        count_query = f"SELECT COUNT(*) as total {query}"
        cursor.execute(count_query, tuple(params))
        total = cursor.fetchone()['total']
        
        offset = (page - 1) * app.config['ITEMS_PER_PAGE']
        user_query = f"SELECT id, nama, email, nik, no_telepon, role, status, foto_profil, created_at, last_login {query} ORDER BY created_at DESC LIMIT %s OFFSET %s"
        params.extend([app.config['ITEMS_PER_PAGE'], offset])
        
        cursor.execute(user_query, tuple(params))
        users = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        total_pages = (total + app.config['ITEMS_PER_PAGE'] - 1) // app.config['ITEMS_PER_PAGE']
        
        return render_template('admin/users/list.html',
                             users=users,
                             page=page,
                             total_pages=total_pages,
                             total=total,
                             role=role,
                             status=status,
                             search=search)
    
    return redirect(url_for('admin_dashboard'))

@app.route('/api/admin/users/add', methods=['POST'])
@admin_required
def api_admin_add_user():
    """API untuk menambahkan user baru"""
    try:
        data = request.json
        
        nama = data.get('nama')
        email = data.get('email')
        password = data.get('password')
        nik = data.get('nik')
        no_telepon = data.get('no_telepon')
        alamat = data.get('alamat')
        tanggal_lahir = data.get('tanggal_lahir')
        role = data.get('role', 'customer')
        status = data.get('status', 'active')
        
        # Validasi
        if not all([nama, email, password, nik]):
            return jsonify({'success': False, 'message': 'Semua field wajib diisi'}), 400
        
        if not validate_nik(nik):
            return jsonify({'success': False, 'message': 'NIK harus 16 digit angka'}), 400
        
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor(dictionary=True)
            
            # Check if email or NIK already exists
            cursor.execute("SELECT id FROM users WHERE email = %s OR nik = %s", (email, nik))
            existing_user = cursor.fetchone()
            
            if existing_user:
                cursor.close()
                conn.close()
                return jsonify({'success': False, 'message': 'Email atau NIK sudah terdaftar'}), 400
            
            # Hash password
            hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            
            # Insert user
            cursor.execute("""
                INSERT INTO users (nama, email, password, nik, no_telepon, alamat, tanggal_lahir, role, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (nama, email, hashed_password, nik, no_telepon, alamat, tanggal_lahir, role, status))
            
            user_id = cursor.lastrowid
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"User {nama} added successfully by admin {session['user_id']}")
            
            return jsonify({
                'success': True,
                'message': 'User berhasil ditambahkan',
                'user_id': user_id,
                'user': {
                    'nama': nama,
                    'email': email,
                    'role': role,
                    'status': status
                }
            })
        
        return jsonify({'success': False, 'message': 'Database error'}), 500
        
    except Exception as e:
        logger.error(f"Error in api_admin_add_user: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/admin/user/<int:user_id>/toggle', methods=['POST'])
@admin_required
def admin_toggle_user(user_id):
    """Toggle user status (active/inactive)"""
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        
        # Get current status
        cursor.execute("SELECT status FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        
        if not user:
            flash('Pengguna tidak ditemukan', 'danger')
            return redirect(url_for('admin_users'))
        
        # Toggle status
        new_status = 'inactive' if user['status'] == 'active' else 'active'
        
        cursor.execute("UPDATE users SET status = %s WHERE id = %s", (new_status, user_id))
        conn.commit()
        
        cursor.close()
        conn.close()
        
        status_text = 'dinonaktifkan' if new_status == 'inactive' else 'diaktifkan'
        flash(f'Status pengguna berhasil {status_text}', 'success')
    
    return redirect(url_for('admin_users'))

@app.route('/admin/user/<int:user_id>/delete', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    """Delete user"""
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        
        # Check if user exists
        cursor.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        if not cursor.fetchone():
            flash('Pengguna tidak ditemukan', 'danger')
            return redirect(url_for('admin_users'))
        
        # Delete user
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
        
        cursor.close()
        conn.close()
        
        flash('Pengguna berhasil dihapus', 'success')
    
    return redirect(url_for('admin_users'))

@app.route('/admin/user/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_user_edit(user_id):
    """Edit user"""
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        
        if request.method == 'POST':
            nama = request.form.get('nama')
            email = request.form.get('email')
            nik = request.form.get('nik')
            no_telepon = request.form.get('no_telepon')
            alamat = request.form.get('alamat')
            tanggal_lahir = request.form.get('tanggal_lahir')
            role = request.form.get('role')
            status = request.form.get('status')
            
            cursor.execute("""
                UPDATE users 
                SET nama = %s, email = %s, nik = %s, no_telepon = %s, 
                    alamat = %s, tanggal_lahir = %s, role = %s, status = %s
                WHERE id = %s
            """, (nama, email, nik, no_telepon, alamat, tanggal_lahir, role, status, user_id))
            
            conn.commit()
            flash('Data pengguna berhasil diperbarui', 'success')
            return redirect(url_for('admin_users'))
        
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if not user:
            flash('Pengguna tidak ditemukan', 'danger')
            return redirect(url_for('admin_users'))
        
        return render_template('admin/users/edit.html', user=user)
    
    return redirect(url_for('admin_dashboard'))

# ============================================
# ROUTES - ADMIN CARS (LENGKAP)
# ============================================

@app.route('/admin/cars')
@admin_required
def admin_cars():
    """Admin car management"""
    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', 'all')
    search = request.args.get('search', '')
    
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        
        query = "FROM mobil WHERE 1=1"
        params = []
        
        if status != 'all':
            query += " AND status = %s"
            params.append(status)
        
        if search:
            query += " AND (merk LIKE %s OR model LIKE %s OR plat_nomor LIKE %s)"
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
        
        count_query = f"SELECT COUNT(*) as total {query}"
        cursor.execute(count_query, tuple(params))
        total = cursor.fetchone()['total']
        
        offset = (page - 1) * app.config['ITEMS_PER_PAGE']
        car_query = f"SELECT * {query} ORDER BY created_at DESC LIMIT %s OFFSET %s"
        params.extend([app.config['ITEMS_PER_PAGE'], offset])
        
        cursor.execute(car_query, tuple(params))
        cars = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        total_pages = (total + app.config['ITEMS_PER_PAGE'] - 1) // app.config['ITEMS_PER_PAGE']
        
        return render_template('admin/cars/list.html',
                             cars=cars,
                             page=page,
                             total_pages=total_pages,
                             total=total,
                             status=status,
                             search=search)
    
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/car/add', methods=['GET', 'POST'])
@admin_required
def admin_car_add():
    """Add new car"""
    if request.method == 'POST':
        merk = request.form.get('merk')
        model = request.form.get('model')
        tahun = request.form.get('tahun')
        plat_nomor = request.form.get('plat_nomor')
        tipe = request.form.get('tipe')
        transmisi = request.form.get('transmisi')
        kapasitas = request.form.get('kapasitas')
        harga_per_hari = request.form.get('harga_per_hari')
        deskripsi = request.form.get('deskripsi')
        
        gambar = None
        if 'gambar' in request.files:
            file = request.files['gambar']
            if file and file.filename != '':
                gambar = save_file(file, app.config['CAR_UPLOAD_FOLDER'])
        
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO mobil (merk, model, tahun, plat_nomor, tipe, 
                                transmisi, kapasitas, harga_per_hari, deskripsi, gambar, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'tersedia')
            """, (merk, model, tahun, plat_nomor, tipe, transmisi, 
                  kapasitas, harga_per_hari, deskripsi, gambar))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            flash('Mobil berhasil ditambahkan', 'success')
            return redirect(url_for('admin_cars'))
    
    # TAMBAHKAN INI UNTUK MENGIRIM VARIABEL KE TEMPLATE
    now_year = datetime.now().year
    return render_template('admin/cars/add.html', now_year=now_year)

@app.route('/admin/car/<int:car_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_car_edit(car_id):
    """Edit car"""
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        
        if request.method == 'POST':
            merk = request.form.get('merk')
            model = request.form.get('model')
            tahun = request.form.get('tahun')
            plat_nomor = request.form.get('plat_nomor')
            tipe = request.form.get('tipe')
            transmisi = request.form.get('transmisi')
            kapasitas = request.form.get('kapasitas')
            harga_per_hari = request.form.get('harga_per_hari')
            deskripsi = request.form.get('deskripsi')
            status = request.form.get('status')
            
            gambar = None
            if 'gambar' in request.files:
                file = request.files['gambar']
                if file and file.filename != '':
                    gambar = save_file(file, app.config['CAR_UPLOAD_FOLDER'])
            
            update_query = """
                UPDATE mobil 
                SET merk = %s, model = %s, tahun = %s, plat_nomor = %s, tipe = %s,
                    transmisi = %s, kapasitas = %s, harga_per_hari = %s, deskripsi = %s, status = %s
            """
            params = [merk, model, tahun, plat_nomor, tipe, transmisi, 
                     kapasitas, harga_per_hari, deskripsi, status]
            
            if gambar:
                update_query = update_query.replace("status = %s", "gambar = %s, status = %s")
                params.insert(-1, gambar)
            
            update_query += " WHERE id = %s"
            params.append(car_id)
            
            cursor.execute(update_query, tuple(params))
            conn.commit()
            
            flash('Data mobil berhasil diperbarui', 'success')
            return redirect(url_for('admin_cars'))
        
        cursor.execute("SELECT * FROM mobil WHERE id = %s", (car_id,))
        car = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if not car:
            flash('Mobil tidak ditemukan', 'danger')
            return redirect(url_for('admin_cars'))
        
        return render_template('admin/cars/edit.html', car=car)
    
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/car/<int:car_id>/delete', methods=['POST'])
@admin_required
def admin_delete_car(car_id):
    """Delete car"""
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        
        # Check if car exists
        cursor.execute("SELECT id FROM mobil WHERE id = %s", (car_id,))
        if not cursor.fetchone():
            flash('Mobil tidak ditemukan', 'danger')
            return redirect(url_for('admin_cars'))
        
        # Delete car
        cursor.execute("DELETE FROM mobil WHERE id = %s", (car_id,))
        conn.commit()
        
        cursor.close()
        conn.close()
        
        flash('Mobil berhasil dihapus', 'success')
    
    return redirect(url_for('admin_cars'))

# ============================================
# ROUTES - ADMIN ORDERS (LENGKAP) - IMPROVED
# ============================================

@app.route('/admin/orders')
@admin_required
def admin_orders():
    """Order management"""
    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', 'all')
    payment_status = request.args.get('payment_status', 'all')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    search = request.args.get('search', '')
    
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor(dictionary=True)
        
        query = """
            FROM pesanan p 
            JOIN users u ON p.user_id = u.id 
            JOIN mobil m ON p.mobil_id = m.id 
            WHERE 1=1
        """
        params = []
        
        if status != 'all':
            query += " AND p.status = %s"
            params.append(status)
        
        if payment_status != 'all':
            query += " AND p.status_pembayaran = %s"
            params.append(payment_status)
        
        if start_date:
            query += " AND DATE(p.tanggal_pemesanan) >= %s"
            params.append(start_date)
        
        if end_date:
            query += " AND DATE(p.tanggal_pemesanan) <= %s"
            params.append(end_date)
        
        if search:
            query += " AND (u.nama LIKE %s OR m.merk LIKE %s OR m.model LIKE %s OR p.kode_pesanan LIKE %s OR u.email LIKE %s)"
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%"])
        
        count_query = f"SELECT COUNT(*) as total {query}"
        cursor.execute(count_query, tuple(params))
        total = cursor.fetchone()['total']
        
        offset = (page - 1) * app.config['ITEMS_PER_PAGE']
        order_query = f"""
            SELECT p.*, u.nama as customer_name, u.email as customer_email, u.no_telepon, 
                   m.merk, m.model, m.plat_nomor, m.gambar as car_image {query}
            ORDER BY p.tanggal_pemesanan DESC LIMIT %s OFFSET %s
        """
        params.extend([app.config['ITEMS_PER_PAGE'], offset])
        
        cursor.execute(order_query, tuple(params))
        orders = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        total_pages = (total + app.config['ITEMS_PER_PAGE'] - 1) // app.config['ITEMS_PER_PAGE']
        
        return render_template('admin/orders/list.html',
                             orders=orders,
                             page=page,
                             total_pages=total_pages,
                             total=total,
                             status=status,
                             payment_status=payment_status,
                             start_date=start_date,
                             end_date=end_date,
                             search=search)
    
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/order/<string:order_code>/update', methods=['POST'])
@admin_required
def admin_order_update(order_code):
    """Update order status"""
    status = request.form.get('status')
    
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE pesanan 
            SET status = %s 
            WHERE kode_pesanan = %s
        """, (status, order_code))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        flash('Status pesanan berhasil diperbarui', 'success')
    
    return redirect(url_for('admin_orders'))

# ============================================
# API ROUTES - ADMIN (LENGKAP)
# ============================================

@app.route('/api/admin/user/<int:user_id>')
@admin_required
def api_admin_user_detail(user_id):
    """API untuk mendapatkan detail user"""
    try:
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor(dictionary=True)
            
            # Get user details
            cursor.execute("""
                SELECT u.*, 
                       COUNT(p.id) as order_count,
                       COALESCE(SUM(p.total_harga), 0) as total_spent
                FROM users u
                LEFT JOIN pesanan p ON u.id = p.user_id AND p.status_pembayaran = 'paid'
                WHERE u.id = %s
                GROUP BY u.id
            """, (user_id,))
            
            user = cursor.fetchone()
            
            cursor.close()
            conn.close()
            
            if user:
                return jsonify({
                    'success': True,
                    'user': user
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'User not found'
                }), 404
        
        return jsonify({'success': False, 'message': 'Database error'}), 500
        
    except Exception as e:
        logger.error(f"Error in api_admin_user_detail: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/admin/order/<string:order_code>')
@admin_required
def api_admin_order_detail(order_code):
    """API untuk detail pesanan"""
    try:
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("""
                SELECT p.*, u.nama as customer_name, u.email as customer_email, 
                       u.no_telepon as customer_phone, m.merk, m.model, m.plat_nomor,
                       m.tipe as car_type, m.harga_per_hari as daily_rate,
                       pb.status as payment_record_status, pb.tanggal_pembayaran as payment_date
                FROM pesanan p
                JOIN users u ON p.user_id = u.id
                JOIN mobil m ON p.mobil_id = m.id
                LEFT JOIN pembayaran pb ON p.id = pb.pesanan_id AND pb.status = 'success'
                WHERE p.kode_pesanan = %s
            """, (order_code,))
            
            order = cursor.fetchone()
            
            cursor.close()
            conn.close()
            
            if order:
                return jsonify({
                    'success': True,
                    'order': order
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'Order not found'
                }), 404
        
        return jsonify({'success': False, 'message': 'Database error'}), 500
        
    except Exception as e:
        logger.error(f"Error in api_admin_order_detail: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

# ============================================
# UTILITY ROUTES (LENGKAP)
# ============================================

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    """Serve uploaded files"""
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    except:
        # Return placeholder image if file doesn't exist
        return send_from_directory('static/img', 'car-placeholder.jpg')

@app.route('/static/img/<path:filename>')
def serve_static_img(filename):
    """Serve static images"""
    try:
        return send_from_directory('static/img', filename)
    except:
        # Return default placeholder
        return "Image not found", 404

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy', 
        'timestamp': datetime.now().isoformat(),
        'database': 'connected' if get_db_connection() else 'disconnected',
        'midtrans': 'sandbox' if app.config['MIDTRANS_SANDBOX'] else 'production'
    })

# ============================================
# ERROR HANDLERS (LENGKAP)
# ============================================

@app.errorhandler(404)
def page_not_found(e):
    """Handle 404 errors"""
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    """Handle 500 errors"""
    logger.error(f"500 Error: {e}")
    return render_template('errors/500.html'), 500

@app.errorhandler(403)
def forbidden(e):
    """Handle 403 errors"""
    return render_template('errors/403.html'), 403

# ============================================
# DATABASE TABLES CREATION
# ============================================

def create_tables():
    """Create database tables if they don't exist"""
    try:
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            
            # Read SQL file and execute
            sql_file = 'rental_mobil.sql'
            if os.path.exists(sql_file):
                with open(sql_file, 'r', encoding='utf-8') as f:
                    sql_commands = f.read()
                
                # Execute SQL commands
                for result in cursor.execute(sql_commands, multi=True):
                    if result.with_rows:
                        result.fetchall()
                
                conn.commit()
                print("Database tables created successfully")
            else:
                print(f"SQL file {sql_file} not found")
            
            cursor.close()
            conn.close()
        else:
            print("Failed to connect to database")
    except Exception as e:
        print(f"Error creating tables: {e}")

# ============================================
# SETUP PLACEHOLDER IMAGES
# ============================================

def setup_placeholder_images():
    """Setup placeholder images if they don't exist"""
    import urllib.request
    
    # Create static/img directory
    os.makedirs('static/img', exist_ok=True)
    
    # Download placeholder image if doesn't exist
    placeholder_path = 'static/img/car-placeholder.jpg'
    if not os.path.exists(placeholder_path):
        try:
            # Download a placeholder image
            url = 'https://placehold.co/600x400/003366/FFFFFF/png?text=Car+Rental'
            urllib.request.urlretrieve(url, placeholder_path)
            print(f"Downloaded placeholder image to {placeholder_path}")
        except:
            # Create a simple colored image
            from PIL import Image, ImageDraw
            img = Image.new('RGB', (600, 400), color='#003366')
            draw = ImageDraw.Draw(img)
            img.save(placeholder_path)
            print(f"Created placeholder image at {placeholder_path}")

# ============================================
# TEST WEBHOOK FUNCTION
# ============================================

def test_midtrans_webhook(order_id):
    """Test Midtrans webhook manually"""
    import json
    
    test_data = {
        "order_id": order_id,
        "transaction_status": "settlement",
        "transaction_id": f"TEST-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "payment_type": "qris",
        "gross_amount": 1750000,
        "settlement_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "fraud_status": "accept"
    }
    
    # Simulate webhook call
    with app.test_client() as client:
        response = client.post('/payment/notification', 
                              data=json.dumps(test_data),
                              content_type='application/json')
        
        print(f"Test webhook response: {response.status_code}")
        print(f"Response data: {response.get_json()}")
        
        return response

# ============================================
# MAIN ENTRY POINT
# ============================================

if __name__ == '__main__':
    # Create necessary directories
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['CAR_UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['PROFILE_UPLOAD_FOLDER'], exist_ok=True)
    
    # Create static folders
    os.makedirs('static/css', exist_ok=True)
    os.makedirs('static/js', exist_ok=True)
    os.makedirs('static/img', exist_ok=True)
    
    # Setup placeholder images
    setup_placeholder_images()
    
    print("=" * 60)
    print("RENTAL MOBIL FLASK - MIDTRANS (TANPA CHAT & REPORT)")
    print("=" * 60)
    print("FITUR YANG DIHAPUS:")
    print("   1. Chat internal - diganti dengan WhatsApp")
    print("   2. Report/laporan keuangan")
    print("=" * 60)
    print("Admin Login:")
    print("Email: admin@rental.com")
    print("Password: admin123")
    print("=" * 60)
    print(f"Admin WhatsApp: {app.config['WHATSAPP_ADMIN_NUMBER']}")
    print("=" * 60)
    
    app.run( 
            debug=True, 
            host='0.0.0.0', 
            port=5000, 

            )



