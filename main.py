from attrs import has
from fastapi import FastAPI, Form, UploadFile, File, Query, HTTPException, Response
from pydantic import BaseModel
import pymysql
from passlib.context import CryptContext
import cloudinary
import cloudinary.uploader
import firebase_admin
from firebase_admin import credentials, messaging
from typing import List, Optional
import random
from datetime import datetime, timedelta
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
import warnings
import os
import json
# --- PYTHON 3.13 BCRYPT LOG CLEANER ---
# Passlib ki internal library warnings ko hide karne ke liye
warnings.filterwarnings("ignore", category=UserWarning, module="passlib")
# --- 1. INITIALIZE FASTAPI APP ---
app = FastAPI(title="CAIT AAU Hostel Portal Backend")
# --- 2. GLOBAL SECURITY CRYPT CONTEXT ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# --- 3. THIRD-PARTY PLATFORM CONFIGURATIONS ---
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
OFFICIAL_SENDER_EMAIL = "caithostelhelp@gmail.com"
OFFICIAL_SENDER_NAME = "CAIT AAU Hostel Administration"
cloudinary.config(
    cloud_name="daipbiuep",
    api_key="618276459223625",
    api_secret=os.getenv("secret_cloudinary_key")
)
firebase_key = json.loads(os.environ["FIREBASE_KEY"])
cred = credentials.Certificate(firebase_key)
firebase_admin.initialize_app(cred)
# --- 4. DATABASE CONNECTION ---
def get_connection():
    return pymysql.connect(
        host="gateway01.ap-southeast-1.prod.alicloud.tidbcloud.com",
        port=4000,
        user="73UgtMJvpWa52hF.root",
        password=os.getenv("PASS"),
        database="hostel_app",
        cursorclass=pymysql.cursors.DictCursor,
        
        # ✅ IMPORTANT FIX
        ssl={
            "ssl": {
                "ca": None
            }
        }
    )
def send_brevo_email(to_email: str, subject: str, html_content: str) -> str:
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key['api-key'] = BREVO_API_KEY
    api_instance = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))
    send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": to_email}],
        sender={"name": OFFICIAL_SENDER_NAME, "email": OFFICIAL_SENDER_EMAIL},
        subject=subject,
        html_content=html_content
    )
    try:
        api_instance.send_transac_email(send_smtp_email)
        return "Sent"
    except ApiException:
        return "Failed"
def log_communication(cur, user_id, receiver_type: str, channel: str, subject: str, message: str, status: str):
    cur.execute("""
        INSERT INTO communication_logs (user_id, receiver_type, channel, subject, message, status)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (user_id, receiver_type, channel, subject, message, status))
def send_notification(token, title, body):
    try:
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            token=token
        )
        messaging.send(message)
        print(f"Successfully sent message to token: {token[:10]}...")
    except messaging.UnregisteredError:
        print(f"Stale FCM token detected: {token[:10]}... skipping sending.")
    except Exception as e:
        print(f"FCM Notification failed: {e}")
# --- 📱 GLOBAL OUTBOX QUEUE FOR YOUR ANDROID GATEWAY APP ---
SMS_OUTBOX_QUEUE = []
def send_custom_sms_gateway(phone_no: str, message: str) -> str:
    try:
        clean_phone = "".join(filter(str.isdigit, phone_no))
        if len(clean_phone) > 10 and clean_phone.startswith("91"):
            clean_phone = clean_phone[2:]
        sms_payload = {
            "phone": clean_phone,
            "msg": message
        }
        SMS_OUTBOX_QUEUE.append(sms_payload)
        print(f"📥 [Custom Queue] SMS Added to Outbox Queue for {clean_phone}")
        return "Sent"
    except Exception as e:
        print(f"❌ [Queue Failed] Error: {str(e)}")
        return "Failed"
# --- 5. PYDANTIC VALIDATION MODELS ---
class OTPModel(BaseModel):
    email: str
class VerifyOTPModel(BaseModel):
    email: str
    otp: str
class RegisterModel(BaseModel):
    name: str
    enrollment_no: str
    email: str
    phone_no: str
    semester: int
    dob: str
    parent_email: str
    parent_phone: str
    address: str
    password: str
    role: str = "Student"
    fcm_token: str
    room_no: Optional[str] = None
    floor_no: Optional[str] = None
    otp_entered: str
class LoginModel(BaseModel):
    email: str
    password: str
    fcm_token: str
class LogoutModel(BaseModel):
    fcm_token: str
class ComplaintModel(BaseModel):
    student_id: int
    title: str
    complaint_text: str
    complaint_photo: str
class RemarkModel(BaseModel):
    complaint_id: int
    user_id: int
    remark: str
    photo: str = ""
class SolveComplaintModel(BaseModel):
    complaint_id: int
    user_id: int
    remark: str
    photo: str = ""
class StudentFeedbackModel(BaseModel):
    complaint_id: int
    user_id: int
    remark: str
class WardenSolveModel(BaseModel):
    complaint_id: int
    status: str
    remarks: str
# --- 6. CORE OPERATIONAL WEB ENDPOINTS ---
@app.get("/get-sms")
def get_sms(response: Response):
    """Android app polling endpoint with Cloudflare/Ngrok bypass headers"""
    global SMS_OUTBOX_QUEUE
    response.headers["ngrok-skip-browser-warning"] = "true"
    response.headers["cloudflare-skip-browser-warning"] = "true"
    if not SMS_OUTBOX_QUEUE:
        return []
    pending_sms = list(SMS_OUTBOX_QUEUE)
    SMS_OUTBOX_QUEUE.clear()
    print(f"🚀 [App Polling] Dispatched {len(pending_sms)} messages to Android App Gateway.")
    return pending_sms
@app.get("/")
def root_sms_backup(response: Response):
    """Backup root path bypass endpoint"""
    global SMS_OUTBOX_QUEUE
    response.headers["ngrok-skip-browser-warning"] = "true"
    response.headers["cloudflare-skip-browser-warning"] = "true"
    if not SMS_OUTBOX_QUEUE:
        return []
    pending_sms = list(SMS_OUTBOX_QUEUE)
    SMS_OUTBOX_QUEUE.clear()
    return pending_sms
@app.post("/send-registration-otp")
def send_registration_otp(data: OTPModel):
    otp = str(random.randint(100000, 999999))
    expiry = datetime.now() + timedelta(minutes=10)
    con = get_connection()
    cur = con.cursor()
    try:
        cur.execute("SELECT user_id FROM users WHERE email=%s", (data.email,))
        if cur.fetchone():
            return {"success": False, "message": "Email already registered"}
        cur.execute("""
            INSERT INTO otp_verifications (email, otp_code, expires_at, is_verified)
            VALUES (%s, %s, %s, 0)
        """, (data.email, otp, expiry))
        email_body = f"""
        <h3>Arya Boys Hostel, Anand Agricultural University,Anand ,Hostel Verification</h3>
        <p>Your OTP verification token for registration is:</p>
        <h2 style='color: #008080;'>{otp}</h2>
        <p>Valid for 10 minutes only.</p>
        """
        email_status = send_brevo_email(data.email, "Registration Verification Token Code", email_body)
        log_communication(cur, None, "Student", "Email", "Registration Verification Token Code", email_body, email_status)
        con.commit()
        return {"success": True, "message": "OTP Dispatched Over Verified Email Pipeline!"}
    finally:
        cur.close()
        con.close()
@app.post("/verify-registration-otp")
def verify_registration_otp(data: VerifyOTPModel):
    con = get_connection()
    cur = con.cursor()
    try:
        cur.execute("""
            SELECT * FROM otp_verifications
            WHERE email=%s AND otp_code=%s AND is_verified=0
            ORDER BY otp_id DESC LIMIT 1
        """, (data.email, data.otp))
        otp_data = cur.fetchone()
        if not otp_data:
            return {"success": False, "message": "Invalid OTP"}
        if datetime.now() > otp_data["expires_at"]:
            return {"success": False, "message": "OTP Expired"}
        cur.execute("UPDATE otp_verifications SET is_verified=1 WHERE otp_id=%s", (otp_data["otp_id"],))
        con.commit()
        return {"success": True, "message": "OTP Verified Successfully!"}
    finally:
        cur.close()
        con.close()
@app.post("/register")
def register(data: RegisterModel):
    con = get_connection()
    cur = con.cursor()
    try:
        # 1. Duplicate Email Check
        cur.execute("SELECT user_id FROM users WHERE email=%s", (data.email,))
        if cur.fetchone():
            return {"success": False, "message": "Email already exists"}
        # 2. Strict Match Verification Check against the input field code
        cur.execute("""
            SELECT * FROM otp_verifications
            WHERE email=%s AND otp_code=%s AND expires_at > NOW()
            ORDER BY otp_id DESC LIMIT 1
        """, (data.email, data.otp_entered))
        otp_record = cur.fetchone()
        if not otp_record:
            return {"success": False, "message": "Invalid OTP Code or session expired. Verification failed."}
        # -------------------------------------------------------------------------
        # 🏢 NEW CRITICAL STEP: ROOM CAPACITY & MAX OCCUPANCY CHECK (Max 3)
        # -------------------------------------------------------------------------
        room_input = data.room_no.strip() if data.room_no else ""
        floor_input = data.floor_no.strip() if data.floor_no else ""
        # Agar input khali ya default string nahi hai, matlab valid room allocate ho raha hai
        final_room = None if room_input in ["", "Not Assigned", "null", "None"] else room_input
        final_floor = None if floor_input in ["", "Not Assigned", "null", "None"] else floor_input
        if final_room:
            # Check current occupancy from rooms table
            cur.execute("SELECT current_occupancy FROM rooms WHERE room_no = %s", (final_room,))
            room_data = cur.fetchone()
            if room_data:
                current_count = room_data.get("current_occupancy", 0)
                # Agar room me pehle se 3 ya usse zyada bacche hain, toh register mat hone do
                if current_count >= 3:
                    return {
                        "success": False, 
                        "message": f"Registration Failed! Room {final_room} is already FULL (Max 3 students allowed)."
                    }
            else:
                # Agar wo room number 'rooms' table me exist hi nahi karta, toh safety ke liye block kar do
                return {
                    "success": False, 
                    "message": f"Registration Failed! Room {final_room} does not exist in the administration records."
                }
        # -------------------------------------------------------------------------
        # Mark token as consumed instantly
        cur.execute("UPDATE otp_verifications SET is_verified=1 WHERE otp_id=%s", (otp_record["otp_id"],))
        # 3. Create User Account Execution Flow
        hashed_password = pwd_context.hash(data.password)
        sql_user = """
        INSERT INTO users(name, enrollment_no, email, phone_no, dob, parent_email, parent_phone, address, password, role, fcm_token)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        cur.execute(sql_user, (data.name, data.enrollment_no, data.email, data.phone_no, data.dob, data.parent_email, data.parent_phone, data.address, hashed_password, data.role, data.fcm_token))
        new_user_id = cur.lastrowid
        # 4. Save Profile & Update Occupancy (Only for Students)
        if data.role == "Student":
            cur.execute("""
                INSERT INTO student_profiles (user_id, semester, room_no, floor_no)
                VALUES (%s, %s, %s, %s)
            """, (new_user_id, data.semester, final_room, final_floor))
            # 🔄 3 IN 1 AUTOMATION: Agar room assign hua hai toh occupancy count badhao (+1)
            if final_room:
                cur.execute("""
                    UPDATE rooms 
                    SET current_occupancy = current_occupancy + 1 
                    WHERE room_no = %s
                """, (final_room,))
                print(f"📈 [Room Update] Occupancy increased for Room: {final_room}")
        # 5. 📱 SMS DRIVEN AUTOMATION (Student Alert)
        student_sms_msg = f"Hello {data.name}, your registration in CAIT AAU Hostel Portal is successful! Room {final_room if final_room else 'Pending'} allocated."
        student_sms_status = send_custom_sms_gateway(data.phone_no, student_sms_msg)
        log_communication(cur, new_user_id, "Student", "SMS", None, student_sms_msg, student_sms_status)
        # 6. AUTOMATED PARENT TRACKING NOTIFICATIONS ADVISORY
        parent_subject = "Official Hostel Registration & Accommodation Confirmation - CAIT AAU"
        parent_email_content = f"""
        <h3>College of Agricultural Information Technology (CAIT)</h3>
        <h4>Anand Agricultural University, Anand</h4>
        <hr/>
        <p>Respected Parent,</p>
        <p>Greetings from the College of Agricultural Information Technology (CAIT), Anand Agricultural University, Anand.</p>
        <p>We are pleased to inform you that your ward has successfully completed registration in the CAIT Hostel Management System and has been allotted the following accommodation:</p>
        <ul>
            <li><b>Student Name:</b> {data.name}</li>
            <li><b>Enrollment No:</b> {data.enrollment_no}</li>
            <li><b>Hostel:</b> Arya Boys Hostel</li>
            <li><b>Room Number:</b> {final_room if final_room else 'Not Assigned Yet'}</li>
        </ul>
        <p>This communication serves as an official confirmation of hostel registration. You will receive future tracking notifications regarding your child's leave applications, hostel notices, emergency alerts, and administrative updates.</p>
        <p>We kindly request that you regularly monitor your registered email address and mobile number for official communications from the hostel administration.</p>
        <br/>
        <p>For assistance, please contact:</p>
        <p><b>College of Agricultural Information Technology (CAIT)</b><br/>
        Anand Agricultural University, Anand - 388110, Gujarat, India<br/>
        Email: <a href="mailto:cait@aau.in">cait@aau.in</a></p>
        <br/>
        <p>Warm Regards,</p>
        <p><b>Hostel Administration</b><br/>
        College of Agricultural Information Technology (CAIT)<br/>
        Anand Agricultural University, Anand</p>
        """
        parent_sms_content = f"Your student {data.name} completed registration in CAIT AAU Hostel. Room: {final_room if final_room else 'Pending'}. This is official tracking text."
        p_email_status = send_brevo_email(data.parent_email, parent_subject, parent_email_content)
        p_sms_status = send_custom_sms_gateway(data.parent_phone, parent_sms_content)
        log_communication(cur, new_user_id, "Parent", "Email", parent_subject, parent_email_content, p_email_status)
        log_communication(cur, new_user_id, "Parent", "SMS", None, parent_sms_content, p_sms_status)
        con.commit()
        return {"success": True, "message": "Registration Complete! Room allocated and capacity counters updated safely."}
    except Exception as e:
        con.rollback()
        print(f"Database Transaction Failure: {str(e)}")
        return {"success": False, "message": f"Server Error during signup: {str(e)}"}
    finally:
        cur.close()
        con.close()
@app.post("/login")
def login(data: LoginModel):
    con = get_connection()
    cur = con.cursor()
    try:
        cur.execute("SELECT * FROM users WHERE email=%s", (data.email,))
        user = cur.fetchone()
        if not user:
            return {"success": False, "message": "User Not Found"}
        if not pwd_context.verify(data.password, user["password"]):
            return {"success": False, "message": "Invalid Password"}
        cur.execute("UPDATE users SET fcm_token=%s WHERE user_id=%s", (data.fcm_token, user["user_id"]))
        con.commit()
        return {
            "success": True,
            "token": "sample_token",
            "user": {
                "user_id": user["user_id"],
                "name": user["name"],
                "enrollment_no": user["enrollment_no"],
                "email": user["email"],
                "phone_no": user["phone_no"],
                "role": user["role"]
            }
        }
    finally:
        cur.close()
        con.close()
@app.post("/logout")
def logout(data: LogoutModel):
    con = get_connection()
    cur = con.cursor()
    try:
        cur.execute("UPDATE users SET fcm_token='' WHERE fcm_token=%s", (data.fcm_token,))
        con.commit()
        return {"success": True, "message": "Logout Success"}
    finally:
        cur.close()
        con.close()
# PROFILE
@app.get("/profile/{user_id}")
def profile(user_id: int):
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT u.user_id, u.name, u.enrollment_no, u.email, u.phone_no, u.dob, 
               u.parent_email, u.parent_phone, u.address, u.role,
               p.semester, p.room_no, p.floor_no 
        FROM users u 
        LEFT JOIN student_profiles p ON u.user_id = p.user_id 
        WHERE u.user_id=%s
    """, (user_id,))
    user = cur.fetchone()
    cur.close()
    con.close()
    if not user:
        return {"success": False, "message": "User Not Found"}
    return {"success": True, "user": user}
# =========================================================================
#                          STUDENT APIS
# =========================================================================
# CREATE COMPLAINT
@app.post("/create-complaint")
async def create_complaint(
    student_id: int = Form(...),
    title: str = Form(...),
    complaint_text: str = Form(...),
    file: Optional[UploadFile] = File(None)
):
    uploaded_url = ""
    if file:
        try:
            file_bytes = await file.read()
            upload_result = cloudinary.uploader.upload(
                file_bytes,
                folder="hostel_complaints"
            )
            uploaded_url = upload_result.get("secure_url", "")
        except Exception as upload_error:
            return {"success": False, "message": f"Cloudinary Upload Failed: {str(upload_error)}"}
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO complaints (student_id, title, complaint_text, complaint_photo, status, created_at)
        VALUES (%s, %s, %s, %s, 'Pending', NOW())
    """, (student_id, title, complaint_text, uploaded_url))
    complaint_id = cur.lastrowid
    cur.execute("SELECT user_id, fcm_token FROM users WHERE role IN ('AssistantRector', 'Rector', 'Warden')")
    admins = cur.fetchall()
    for admin in admins:
        cur.execute("""
            INSERT INTO notifications (user_id, title, body, created_at)
            VALUES (%s, %s, %s, NOW())
        """, (admin["user_id"], "New Complaint", f"Complaint #{complaint_id}"))
        if admin["fcm_token"]:
            send_notification(admin["fcm_token"], "New Complaint", title)
    con.commit()
    cur.close()
    con.close()
    return {"success": True, "message": "Complaint Submitted Successfully!"}
# MY COMPLAINTS (STUDENT STREAM WITH SORT OPTION)
@app.get("/my-complaints/{student_id}")
def my_complaints(student_id: int, sort_by: str = Query("newest", regex="^(newest|oldest)$")):
    order = "DESC" if sort_by == "newest" else "ASC"
    con = get_connection()
    cur = con.cursor()
    cur.execute(f"""
        SELECT complaint_id, title, complaint_text, complaint_photo, status, 
               DATE_FORMAT(created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at, 
               DATE_FORMAT(solved_at, '%%Y-%%m-%%d %%H:%%i:%%s') as solved_at 
        FROM complaints 
        WHERE student_id=%s 
        ORDER BY complaints.created_at {order}
    """, (student_id,))
    data = cur.fetchall()
    cur.close()
    con.close()
    return {"success": True, "data": data}


# COMPLAINT DETAILS WITH REMARKS (STUDENT READ VIEW)
@app.get("/complaint-details/{complaint_id}")
def student_complaint_details(complaint_id: int):
    con = get_connection()
    cur = con.cursor()
    
    cur.execute("""
        SELECT complaint_id, student_id, title, complaint_text, complaint_photo, status,
               DATE_FORMAT(created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at,
               DATE_FORMAT(solved_at, '%%Y-%%m-%%d %%H:%%i:%%s') as solved_at
        FROM complaints WHERE complaint_id=%s
    """, (complaint_id,))
    complaint = cur.fetchone()

    if not complaint:
        cur.close()
        con.close()
        return {"success": False, "message": "Complaint not found"}

    cur.execute("""
        SELECT r.remark_id, r.complaint_id, r.user_id, r.remark, r.photo,
               DATE_FORMAT(r.created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at,
               u.name as remark_by, u.role 
        FROM complaint_remarks r
        JOIN users u ON r.user_id = u.user_id
        WHERE r.complaint_id=%s
        ORDER BY r.remark_id ASC
    """, (complaint_id,))
    remarks = cur.fetchall()

    cur.close()
    con.close()
    return {"success": True, "complaint": complaint, "remarks": remarks}

@app.get("/all-complaints")
def all_complaints():
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT c.*, u.name, u.enrollment_no
        FROM complaints c
        JOIN users u ON c.student_id=u.user_id
        ORDER BY c.complaint_id DESC
    """)
    data = cur.fetchall()
    cur.close()
    con.close()
    return {"success": True, "data": data}


# STUDENT FEEDBACK (CLOSE COMPLAINT)
@app.post("/student-feedback")
def student_feedback(data: StudentFeedbackModel):
    con = get_connection()
    cur = con.cursor()
    try:
        cur.execute("""
            INSERT INTO complaint_remarks (complaint_id, user_id, remark, photo, created_at)
            VALUES(%s, %s, %s, '', NOW())
        """, (data.complaint_id, data.user_id, data.remark))

        cur.execute("UPDATE complaints SET status='Closed', solved_at=NOW() WHERE complaint_id=%s", (data.complaint_id,))
        con.commit()
        return {"success": True, "message": "Complaint Closed Successfully"}
    except Exception as e:
        con.rollback()
        return {"success": False, "message": str(e)}
    finally:
        cur.close()
        con.close()


# =========================================================================
#                      ASSISTANT RECTOR APIS
# =========================================================================

@app.get("/assistant-rector/dashboard")
def get_assistant_rector_dashboard(sort_by: str = Query("newest", regex="^(newest|oldest)$")):
    order = "DESC" if sort_by == "newest" else "ASC"
    con = get_connection()
    cur = con.cursor()
    
    try:
        cur.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'Pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'Solved' THEN 1 ELSE 0 END) as solved,
                SUM(CASE WHEN status = 'Closed' THEN 1 ELSE 0 END) as closed
            FROM complaints
        """)
        metrics = cur.fetchone()

        cur.execute(f"""
            SELECT c.complaint_id, c.student_id, c.title, c.complaint_text, c.complaint_photo, c.status,
                   DATE_FORMAT(c.created_at, '%Y-%m-%d %H:%i:%s') as created_at,
                   DATE_FORMAT(c.solved_at, '%Y-%m-%d %H:%i:%s') as solved_at,
                   u.name as student_name, u.enrollment_no
            FROM complaints c
            JOIN users u ON c.student_id = u.user_id
            ORDER BY c.created_at {order}
        """)
        complaints = cur.fetchall()

        return {
            "success": True,
            "metrics": metrics,
            "complaints": complaints
        }
    finally:
        cur.close()
        con.close()

@app.get("/assistant-rector/complaint-details/{complaint_id}")
def assistant_rector_complaint_details(complaint_id: int):
    con = get_connection()
    cur = con.cursor()
    try:
        # First Query - Checked & Validated
        cur.execute("""
            SELECT c.complaint_id, c.student_id, c.title, c.complaint_text, c.complaint_photo, c.status,
                   DATE_FORMAT(c.created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at,
                   DATE_FORMAT(c.solved_at, '%%Y-%%m-%%d %%H:%%i:%%s') as solved_at,
                   u.name as student_name, u.enrollment_no, u.email as student_email, u.phone_no as student_phone,
                   p.semester, p.room_no, p.floor_no
            FROM complaints c
            JOIN users u ON c.student_id = u.user_id
            LEFT JOIN student_profiles p ON u.user_id = p.user_id
            WHERE c.complaint_id = %s
        """, (complaint_id,))
        complaint = cur.fetchone()

        if not complaint:
            return {"success": False, "message": "Complaint not found"}

        # Second Query - 🛠️ FIXED: Escaped '%' to '%%' here too
        cur.execute("""
            SELECT r.remark_id, r.complaint_id, r.user_id, r.remark, r.photo,
                   DATE_FORMAT(r.created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at,
                   u.name as remark_by, u.role
            FROM complaint_remarks r
            JOIN users u ON r.user_id = u.user_id
            WHERE r.complaint_id = %s
            ORDER BY r.remark_id ASC
        """, (complaint_id,))
        remarks = cur.fetchall()

        return {"success": True, "complaint": complaint, "remarks": remarks}
    finally:
        cur.close()
        con.close()
# =========================================================================
#                             RECTOR APIS
# =========================================================================

@app.get("/rector/dashboard-summary")
async def get_rector_summary(sort_by: str = Query("newest", regex="^(newest|oldest)$")):
    order = "DESC" if sort_by == "newest" else "ASC"
    con = get_connection()
    cur = con.cursor()
    try:
        cur.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'Pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'Solved' THEN 1 ELSE 0 END) as solved,
                SUM(CASE WHEN status = 'Closed' THEN 1 ELSE 0 END) as closed
            FROM complaints
        """)
        stats = cur.fetchone()

        cur.execute(f"""
            SELECT c.complaint_id, c.student_id, c.title, c.complaint_text, c.complaint_photo, c.status,
                   DATE_FORMAT(c.created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at,
                   DATE_FORMAT(c.solved_at, '%%Y-%%m-%%d %%H:%%i:%%s') as solved_at,
                   u.name as student_name, p.room_no, u.enrollment_no
            FROM complaints c
            JOIN users u ON c.student_id = u.user_id
            LEFT JOIN student_profiles p ON u.user_id = p.user_id
            ORDER BY c.created_at {order}
        """)
        all_complaints = cur.fetchall()

        return {
            "success": True,
            "metrics": {
                "total": stats.get("total", 0) if stats else 0,
                "pending": int(stats.get("pending") or 0) if stats else 0,
                "solved": int(stats.get("solved") or 0) if stats else 0,
                "closed": int(stats.get("closed") or 0) if stats else 0,
            },
            "complaints": all_complaints
        }
    except Exception as e:
        return {"success": False, "message": str(e)}
    finally:
        cur.close()
        con.close()



@app.get("/rector/complaint-details/{complaint_id}")
def rector_complaint_details(complaint_id: int):
    return assistant_rector_complaint_details(complaint_id)


# =========================================================================
#                         CHIEF RECTOR APIS
# =========================================================================

@app.get("/chief-rector/dashboard-summary")
async def get_chief_rector_summary(sort_by: str = Query("newest", regex="^(newest|oldest)$")):
    order = "DESC" if sort_by == "newest" else "ASC"
    con = get_connection()
    cur = con.cursor()
    try:
        cur.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'Pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'Solved' THEN 1 ELSE 0 END) as solved,
                SUM(CASE WHEN status = 'Closed' THEN 1 ELSE 0 END) as closed
            FROM complaints
        """)
        stats = cur.fetchone()

        cur.execute(f"""
            SELECT c.complaint_id, c.student_id, c.title, c.complaint_text, c.complaint_photo, c.status,
                   DATE_FORMAT(c.created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at,
                   DATE_FORMAT(c.solved_at, '%%Y-%%m-%%d %%H:%%i:%%s') as solved_at,
                   u.name as student_name, u.enrollment_no 
            FROM complaints c
            JOIN users u ON c.student_id = u.user_id
            ORDER BY c.created_at {order}
        """)
        all_complaints = cur.fetchall()

        return {
            "success": True,
            "metrics": {
                "total": int(stats.get("total") or 0),
                "pending": int(stats.get("pending") or 0),
                "solved": int(stats.get("solved") or 0),
                "closed": int(stats.get("closed") or 0)
            },
            "complaints": all_complaints
        }
    except Exception as e:
        return {"success": False, "message": f"Internal Server Error: {str(e)}"}
    finally:
        cur.close()
        con.close()


@app.get("/chief-rector/complaint-details/{complaint_id}")
def chief_rector_complaint_details(complaint_id: int):
    return assistant_rector_complaint_details(complaint_id)


# =========================================================================
#                             WARDEN APIS
# =========================================================================

@app.get("/warden/dashboard-summary")
def get_warden_summary(sort_by: str = Query("newest", regex="^(newest|oldest)$")):
    order = "DESC" if sort_by == "newest" else "ASC"
    con = get_connection()
    cur = con.cursor()
    try:
        cur.execute("SELECT COUNT(*) as pending FROM complaints WHERE status = 'Pending'")
        stats = cur.fetchone()

        cur.execute(f"""
            SELECT 
                c.complaint_id, 
                c.title, 
                c.status,
                c.complaint_text as description,
                c.complaint_photo as image_url,
                DATE_FORMAT(c.created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at,
                DATE_FORMAT(c.solved_at, '%%Y-%%m-%%d %%H:%%i:%%s') as solved_at,
                u.name as student_name,
                u.enrollment_no,
                p.room_no,
                p.floor_no
            FROM complaints c
            JOIN users u ON c.student_id = u.user_id
            LEFT JOIN student_profiles p ON u.user_id = p.user_id
            ORDER BY c.created_at {order}
        """)
        all_complaints = cur.fetchall()

        return {
            "success": True,
            "metrics": {
                "pending": int(stats.get("pending") or 0)
            },
            "complaints": all_complaints
        }
    except Exception as e:
        return {"success": False, "message": f"Server Error: {str(e)}"}
    finally:
        cur.close()
        con.close()


@app.get("/complaint/details")
def get_complaint_details(id: int):
    con = get_connection()
    cur = con.cursor()
    try:
        cur.execute("""
            SELECT 
                c.complaint_id, 
                c.title, 
                c.status, 
                c.complaint_text as description, 
                c.complaint_photo as image_url,
                DATE_FORMAT(c.created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at,
                DATE_FORMAT(c.solved_at, '%%Y-%%m-%%d %%H:%%i:%%s') as solved_at,
                u.name as student_name,
                u.enrollment_no,
                p.room_no,
                p.floor_no,
                (SELECT remark FROM complaint_remarks WHERE complaint_id = c.complaint_id ORDER BY remark_id DESC LIMIT 1) as remarks
            FROM complaints c
            JOIN users u ON c.student_id = u.user_id
            LEFT JOIN student_profiles p ON u.user_id = p.user_id
            WHERE c.complaint_id = %s
        """, (id,))
        complaint = cur.fetchone()

        if not complaint:
            return {"success": False, "message": "Record not found"}

        return {
            "success": True,
            "data": complaint
        }
    except Exception as e:
        return {"success": False, "message": str(e)}
    finally:
        cur.close()
        con.close()


@app.post("/warden/solve-complaint")
def warden_solve_complaint(data: WardenSolveModel):
    con = get_connection()
    cur = con.cursor()
    try:
        cur.execute(
            "UPDATE complaints SET status = %s, solved_at = NOW() WHERE complaint_id = %s",
            (data.status, data.complaint_id)
        )

        cur.execute("""
            INSERT INTO complaint_remarks (complaint_id, user_id, remark, photo, created_at)
            VALUES (%s, 0, %s, '', NOW())
        """, (data.complaint_id, data.remarks))

        cur.execute("SELECT student_id FROM complaints WHERE complaint_id = %s", (data.complaint_id,))
        row = cur.fetchone()
        
        if row:
            student_id = row["student_id"]
            cur.execute("""
                INSERT INTO notifications (user_id, title, body, created_at)
                VALUES (%s, 'Job Completed', %s, NOW())
            """, (student_id, f"Warden updated your request: {data.remarks[:30]}..."))

            cur.execute("SELECT fcm_token FROM users WHERE user_id = %s", (student_id,))
            user_row = cur.fetchone()
            if user_row and user_row.get("fcm_token"):
                send_notification(user_row["fcm_token"], "Complaint Solved ✅", f"Warden: {data.remarks}")

        con.commit()
        return {"success": True, "message": "Status updated successfully!"}
        
    except Exception as e:
        con.rollback()
        return {"success": False, "message": f"Database Error: {str(e)}"}
    finally:
        cur.close()
        con.close()


# =========================================================================
#                        SHARED WORKFLOW APIS
# =========================================================================

# ADD REMARK
@app.post("/add-remark")
def add_remark(data: RemarkModel):
    con = get_connection()
    cur = con.cursor()
    try:
        cur.execute("""
            INSERT INTO complaint_remarks (complaint_id, user_id, remark, photo, created_at)
            VALUES (%s, %s, %s, %s, NOW())
        """, (data.complaint_id, data.user_id, data.remark, data.photo))
        con.commit()
        return {"success": True, "message": "Remark Added"}
    except Exception as e:
        con.rollback()
        return {"success": False, "message": str(e)}
    finally:
        cur.close()
        con.close()


# SOLVE COMPLAINT BY ADMINS
@app.post("/solve-complaint")
async def solve_complaint(
    complaint_id: int = Form(...),
    user_id: int = Form(...),
    remark: str = Form(...),
    file: Optional[UploadFile] = File(None)
):
    uploaded_url = ""
    if file:
        try:
            file_bytes = await file.read()
            upload_result = cloudinary.uploader.upload(
                file_bytes,
                folder="hostel_complaints"
            )
            uploaded_url = upload_result.get("secure_url", "")
        except Exception as upload_error:
            return {"success": False, "message": f"Cloudinary Failed: {str(upload_error)}"}

    con = get_connection()
    cur = con.cursor()
    try:
        # Fixed: Explicitly passed NOW() timestamp into the required created_at tracking field
        cur.execute("""
            INSERT INTO complaint_remarks (complaint_id, user_id, remark, photo, created_at)
            VALUES (%s, %s, %s, %s, NOW())
        """, (complaint_id, user_id, remark, uploaded_url))

        cur.execute("UPDATE complaints SET status='Solved', solved_at=NOW() WHERE complaint_id=%s", (complaint_id,))
        
        cur.execute("SELECT student_id FROM complaints WHERE complaint_id=%s", (complaint_id,))
        complaint_row = cur.fetchone()

        if complaint_row and "student_id" in complaint_row:
            target_student_id = complaint_row["student_id"]
            
            cur.execute("""
                INSERT INTO notifications (user_id, title, body, created_at)
                VALUES (%s, %s, %s, NOW())
            """, (target_student_id, "Complaint Solved", f"Your issue has been resolved: {remark[:30]}..."))
            
            cur.execute("SELECT fcm_token FROM users WHERE user_id=%s", (target_student_id,))
            user_row = cur.fetchone()
            
            if user_row and user_row.get("fcm_token"):
                send_notification(user_row["fcm_token"], "Complaint Solved ✅", f"Resolution: {remark}")

        con.commit()
        return {"success": True, "message": "Complaint Solved and Student Notified!"}
    except Exception as db_error:
        con.rollback()
        return {"success": False, "message": f"Internal Server Error: {str(db_error)}"}
    finally:
        cur.close()
        con.close()


# NOTIFICATIONS LIST
@app.get("/notifications/{user_id}")
def notifications(user_id: int):
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT notification_id, user_id, title, body,
               DATE_FORMAT(created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at
        FROM notifications WHERE user_id=%s ORDER BY notification_id DESC
    """, (user_id,))
    data = cur.fetchall()
    cur.close()
    con.close()
    return {"success": True, "data": data}


from fastapi import Form, UploadFile, File
from pydantic import BaseModel

class NoticeDeleteModel(BaseModel):
    notice_id: int
    user_id: int

# =========================================================================
#                          NOTICE BOARD APIS
# =========================================================================

# 1. FETCH ALL NOTICES (Accessible by ALL roles)
@app.get("/notices")
def get_all_notices():
    con = get_connection()
    cur = con.cursor()
    try:
        cur.execute("""
            SELECT n.notice_id, n.title, n.description, n.attachment_url, n.attachment_type,
                   DATE_FORMAT(n.created_at, '%Y-%m-%d %H:%i:%s') as created_at,
                   u.name as author_name, u.role as author_role
            FROM notices n
            JOIN users u ON n.posted_by = u.user_id
            ORDER BY n.created_at DESC
        """)
        notices = cur.fetchall()
        return {"success": True, "notices": notices}
    except Exception as e:
        return {"success": False, "message": str(e)}
    finally:
        cur.close()
        con.close()

# 2. CREATE NOTICE (AssistantRector, Rector, ChiefRector Only)
# =========================================================================
#                         CAMPUS NOTICES WORKFLOW
# =========================================================================
@app.post("/notices/create")
async def create_notice(
    title: str = Form(...),
    description: str = Form(...),
    posted_by: int = Form(...),
    file: Optional[UploadFile] = File(None)
):
    con = get_connection()
    cur = con.cursor()

    try:

        # CHECK ROLE

        cur.execute(
            """
            SELECT role
            FROM users
            WHERE user_id=%s
            """,
            (posted_by,)
        )

        user = cur.fetchone()

        if not user:
            return {
                "success": False,
                "message": "User Not Found"
            }

        if user["role"] not in [
            "AssistantRector",
            "Rector",
            "ChiefRector"
        ]:
            return {
                "success": False,
                "message": "Permission Denied"
            }

        uploaded_url = ""
        attachment_type = ""

        # UPLOAD FILE TO CLOUDINARY

        if file:

            try:

                file_bytes = await file.read()

                content_type = file.content_type or ""

                filename =file.filename.lower()

                if (
                    "image" in content_type
                    or filename.endswith(
                        (
                            ".jpg",
                            ".jpeg",
                            ".png"
                        )
                    )
                ):
                    attachment_type = "image"

                elif (
                    "pdf" in content_type
                    or filename.endswith(
                        ".pdf"
                    )
                ):
                    attachment_type = "pdf"

                else:
                    attachment_type = "document"

                upload_result = (
                    cloudinary.uploader.upload(
                        file_bytes,
                        folder="hostel_notices",
                        resource_type="auto"
                    )
                )

                uploaded_url = (
                    upload_result.get(
                        "secure_url",
                        ""
                    )
                )

            except Exception as e:

                return {
                    "success": False,
                    "message":
                    f"Cloudinary Error : {str(e)}"
                }

        # INSERT NOTICE

        cur.execute(
            """
            INSERT INTO notices
            (
                title,
                description,
                attachment_url,
                attachment_type,
                posted_by,
                created_at
            )
            VALUES
            (
                %s,%s,%s,%s,%s,NOW()
            )
            """,
            (
                title,
                description,
                uploaded_url,
                attachment_type,
                posted_by
            )
        )

        notice_id = cur.lastrowid

        # SAVE NOTIFICATION FOR ALL USERS

        cur.execute("""
            SELECT
                user_id,
                fcm_token
            FROM users
        """)

        users = cur.fetchall()

        for target_user in users:

            # SAVE IN APP NOTIFICATION

            cur.execute(
                """
                INSERT INTO notifications
                (
                    user_id,
                    title,
                    body,
                    created_at
                )
                VALUES
                (
                    %s,%s,%s,NOW()
                )
                """,
                (
                    target_user["user_id"],
                    "📢 New Notice",
                    title
                )
            )

            # SEND PUSH NOTIFICATION

            if (
                target_user.get(
                    "fcm_token"
                )
            ):

                try:

                    send_notification(
                        target_user[
                            "fcm_token"
                        ],
                        "📢 New Notice",
                        title
                    )

                except Exception as e:

                    print(
                        f"FCM Error : {e}"
                    )

        con.commit()

        return {
            "success": True,
            "message":
            "Notice Published Successfully",
            "notice_id":
            notice_id
        }

    except Exception as e:

        con.rollback()

        return {
            "success": False,
            "message": str(e)
        }

    finally:

        cur.close()
        con.close()
# 3. EDIT EXISTING NOTICE
@app.post("/notices/edit")
async def edit_notice(
    notice_id: int = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    user_id: int = Form(...),
    file: Optional[UploadFile] = File(None)
):
    con = get_connection()
    cur = con.cursor()
    try:
        cur.execute("SELECT role FROM users WHERE user_id = %s", (user_id,))
        user = cur.fetchone()
        if not user or user["role"] not in ["AssistantRector", "Rector", "ChiefRector"]:
            return {"success": False, "message": "Unauthorized execution logic access."}

        if file:
            content_type = file.content_type or ""
            attachment_type = "image" if "image" in content_type else "pdf" if "pdf" in content_type else "document"
            
            file_bytes = await file.read()
            upload_result = cloudinary.uploader.upload(file_bytes, folder="hostel_notices", resource_type="auto")
            uploaded_url = upload_result.get("secure_url", "")

            cur.execute("""
                UPDATE notices 
                SET title = %s, description = %s, attachment_url = %s, attachment_type = %s 
                WHERE notice_id = %s
            """, (title, description, uploaded_url, attachment_type, notice_id))
        else:
            cur.execute("""
                UPDATE notices SET title = %s, description = %s WHERE notice_id = %s
            """, (title, description, notice_id))

        con.commit()
        return {"success": True, "message": "Notice altered successfully."}
    except Exception as e:
        con.rollback()
        return {"success": False, "message": str(e)}
    finally:
        cur.close()
        con.close()

@app.post("/notices/delete")
async def delete_notice(
    notice_id: int = Form(...),
    user_id: int = Form(...)
):
    con = get_connection()
    # Explicitly verify we are fetching a dictionary cursor style
    cur = con.cursor()
    try:
        # Check permissions
        cur.execute("SELECT role FROM users WHERE user_id = %s", (user_id,))
        user = cur.fetchone()
        
        if not user:
            return {"success": False, "message": "User validation record not found."}
            
        if user["role"] not in ["AssistantRector", "Rector", "ChiefRector"]:
            return {"success": False, "message": "Unauthorized execution logic access."}

        # Clear out the database entry
        cur.execute("DELETE FROM notices WHERE notice_id = %s", (notice_id,))
        con.commit()
        
        return {"success": True, "message": "Notice purged successfully."}
        
    except Exception as e:
        con.rollback()
        return {"success": False, "message": f"Server error: {str(e)}"}
    finally:
        cur.close()
        con.close()

@app.get("/notice/{notice_id}")
def notice_details(notice_id: int):

    con = get_connection()
    cur = con.cursor()

    cur.execute("""
        SELECT
            n.*,
            u.name as author_name
        FROM notices n
        JOIN users u
        ON n.posted_by=u.user_id
        WHERE n.notice_id=%s
    """,(notice_id,))

    notice = cur.fetchone()

    cur.close()
    con.close()

    return {
        "success": True,
        "notice": notice
    }

def broadcast_notice_notification(title: str, body: str):
    """
    Broadcasts a push notification to all users subscribed to the 'notices' topic
    and writes the notification records into the database logs.
    """
    # 1. Send FCM Topic Push Notification
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body
            ),
            topic="notices" # All students, wardens, and rectors subscribe to this
        )
        messaging.send(message)
        print("Successfully broadcasted notice notification to 'notices' topic.")
    except Exception as e:
        print(f"FCM Broadcast failed: {e}")

    # 2. Log notification inside the database for all users to see in their App Notifications Tab
    con = get_connection()
    cur = con.cursor()
    try:
        # Get all active user IDs
        cur.execute("SELECT user_id FROM users")
        users = cur.fetchall()
        
        if users:
            # Batch insertion payload creation
            insert_query = "INSERT INTO notifications (user_id, title, body, created_at) VALUES (%s, %s, %s, NOW())"
            records = [(user["user_id"], title, body) for user in users]
            
            cur.executemany(insert_query, records)
            con.commit()
            print(f"Logged notification in-app records for {len(records)} users.")
    except Exception as db_err:
        print(f"Failed to log background broadcast notifications: {db_err}")
    finally:
        cur.close()
        con.close()


# HOME
@app.get("/home")
def home():
    return {"success": True, "message": "Welcome To Hostel Management System"}





#from fastapi import FastAPI, Form, HTTPException, Query
#from pydantic import BaseModel
#import pymysql

#app = FastAPI(title="Hostel Room Allocation Engine")


# Helper function to recalculate room occupancies instantly
def sync_room_occupancy(cur, room_no):
    if not room_no: return
    cur.execute("SELECT COUNT(*) as count FROM student_profiles WHERE room_no = %s", (room_no,))
    actual_count = cur.fetchone()["count"]
    cur.execute("UPDATE rooms SET current_occupancy = %s WHERE room_no = %s", (actual_count, room_no))

# =========================================================================
# 1. RECTOR DASHBOARD: STATISTICS & ROOM AVAILABILITY METRICS
# =========================================================================
@app.get("/rector/dashboard-stats")
def get_dashboard_stats():
    con = get_connection()
    cur = con.cursor()
    try:
        # Total metrics
        cur.execute("SELECT COUNT(*) as total_students FROM student_profiles")
        tot_students = cur.fetchone()["total_students"]
        
        # Room Space Status Parser
        cur.execute("""
            SELECT 
                room_no, floor_no, max_capacity, current_occupancy,
                (max_capacity - current_occupancy) as available_spaces,
                CASE 
                    WHEN current_occupancy = 0 THEN 'Empty'
                    WHEN current_occupancy >= max_capacity THEN 'Full'
                    ELSE CONCAT('Space Available: ', (max_capacity - current_occupancy))
                END as live_status
            FROM rooms WHERE status = 'active'
        """)
        rooms_report = cur.fetchall()
        
        # Floor Wise Metrics
        cur.execute("""
            SELECT floor_no, COUNT(room_id) as total_rooms, SUM(current_occupancy) as floor_student_count
            FROM rooms GROUP BY floor_no
        """)
        floor_report = cur.fetchall()

        return {
            "success": True,
            "total_students_in_hostel": tot_students,
            "floors_summary": floor_report,
            "rooms_detailed_matrix": rooms_report
        }
    finally:
        cur.close()
        con.close()

# =========================================================================
# 2. ASSIGN / UPDATE ROOM (INDIVIDUAL MANUAL ALLOCATION)
# =========================================================================

class AllocateStudentRequest(BaseModel):
    user_id: int
    room_no: str
    floor_no: str


@app.post("/rector/allocate-student")
def allocate_student(data: AllocateStudentRequest):

    con = get_connection()
    cur = con.cursor()

    try:

        cur.execute("""
            SELECT
                current_occupancy,
                max_capacity
            FROM rooms
            WHERE room_no=%s
        """,(data.room_no,))

        room = cur.fetchone()

        if not room:
            return {
                "success": False,
                "message": "Room not found"
            }

        if room["current_occupancy"] >= room["max_capacity"]:
            return {
                "success": False,
                "message": "Room Full"
            }

        cur.execute("""
            SELECT room_no
            FROM student_profiles
            WHERE user_id=%s
        """,(data.user_id,))

        old_profile = cur.fetchone()

        old_room = None

        if old_profile:
            old_room = old_profile["room_no"]

        cur.execute("""
            UPDATE student_profiles
            SET
                room_no=%s,
                floor_no=%s
            WHERE user_id=%s
        """,
        (
            data.room_no,
            data.floor_no,
            data.user_id
        ))

        sync_room_occupancy(
            cur,
            data.room_no
        )

        if old_room:

            sync_room_occupancy(
                cur,
                old_room
            )

        con.commit()

        return {
            "success": True,
            "message":
            "Room assigned successfully"
        }

    except Exception as e:

        con.rollback()

        return {
            "success": False,
            "message": str(e)
        }

    finally:

        cur.close()
        con.close()
# =========================================================================
# 3. DELETE / DE-ALLOCATE ASSIGNMENT (REMOVE STUDENT FROM ROOM)
# =========================================================================

class DeallocateStudentRequest(BaseModel):
    user_id: int


@app.post("/rector/deallocate-student")
def deallocate_student(
    data: DeallocateStudentRequest
):

    con = get_connection()
    cur = con.cursor()

    try:

        cur.execute("""
            SELECT room_no
            FROM student_profiles
            WHERE user_id=%s
        """,(data.user_id,))

        profile = cur.fetchone()

        if not profile:

            return {
                "success": False,
                "message":
                "Profile not found"
            }

        assigned_room =profile["room_no"]

        cur.execute("""
            UPDATE student_profiles
            SET
                room_no=NULL,
                floor_no=NULL
            WHERE user_id=%s
        """,(data.user_id,))

        if assigned_room:

            sync_room_occupancy(
                cur,
                assigned_room
            )

        con.commit()

        return {
            "success": True,
            "message":
            "Student removed successfully"
        }

    except Exception as e:

        con.rollback()

        return {
            "success": False,
            "message": str(e)
        }

    finally:

        cur.close()
        con.close()


#================deallocation floor room bulk mostly for final year student leave room thaen use=====
#====================================================================================================

class DeallocateRoomRequest(BaseModel):
    room_no: str


@app.post("/rector/deallocate-room")
def deallocate_room(data: DeallocateRoomRequest):

    con = get_connection()
    cur = con.cursor()

    try:

        cur.execute("""
            SELECT COUNT(*) AS total
            FROM student_profiles
            WHERE room_no=%s
        """,(data.room_no,))

        total_students = cur.fetchone()["total"]

        if total_students == 0:

            return {
                "success": False,
                "message":
                "Room already empty"
            }

        cur.execute("""
            UPDATE student_profiles
            SET
                room_no=NULL,
                floor_no=NULL
            WHERE room_no=%s
        """,(data.room_no,))

        sync_room_occupancy(
            cur,
            data.room_no
        )

        con.commit()

        return {
            "success": True,
            "message":
            f"{total_students} students removed from Room {data.room_no}"
        }

    except Exception as e:

        con.rollback()

        return {
            "success": False,
            "message": str(e)
        }

    finally:

        cur.close()
        con.close()
# =========================================================================
# 4. BULK ADVANCED: ROOM-TO-ROOM FULL BATCH SHIFT
# =========================================================================

class ShiftRoomRequest(BaseModel):
    from_room: str
    to_room: str
    to_floor: str


@app.post("/rector/shift-entire-room")
def shift_entire_room(data: ShiftRoomRequest):

    con = get_connection()
    cur = con.cursor()

    try:

        if data.from_room == data.to_room:
            return {
                "success": False,
                "message": "Source and destination room cannot be same"
            }

        # Count students in source room
        cur.execute("""
            SELECT COUNT(*) AS count
            FROM student_profiles
            WHERE room_no=%s
        """, (data.from_room,))

        moving_count = cur.fetchone()["count"]

        if moving_count == 0:
            return {
                "success": False,
                "message": f"Room {data.from_room} is empty"
            }

        # Verify destination room and floor
        cur.execute("""
            SELECT
                room_no,
                floor_no,
                current_occupancy,
                max_capacity
            FROM rooms
            WHERE room_no=%s
        """, (data.to_room,))

        target_room = cur.fetchone()

        if not target_room:
            return {
                "success": False,
                "message": "Destination room not found"
            }

        if str(target_room["floor_no"]) != str(data.to_floor):
            return {
                "success": False,
                "message": "Selected room does not belong to selected destination floor"
            }

        available_seats = (
            target_room["max_capacity"]
            - target_room["current_occupancy"]
        )

        if moving_count > available_seats:
            return {
                "success": False,
                "message":
                f"Only {available_seats} seats available in Room {data.to_room}"
            }

        # Shift all students
        cur.execute("""
            UPDATE student_profiles
            SET
                room_no=%s,
                floor_no=%s
            WHERE room_no=%s
        """,
        (
            data.to_room,
            data.to_floor,
            data.from_room
        ))

        # Update room occupancy counters
        sync_room_occupancy(
            cur,
            data.from_room
        )

        sync_room_occupancy(
            cur,
            data.to_room
        )

        con.commit()

        return {
            "success": True,
            "message":
            f"Successfully shifted {moving_count} students from Room {data.from_room} to Room {data.to_room}"
        }

    except Exception as e:

        con.rollback()

        return {
            "success": False,
            "message": str(e)
        }

    finally:

        cur.close()
        con.close()
# =========================================================================
# 5. INDIVIDUAL ADVANCED: INDIVIDUAL SWAP WITH ANOTHER STUDENT
# =========================================================================

class SwapStudentsRequest(BaseModel):
    student_a_id: int
    student_b_id: int
@app.post("/rector/swap-students")
def swap_students(data: SwapStudentsRequest):
    con = get_connection()
    cur = con.cursor()
    try:
        cur.execute("""
            SELECT room_no,floor_no
            FROM student_profiles
            WHERE user_id=%s
        """,(data.student_a_id,))
        a_data = cur.fetchone()
        cur.execute("""
            SELECT room_no,floor_no
            FROM student_profiles
            WHERE user_id=%s
        """,(data.student_b_id,))
        b_data = cur.fetchone()
        if not a_data:
            return {
                "success": False,
                "message": "Student A not allocated"
            }
        if not b_data:
            return {
                "success": False,
                "message": "Student B not allocated"
            }
        cur.execute("""
            UPDATE student_profiles
            SET
                room_no=%s,
                floor_no=%s
            WHERE user_id=%s
        """,
        (
            b_data["room_no"],
            b_data["floor_no"],
            data.student_a_id
        ))
        cur.execute("""
            UPDATE student_profiles
            SET
                room_no=%s,
                floor_no=%s
            WHERE user_id=%s
        """,
        (
            a_data["room_no"],
            a_data["floor_no"],
            data.student_b_id
        ))
        sync_room_occupancy(
            cur,
            a_data["room_no"]
        )
        sync_room_occupancy(
            cur,
            b_data["room_no"]
        )
        con.commit()
        return {
            "success": True,
            "message":
            "Students swapped successfully"
        }
    except Exception as e:
        con.rollback()
        return {
            "success": False,
            "message": str(e)
        }
    finally:
        cur.close()
        con.close()
@app.get("/floors")
def get_floors():
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT *
        FROM floors
        ORDER BY floor_no
    """)
    floors = cur.fetchall()
    cur.close()
    con.close()
    return {
        "success": True,
        "floors": floors
    }
@app.get("/rooms/{floor_no}")
def get_rooms(floor_no: str):
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT *
        FROM rooms
        WHERE floor_no=%s
        ORDER BY room_no
    """,(floor_no,))
    rooms = cur.fetchall()
    cur.close()
    con.close()
    return {
        "success": True,
        "rooms": rooms
    }
@app.get("/student-room/{user_id}")
def get_student_room(user_id: int):
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT
            u.user_id,
            u.name,
            u.enrollment_no,
            sp.floor_no,
            sp.room_no
        FROM users u
        JOIN student_profiles sp
        ON u.user_id=sp.user_id
        WHERE u.user_id=%s
    """,(user_id,))
    data = cur.fetchone()
    cur.close()
    con.close()
    return {
        "success": True,
        "student": data
    }
@app.get("/students")
def get_students():
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT
            u.user_id,
            u.name,
            u.enrollment_no,
            sp.floor_no,
            sp.room_no
        FROM users u
        LEFT JOIN student_profiles sp
        ON u.user_id=sp.user_id
        WHERE u.role='Student'
        ORDER BY u.name
    """)
    students = cur.fetchall()
    cur.close()
    con.close()
    return {
        "success": True,
        "students": students
    }
@app.get("/room-students/{room_no}")
def get_room_students(room_no: str):
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT
            u.user_id,
            u.name,
            u.enrollment_no
        FROM student_profiles sp
        JOIN users u
        ON sp.user_id=u.user_id
        WHERE sp.room_no=%s
    """,(room_no,))
    students = cur.fetchall()
    cur.close()
    con.close()
    return {
        "success": True,
        "students": students
    }
@app.post("/rector/create-floor")
def create_floor(
    floor_no: str = Form(...)
):
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO floors
        (
            floor_no,
            total_rooms
        )
        VALUES(%s,0)
    """,(floor_no,))
    con.commit()
    cur.close()
    con.close()
    return {
        "success": True,
        "message": "Floor Created"
    }
@app.post("/rector/create-room")
def create_room(
    room_no: str = Form(...),
    floor_no: str = Form(...),
    max_capacity: int = Form(...)
):
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO rooms
        (
            room_no,
            floor_no,
            max_capacity
        )
        VALUES(%s,%s,%s)
    """,
    (
        room_no,
        floor_no,
        max_capacity
    ))
    cur.execute("""
        UPDATE floors
        SET total_rooms=total_rooms+1
        WHERE floor_no=%s
    """,(floor_no,))
    con.commit()
    cur.close()
    con.close()
    return {
        "success": True,
        "message": "Room Created"
    }
@app.post("/rector/update-room-capacity")
def update_room_capacity(
    room_no: str = Form(...),
    max_capacity: int = Form(...)
):
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        UPDATE rooms
        SET max_capacity=%s
        WHERE room_no=%s
    """,
    (
        max_capacity,
        room_no
    ))
    con.commit()
    cur.close()
    con.close()
    return {
        "success": True,
        "message": "Capacity Updated"
    }
class StaffUpdate(BaseModel):
    user_id: int
    name: str
    email: str
    phone_no: str
# 1. Pydantic Model check karein (Ensure email, password, etc. are defined)
class StaffCreate(BaseModel):
    name: str
    email: str
    phone_no: str
    dob: str
    address: str
    password: str
    role: str
# 2. Endpoint par type badal kar StaffCreate kijiye
@app.post("/rector/create-staff")
def create_staff(data: StaffCreate):  # 👈 Yahan RegisterModel se badal kar StaffCreate kar diya
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "SELECT * FROM users WHERE email=%s",
        (data.email,)
    )
    if cur.fetchone():
        cur.close() # Connection properly close karein return se pehle
        con.close()
        return {
            "success": False,
            "message": "Email already exists"
        }
    hashed_password = pwd_context.hash(data.password)
    cur.execute("""
        INSERT INTO users
        (name, enrollment_no, email, phone_no, dob, parent_email, parent_phone, address, password, role, fcm_token)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        data.name, "", data.email, data.phone_no, data.dob, "", "", data.address, hashed_password, data.role, ""
    ))
    con.commit()
    cur.close()
    con.close()
    return {
        "success": True,
        "message": "Staff Created Successfully"
    }
@app.put("/rector/update-staff")
def update_staff(data: StaffUpdate):
    con = get_connection()
    cur = con.cursor()
    try:
        cur.execute("""
            UPDATE users
            SET
                name=%s,
                email=%s,
                phone_no=%s
            WHERE user_id=%s
        """, (
            data.name,
            data.email,
            data.phone_no,
            data.user_id
        ))
        con.commit()
        return {
            "success": True,
            "message": "Staff Updated Successfully"
        }
    except Exception as e:
        con.rollback()
        return {
            "success": False,
            "message": str(e)
        }
    finally:
        cur.close()
        con.close()
@app.get("/rector/staff-list")
def staff_list():
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT
            user_id,
            name,
            email,
            phone_no,
            role
        FROM users
        WHERE role IN
        (
            'AssistantRector',
            'ChiefRector',
            'Warden',
            'Rector'
        )
        ORDER BY role
    """)
    data = cur.fetchall()
    cur.close()
    con.close()
    return {
        "success": True,
        "staff": data
    }
@app.delete("/rector/delete-staff")
def delete_staff(user_id:int):
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "DELETE FROM users WHERE user_id=%s",
        (user_id,)
    )
    con.commit()
    cur.close()
    con.close()
    return {
        "success": True,
        "message": "Staff Deleted"
    }

# --- PYDANTIC RESPONSE MODELS (For Clean Swagger and Dynamic UI Mapping) ---
class StudentBasicResponse(BaseModel):
    user_id: int
    name: str
    enrollment_no: str
    sem: int
    room_no: Optional[str] = ""
    role: str

class StudentDetailResponse(BaseModel):
    user_id: int
    name: str
    enrollment_no: str
    email: str
    phone_no: str
    dob: str
    sem: int
    room_no: Optional[str] = ""
    parent_email: Optional[str] = ""
    parent_phone: Optional[str] = ""
    address: Optional[str] = ""


# 📑 1. GET STUDENT LIST (Sorted by Semester)
@app.get("/rector/student-list")
def get_student_list(sem: Optional[int] = Query(None, description="Filter by specific semester if needed")):
    con = get_connection()
    cur = con.cursor(pymysql.cursors.DictCursor) # DictCursor use kiya hai taaki direct key-value mile

    try:
        # Base query to fetch data from users table where role is student
        query = """
            SELECT 
                user_id, 
                name, 
                enrollment_no, 
                sem, 
                room_no,
                role
            FROM users 
            WHERE role = 'student'
        """
        params = []
        
        # Agar Flutter se specific sem filter bheja hai toh query modify hogi
        if sem is not None:
            query += " AND sem = %s"
            params.append(sem)
            
        # ORDER BY sem: Semester wise sorting framework automation logic
        query += " ORDER BY sem ASC, name ASC"

        cur.execute(query, tuple(params))
        students = cur.fetchall()

        return {
            "success": True,
            "message": "Student record matrix fetched successfully",
            "students": students
        }

    except Exception as e:
        return {
            "success": False,
            "message": f"Database pipeline error: {str(e)}"
        }
    finally:
        cur.close()
        con.close()


# 🔍 2. GET STUDENT MORE DETAILS (Triggered on List Click)
@app.get("/rector/student-detail")
def get_student_detail(user_id: int):
    con = get_connection()
    cur = con.cursor(pymysql.cursors.DictCursor)

    try:
        cur.execute("""
            SELECT 
                user_id, 
                name, 
                enrollment_no, 
                email, 
                phone_no, 
                dob, 
                sem, 
                room_no, 
                parent_email, 
                parent_phone, 
                address
            FROM users 
            WHERE user_id = %s AND role = 'student'
        """, (user_id,))
        
        student_data = cur.fetchone()

        if not student_data:
            return {
                "success": False,
                "message": "Student profile track not found in the database directory."
            }

        return {
            "success": True,
            "message": "Detailed profile synchronised successfully.",
            "detail": student_data
        }

    except Exception as e:
        return {
            "success": False,
            "message": f"Execution tracking breakdown: {str(e)}"
        }
    finally:
        cur.close()
        con.close()



# --- Existing Pydantic Validation Models ---
class CreateCaseModel(BaseModel):
    title: str
    description: str
    case_type: str
    target_type: str
    created_by: int
    student_ids: List[int]
    need_parent_meeting: Optional[int] = 0 # 1 = Yes (Send Campus Invite Call)

class RemarkModel(BaseModel):
    case_id: int
    user_id: int
    remark: str

class StudentExplanationModel(BaseModel):
    case_id: int
    student_id: int
    explanation: str

class FineModel(BaseModel):
    case_id: int
    student_id: int
    amount: float
    reason: str

class FinePaidModel(BaseModel):
    fine_id: int

class CloseCaseModel(BaseModel):
    case_id: int
'''
# --- Your Explicit Communication Utility Handlers ---
def send_brevo_email(to_email: str, subject: str, html_content: str) -> str:
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key['api-key'] = BREVO_API_KEY
    api_instance = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))
    send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": to_email}],
        sender={"name": OFFICIAL_SENDER_NAME, "email": OFFICIAL_SENDER_EMAIL},
        subject=subject,
        html_content=html_content
    )
    try:
        api_instance.send_transac_email(send_smtp_email)
        return "Sent"
    except ApiException:
        return "Failed"

def send_custom_sms_gateway(phone_no: str, message: str) -> str:
    try:
        clean_phone = "".join(filter(str.isdigit, phone_no))
        if len(clean_phone) > 10 and clean_phone.startswith("91"):
            clean_phone = clean_phone[2:]
        sms_payload = {
            "phone": clean_phone,
            "msg": message
        }
        SMS_OUTBOX_QUEUE.append(sms_payload)
        print(f"📥 [Custom Queue] SMS Added to Outbox Queue for {clean_phone}")
        return "Sent"
    except Exception as e:
        print(f"❌ [Queue Failed] Error: {str(e)}")
        return "Failed"
'''

# --- Centralized User Fetcher Helper ---
def get_student_parent_info(cur, student_id: int):
    # This queries your standard AAU users storage mapping structure
    cur.execute("""
        SELECT name, enrollment_no, parent_email, parent_phone 
        FROM users WHERE user_id = %s
    """, (student_id,))
    res = cur.fetchone()
    if not res:
        return None
    # Flexible mapping logic for safe fallback on different DB cursor profiles
    if isinstance(res, dict):
        return res
    return {
        "name": res[0],
        "enrollment_no": res[1],
        "parent_email": res[2],
        "parent_phone": res[3]
    }


# --- 🚀 REWRITTEN AND INTEGRATED APIS ---

@app.post("/disciplinary/create-case")
def create_case(data: CreateCaseModel):
    con = get_connection()
    cur = con.cursor(pymysql.cursors.DictCursor) # Safe execution pattern mapping
    try:
        cur.execute("""
            INSERT INTO disciplinary_cases (title, description, case_type, target_type, created_by)
            VALUES (%s,%s,%s,%s,%s)
        """, (data.title, data.description, data.case_type, data.target_type, data.created_by))

        case_id = cur.lastrowid

        for student_id in data.student_ids:

            cur.execute("""
                INSERT INTO disciplinary_case_students
                (
                    case_id,
                    student_id
                )
                VALUES
                (%s,%s)
            """, (
                case_id,
                student_id
            ))

            cur.execute("""
                SELECT
                    name,
                    fcm_token
                FROM users
                WHERE user_id=%s
            """, (
                student_id,
            ))

            student = cur.fetchone()

            if student and student["fcm_token"]:

                send_notification(
                    student["fcm_token"],
                    "Disciplinary Case Opened",
                    f"A disciplinary case has been opened against you.\nCase: {data.title}"
                )
            # Fetch Parent Info details and alert channels
            info = get_student_parent_info(cur, student_id)
            if info:
                email_sub = f"Administrative Activity Log: Ward Track opened"
                
                email_html = f"""
                <p>Dear Parent,</p>
                <p>This is a routine notification to inform you that a standard activity tracking log has been opened regarding your ward <b>{info['name']}</b> (Enrollment: {info['enrollment_no']}) inside our administration tracking interface.</p>
                <p><b>Incident Title:</b> {data.title}<br><b>Context Details:</b> {data.description}</p>
                <p><i>Note: There is no need to panic. This is an informational logging verification step.</i></p>
                """

                sms_msg = f"AAU Hostel Log: A tracking file for '{data.title}' has been logged into the student application profile of {info['name']}. Kindly review your email."

                # Special Case: If explicit college physical presence invitation triggered
                if data.need_parent_meeting == 1:
                    email_html += "<p style='color:crimson; font-weight:bold;'>⚠️ NOTICE: The hostel authorities request you to kindly schedule an official visit to the institute campus desk for a brief monitoring evaluation meet with the Rector Office.</p>"
                    sms_msg += " URGENT: You are requested to visit the hostel office for an administration meet."

                if info['parent_email']:
                    send_brevo_email(info['parent_email'], email_sub, email_html)
                if info['parent_phone']:
                    send_custom_sms_gateway(info['parent_phone'], sms_msg)

        con.commit()
        return {"success": True, "case_id": case_id}
    except Exception as e:
        con.rollback()
        return {"success": False, "message": str(e)}
    finally:
        cur.close()
        con.close()


@app.post("/disciplinary/issue-fine")
def issue_fine(data: FineModel):
    con = get_connection()
    cur = con.cursor(pymysql.cursors.DictCursor)
    try:
        cur.execute("""
            INSERT INTO fines (case_id, student_id, amount, reason)
            VALUES (%s,%s,%s,%s)
        """, (data.case_id, data.student_id, data.amount, data.reason))

        cur.execute("""
            UPDATE disciplinary_cases SET status='FineIssued' WHERE case_id=%s
        """, (data.case_id,))

        # Fetch tracking context targets for routing
        info = get_student_parent_info(cur, data.student_id)
        if info:
            email_sub = f"Fine Assessment Clearance Invoice"
            email_html = f"""
            <p>Dear Parent,</p>
            <p>An official fine of <b>₹{data.amount}</b> has been generated regarding your ward <b>{info['name']}</b> due to violation rules parameter statement: <i>{data.reason}</i>.</p>
            <p>Please advise your ward to clear this fine record using their mobile app panel balance dashboard.</p>
            """
            sms_msg = f"AAU Discipline Alert: A fine of Rs.{data.amount} for '{data.reason}' has been updated to {info['name']}'s balance interface. Kindly instruct them to settle the due statements."

            if info['parent_email']:
                send_brevo_email(info['parent_email'], email_sub, email_html)
            if info['parent_phone']:
                send_custom_sms_gateway(info['parent_phone'], sms_msg)

        con.commit()
        return {"success": True}
    except Exception as e:
        con.rollback()
        return {"success": False, "message": str(e)}
    finally:
        cur.close()
        con.close()


@app.post("/disciplinary/mark-paid")
def mark_paid(data: FinePaidModel):
    con = get_connection()
    cur = con.cursor(pymysql.cursors.DictCursor)
    try:
        # Pull transactional context data for parsing tracking paths before applying updates
        cur.execute("SELECT student_id, amount, reason FROM fines WHERE fine_id = %s", (data.fine_id,))
        fine_record = cur.fetchone()

        cur.execute("""
            UPDATE fines SET status='Paid', paid_at=NOW() WHERE fine_id=%s
        """, (data.fine_id,))

        if fine_record:
            st_id = fine_record['student_id'] if isinstance(fine_record, dict) else fine_record[0]
            amt = fine_record['amount'] if isinstance(fine_record, dict) else fine_record[1]
            rsn = fine_record['reason'] if isinstance(fine_record, dict) else fine_record[2]

            info = get_student_parent_info(cur, st_id)
            if info:
                email_sub = f"Disciplinary Settlement Verification Receipt"
                email_html = f"""
                <p>Dear Parent,</p>
                <p>This is to confirm that the fine statement of <b>₹{amt}</b> allocated under rule tracking segment (<i>{rsn}</i>) for <b>{info['name']}</b> has been successfully processed and marked as **PAID** inside the main administrative system.</p>
                """
                sms_msg = f"AAU Settlement Info: Fine account payment clearing verified successfully for student {info['name']}. Balance state clear."

                if info['parent_email']:
                    send_brevo_email(info['parent_email'], email_sub, email_html)
                if info['parent_phone']:
                    send_custom_sms_gateway(info['parent_phone'], sms_msg)

        con.commit()
        return {"success": True}
    except Exception as e:
        con.rollback()
        return {"success": False, "message": str(e)}
    finally:
        cur.close()
        con.close()


@app.post("/disciplinary/close-case")
def close_case(data: CloseCaseModel):
    con = get_connection()
    cur = con.cursor(pymysql.cursors.DictCursor)
    try:
        cur.execute("""
            UPDATE disciplinary_cases SET status='Closed' WHERE case_id=%s
        """, (data.case_id,))

        # Extract case titles for formatting non-panic closure briefs
        cur.execute("SELECT title FROM disciplinary_cases WHERE case_id = %s", (data.case_id,))
        case_meta = cur.fetchone()
        case_title = (case_meta['title'] if isinstance(case_meta, dict) else case_meta[0]) if case_meta else "Disciplinary Case"

        # Query all individual targets mapped in relational model table paths
        cur.execute("SELECT student_id FROM disciplinary_case_students WHERE case_id = %s", (data.case_id,))
        students_mapped = cur.fetchall()

        for profile in students_mapped:
            st_id = profile['student_id'] if isinstance(profile, dict) else profile[0]
            info = get_student_parent_info(cur, st_id)
            if info:
                email_sub = f"Disciplinary File Investigation Status: CLOSED"
                email_html = f"""
                <p>Dear Parent,</p>
                <p>We are pleased to inform you that the administrative open investigation case regarding <b>{case_title}</b> has been officially **RESOLVED and CLOSED** by the institute authority desk.</p>
                <p>The system standing parameters for your ward <b>{info['name']}</b> are back to clean validation levels.</p>
                """
                sms_msg = f"AAU Administration Updates: Case file '{case_title}' has been successfully completed and CLOSED for {info['name']}."

                if info['parent_email']:
                    send_brevo_email(info['parent_email'], email_sub, email_html)
                if info['parent_phone']:
                    send_custom_sms_gateway(info['parent_phone'], sms_msg)

        con.commit()
        return {"success": True}
    except Exception as e:
        con.rollback()
        return {"success": False, "message": str(e)}
    finally:
        cur.close()
        con.close()


# --- Keeping your remaining passive getter routes untouched ---
@app.get("/disciplinary/all-cases")
def all_cases():
    con = get_connection()
    cur = con.cursor(pymysql.cursors.DictCursor)
    cur.execute("SELECT * FROM disciplinary_cases ORDER BY case_id DESC")
    data = cur.fetchall()
    cur.close()
    con.close()
    return {"success": True, "cases": data}

@app.get("/disciplinary/case/{case_id}")
def case_details(case_id: int):
    con = get_connection()
    cur = con.cursor(pymysql.cursors.DictCursor)
    cur.execute("SELECT * FROM disciplinary_cases WHERE case_id=%s", (case_id,))
    case = cur.fetchone()
    cur.execute("SELECT * FROM disciplinary_remarks WHERE case_id=%s", (case_id,))
    remarks = cur.fetchall()
    cur.close()
    con.close()
    return {"success": True, "case": case, "remarks": remarks}

@app.get("/disciplinary/student-cases/{student_id}")
def student_cases(student_id: int):
    con = get_connection()
    cur = con.cursor(pymysql.cursors.DictCursor)
    cur.execute("""
        SELECT dc.* FROM disciplinary_cases dc
        INNER JOIN disciplinary_case_students dcs ON dc.case_id = dcs.case_id
        WHERE dcs.student_id=%s ORDER BY dc.case_id DESC
    """, (student_id,))
    data = cur.fetchall()
    cur.close()
    con.close()
    return {"success": True, "cases": data}

@app.get("/disciplinary/pending-fines")
def pending_fines():
    con = get_connection()
    cur = con.cursor(pymysql.cursors.DictCursor)
    cur.execute("SELECT * FROM fines WHERE status='Pending'")
    data = cur.fetchall()
    cur.close()
    con.close()
    return {"success": True, "fines": data}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)

    #D:\cloudflared>cloudflared tunnel --url http://localhost:7860
