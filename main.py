from fastapi import FastAPI, HTTPException, Security, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import hmac
import hashlib
import os
import fal_client

app = FastAPI(title="VideoGen SaaS API", version="4.5.0")

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

USAGE_TRACKER = {}
MAX_RENDERS_ALLOWED = 50

@app.post("/api/verify-license")
def verify_license(payload: dict):
    license_key = payload.get("license_key")
    if license_key == "LTD-VIP-2026":
        token = jwt.encode({"sub": license_key, "tier": "lifetime"}, SECRET_KEY, algorithm="HS256")
        return {"status": "success", "access_token": token}
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

def extract_media_url(result: dict, keys=("video", "audio", "output")):
    for key in keys:
        if key in result:
            val = result[key]
            if isinstance(val, dict) and "url" in val:
                return val["url"]
            if isinstance(val, str) and val.startswith("http"):
                return val
    return None

@app.post("/api/render")
def secure_render(data: dict, user: dict = Depends(get_current_user)):
    license_key = user.get("sub", "unknown_user")
    
    current_usage = USAGE_TRACKER.get(license_key, 0)
    if current_usage >= MAX_RENDERS_ALLOWED:
        raise HTTPException(
            status_code=429, 
            detail=f"Usage limit reached ({MAX_RENDERS_ALLOWED} video renders max). Please top up your credits."
        )

    prompt_text = data.get("prompt", "").strip()
    video_format = data.get("video_format", "short_form") # short_form (9:16) or long_form (16:9)
    duration = int(data.get("duration", 10))
    avatar = data.get("avatar", "none")
    enable_captions = data.get("enable_captions", True)

    if not prompt_text:
        raise HTTPException(status_code=400, detail="Prompt is required")

    if not os.environ.get("FAL_KEY"):
        raise HTTPException(status_code=500, detail="FAL_KEY environment variable is not configured on server.")

    try:
        aspect_ratio = "9:16" if video_format == "short_form" else "16:9"
        
        final_prompt = prompt_text
        if avatar in AVATAR_PROMPTS:
            final_prompt = AVATAR_PROMPTS[avatar] + prompt_text

        # 1. Generate Video via Flux-3-Max
        video_handler = fal_client.submit(
            "blackforestlabs/flux-3-max/text-to-video",
            arguments={
                "prompt": final_prompt,
                "aspect_ratio": aspect_ratio,
                "duration": duration
            }
        )
        video_result = video_handler.get()
        raw_video_url = extract_media_url(video_result, ("video", "output"))

        if not raw_video_url:
            raise HTTPException(status_code=500, detail="Video generated, but media URL could not be retrieved.")

        final_video_url = raw_video_url

        # 2. Automatically apply Karaoke Captions if enabled
        if enable_captions:
            try:
                caption_handler = fal_client.submit(
                    "fal-ai/workflow-utilities/auto-subtitle",
                    arguments={
                        "video_url": raw_video_url,
                        "language": "en",
                        "font_name": "Montserrat",
                        "font_size": 90,
                        "font_weight": "bold",
                        "highlight_color": "purple",
                        "position": "bottom"
                    }
                )
                caption_result = caption_handler.get()
                captioned_url = extract_media_url(caption_result, ("video", "output"))
                if captioned_url:
                    final_video_url = captioned_url
            except Exception as cap_err:
                # Fallback to raw video if subtitle processing fails
                print(f"[Caption Warning] Subtitle generation skipped: {str(cap_err)}")

        USAGE_TRACKER[license_key] = current_usage + 1

        return {
            "status": "success",
            "message": "Video generated and captioned successfully via Fal.ai",
            "videos_rendered": USAGE_TRACKER[license_key],
            "limit": MAX_RENDERS_ALLOWED,
            "video_url": final_video_url
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fal.ai rendering failed: {str(e)}")

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
        print(f"[Licensing Success] Order {order_id} ({total_price}) processed for {customer_email}")
        
    return {"status": "received", "event": event_name}
