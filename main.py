from fastapi import FastAPI, HTTPException, Security, Depends, Request, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import hmac
import hashlib
import os
import uuid
import sqlite3
import subprocess
import tempfile
import requests
import imageio_ffmpeg
import resend
import fal_client

app = FastAPI(title="VideoGen SaaS API", version="5.1.0")

# --- DATABASE SETUP ---
DB_FILE = "videogen_saas.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            license_key TEXT PRIMARY KEY,
            tier TEXT,
            renders_used INTEGER DEFAULT 0,
            max_renders INTEGER DEFAULT 10
        )
    """)
    cursor.execute("""
        INSERT OR IGNORE INTO licenses (license_key, tier, renders_used, max_renders)
        VALUES ('LTD-VIP-2026', 'admin_vip', 0, 500)
    """)
    conn.commit()
    conn.close()

init_db()

# In-memory job tracker for background async rendering
RENDER_JOBS = {}

resend.api_key = os.environ.get("RESEND_API_KEY", "re_your_resend_api_key")

@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    return HTMLResponse(content="<h1>Error: index.html not found</h1>", status_code=404)

security = HTTPBearer()
SECRET_KEY = "your-secure-production-jwt-secret-key"
LEMON_SQUEEZY_SIGNING_SECRET = os.environ.get("LEMON_SQUEEZY_SIGNING_SECRET", "your-lemon-squeezy-webhook-secret")

@app.post("/api/verify-license")
def verify_license(payload: dict):
    license_key = payload.get("license_key", "").strip()
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT tier, renders_used, max_renders FROM licenses WHERE license_key = ?", (license_key,))
    row = cursor.fetchone()
    conn.close()

    if row:
        tier, used, max_r = row
        token = jwt.encode({"sub": license_key, "tier": tier}, SECRET_KEY, algorithm="HS256")
        return {
            "status": "success", 
            "access_token": token,
            "renders_used": used,
            "max_renders": max_r
        }
    
    raise HTTPException(status_code=403, detail="Invalid license key")

def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=["HS256"])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired authentication token")

AVATAR_PROMPTS = {
    "female_presenter": "Cinematic shot of a female presenter performing active physical movements and demonstrating a routine while turning to speak directly to the camera, talking and gesturing naturally, 4k, ",
    "male_presenter": "Cinematic shot of a male presenter performing active physical movements and demonstrating a routine while turning to speak directly to the camera, talking and gesturing naturally, 4k, ",
    "wellness_coach": "Cinematic shot of a wellness guide demonstrating active Tai Chi and stretching movements on her yoga mat, turning to look directly at the audience with natural facial expressions and mouth movement while instructing everyone, 4k, ",
    "noir_detective": "Cinematic shot of a thriller character moving dynamically in a dramatic environment while turning to speak directly to the camera, intense action and facial expression, 4k, "
}

MODEL_ENDPOINTS = {
    "economy": "fal-ai/wan/v2.7/text-to-video",
    "flagship": "blackforestlabs/flux-3-max/text-to-video"
}

def extract_media_url(result: dict, keys=("video", "audio", "output", "audio_file")):
    for key in keys:
        if key in result:
            val = result[key]
            if isinstance(val, dict) and "url" in val:
                return val["url"]
            if isinstance(val, str) and val.startswith("http"):
                return val
    return None

def mix_audio_with_ffmpeg(video_url: str) -> str:
    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        response = requests.get(video_url, stream=True, timeout=30)
        if response.status_code != 200:
            return video_url
            
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_in:
            for chunk in response.iter_content(chunk_size=8192):
                tmp_in.write(chunk)
            in_path = tmp_in.name

        out_path = in_path.replace(".mp4", "_mixed.mp4")

        cmd = [
            ffmpeg_exe, "-y", "-i", in_path,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            out_path
        ]
        
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        if result.returncode == 0 and os.path.exists(out_path):
            os.unlink(in_path)
            os.unlink(out_path)
            return video_url 
        
        return video_url
    except Exception:
        return video_url

def process_render_job(job_id: str, license_key: str, active_api_key: str, using_byok: bool, current_usage: int, max_renders: int, data: dict):
    try:
        scenes = data.get("scenes", [])
        video_format = data.get("video_format", "long_form")
        model_tier = data.get("model_tier", "economy")
        duration = int(data.get("duration", 5))
        avatar = data.get("avatar", "none")
        enable_captions = data.get("enable_captions", True)

        client = fal_client.SyncClient(key=active_api_key)
        selected_endpoint = MODEL_ENDPOINTS.get(model_tier, MODEL_ENDPOINTS["economy"])
        aspect_ratio = "16:9" if video_format == "long_form" else "9:16"
        rendered_clips = []
        total_scenes = len(scenes[:10])

        for idx, scene_text in enumerate(scenes[:10]):
            RENDER_JOBS[job_id]["progress"] = f"Generating Scene {idx + 1} of {total_scenes}..."
            
            final_prompt = scene_text.strip()
            if avatar in AVATAR_PROMPTS:
                final_prompt = AVATAR_PROMPTS[avatar] + final_prompt

            if model_tier == "economy":
                args = {
                    "prompt": final_prompt,
                    "aspect_ratio": aspect_ratio,
                    "resolution": "720p"
                }
            else:
                args = {
                    "prompt": final_prompt,
                    "aspect_ratio": aspect_ratio,
                    "duration": duration
                }

            video_result = client.run(selected_endpoint, arguments=args)
            raw_video_url = extract_media_url(video_result, ("video", "output"))

            if raw_video_url:
                final_scene_url = mix_audio_with_ffmpeg(raw_video_url)
                
                if enable_captions:
                    RENDER_JOBS[job_id]["progress"] = f"Burning Captions on Scene {idx + 1} of {total_scenes}..."
                    try:
                        caption_result = client.run(
                            "fal-ai/workflow-utilities/auto-subtitle",
                            arguments={
                                "video_url": final_scene_url,
                                "language": "en",
                                "font_name": "Montserrat",
                                "font_size": 90,
                                "font_weight": "bold",
                                "highlight_color": "purple",
                                "position": "bottom"
                            }
                        )
                        captioned_url = extract_media_url(caption_result, ("video", "output"))
                        if captioned_url:
                            final_scene_url = captioned_url
                    except Exception:
                        pass

                rendered_clips.append(final_scene_url)

        if not rendered_clips:
            RENDER_JOBS[job_id]["status"] = "failed"
            RENDER_JOBS[job_id]["error"] = "Fal.ai returned no video URLs. Verify your API key has active credits."
            return

        new_usage = current_usage if using_byok else (current_usage + len(rendered_clips))
        if not using_byok:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute("UPDATE licenses SET renders_used = ? WHERE license_key = ?", (new_usage, license_key))
            conn.commit()
            conn.close()

        RENDER_JOBS[job_id]["status"] = "completed"
        RENDER_JOBS[job_id]["result"] = {
            "videos_rendered": new_usage,
            "limit": max_renders,
            "byok_active": using_byok,
            "video_urls": rendered_clips,
            "video_url": rendered_clips[0]
        }

    except Exception as e:
        RENDER_JOBS[job_id]["status"] = "failed"
        RENDER_JOBS[job_id]["error"] = str(e)

@app.post("/api/render")
def start_render_job(data: dict, background_tasks: BackgroundTasks, user: dict = Depends(get_current_user)):
    license_key = user.get("sub", "unknown_user")
    user_fal_key = data.get("user_fal_key", "").strip()
    using_byok = len(user_fal_key) > 10

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT renders_used, max_renders FROM licenses WHERE license_key = ?", (license_key,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=403, detail="License not found in database.")
    
    current_usage, max_renders = row

    if not using_byok and current_usage >= max_renders:
        raise HTTPException(
            status_code=429, 
            detail=f"Free starter credits used ({current_usage}/{max_renders}). Enter your own Fal.ai API key above for unlimited lifetime rendering!"
        )

    active_api_key = user_fal_key if using_byok else os.environ.get("FAL_KEY", "")
    if not active_api_key:
        raise HTTPException(status_code=500, detail="No API key available. Please enter your Fal.ai API key in the studio settings.")

    scenes = data.get("scenes", [])
    if not scenes or not isinstance(scenes, list):
        single_prompt = data.get("prompt", "").strip()
        if not single_prompt:
            raise HTTPException(status_code=400, detail="Scene script or prompt is required")
        data["scenes"] = [single_prompt]

    job_id = str(uuid.uuid4())
    RENDER_JOBS[job_id] = {
        "status": "processing",
        "progress": "Initializing AI Engine...",
        "result": None,
        "error": None
    }

    background_tasks.add_task(
        process_render_job, job_id, license_key, active_api_key, using_byok, current_usage, max_renders, data
    )

    return {"status": "queued", "job_id": job_id}

@app.get("/api/status/{job_id}")
def get_job_status(job_id: str, user: dict = Depends(get_current_user)):
    job = RENDER_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Render job not found")
    return job

@app.post("/api/webhook/lemonsqueezy")
async def lemonsqueezy_webhook(request: Request):
    body_bytes = await request.body()
    signature = request.headers.get("X-Signature", "")
    
    computed_sig = hmac.new(
        LEMON_SQUEEZY_SIGNING_SECRET.encode("utf-8"),
        body_bytes,
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(computed_sig, signature):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")
        
    event = await request.json()
    event_name = event.get("meta", {}).get("event_name")
    
    if event_name == "order_created":
        data = event.get("data", {})
        attributes = data.get("attributes", {})
        customer_email = attributes.get("user_email")
        order_id = attributes.get("identifier")
        total_price = attributes.get("total_formatted")
        
        new_key = f"VIP-{order_id[:8].upper()}-2026"
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO licenses (license_key, tier, renders_used, max_renders)
            VALUES (?, 'lifetime_byok', 0, 10)
        """, (new_key,))
        conn.commit()
        conn.close()
        
        try:
            resend.Emails.send({
                "from": "VideoGen Studio <onboarding@resend.dev>",
                "to": [customer_email],
                "subject": "Your VideoGen Studio Lifetime License Key",
                "html": f"""
                    <h2>Thank you for your purchase!</h2>
                    <p>Your order ({order_id}) for {total_price} has been processed.</p>
                    <p>Your Lifetime License Key is:</p>
                    <h3 style="background:#f4f4f4; padding:10px; display:inline-block;">{new_key}</h3>
                    <p>Includes <strong>10 complimentary starter video renders</strong>! After that, simply plug in your own Fal.ai API key inside the studio for unlimited lifetime video generation.</p>
                    <p>Launch your studio here: <a href="https://videogen-saas.onrender.com">https://videogen-saas.onrender.com</a></p>
                """
            })
        except Exception as email_err:
            print(f"[Email Error] Failed to send email to {customer_email}: {str(email_err)}")
        
    return {"status": "received", "event": event_name}
