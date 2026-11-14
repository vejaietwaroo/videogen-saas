from fastapi import FastAPI, HTTPException, Security, Depends, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import hmac
import hashlib
import os
import fal_client

app = FastAPI(title="VideoGen SaaS API", version="4.1.1")

@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    return HTMLResponse(content="<h1>Error: index.html not found</h1>", status_code=404)

security = HTTPBearer()

SECRET_KEY = "your-secure-production-jwt-secret-key"
LEMON_SQUEEZY_SIGNING_SECRET = "your-lemon-squeezy-webhook-secret"

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

VOICE_MAP = {
    "female_calm": "af_sarah",
    "male_cinematic": "am_adam",
    "female_suspense": "af_bella",
    "male_energetic": "am_michael"
}

MUSIC_PROMPTS = {
    "cinematic_ambient": "Cinematic ambient atmospheric soundtrack, warm synth pads, emotional and subtle background score, instrumental",
    "calm_lofi": "Calm relaxing lofi chill beats, gentle acoustic piano and soft vinyl warmth, peaceful wellness background music, instrumental",
    "suspense_thriller": "Dark psychological thriller suspense underscore, deep bass pulse, mysterious tension, instrumental",
    "upbeat_social": "Upbeat modern energetic corporate tech background music, clean rhythmic beat, inspiring and positive, instrumental"
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
    prompt_text = data.get("prompt", "").strip()
    aspect_ratio = data.get("aspect_ratio", "9:16")
    avatar = data.get("avatar", "none")
    voice = data.get("voice", "none")
    dialogue = data.get("dialogue", "").strip()
    music = data.get("music", "none")
    captions = bool(data.get("captions", False))

    if not prompt_text:
        raise HTTPException(status_code=400, detail="Prompt is required")

    if not os.environ.get("FAL_KEY"):
        raise HTTPException(status_code=500, detail="FAL_KEY environment variable is not configured on server.")

    try:
        # Step 1: Base Video Generation
        final_prompt = prompt_text
        if avatar in AVATAR_PROMPTS:
            final_prompt = AVATAR_PROMPTS[avatar] + prompt_text
        elif dialogue:
            final_prompt = "Cinematic shot of a person standing in their environment, turning to speak directly to the camera, " + prompt_text

        video_handler = fal_client.submit(
            "blackforestlabs/flux-3-max/text-to-video",
            arguments={
                "prompt": final_prompt,
                "aspect_ratio": aspect_ratio,
                "duration": 10
            }
        )
        video_result = video_handler.get()
        current_video_url = extract_media_url(video_result, ("video", "output"))

        if not current_video_url:
            raise HTTPException(status_code=500, detail="Base video generated, but media URL could not be retrieved.")

        # Step 2: Optional Speech Synthesis + Lip-Sync
        if dialogue and (voice != "none" or avatar != "none"):
            selected_voice = VOICE_MAP.get(voice, "af_sarah")
            tts_handler = fal_client.submit(
                "fal-ai/kokoro/american-english",
                arguments={
                    "prompt": dialogue,
                    "voice": selected_voice
                }
            )
            tts_result = tts_handler.get()
            audio_url = extract_media_url(tts_result, ("audio", "output"))

            if audio_url:
                lipsync_handler = fal_client.submit(
                    "fal-ai/sync-lipsync",
                    arguments={
                        "video_url": current_video_url,
                        "audio_url": audio_url
                    }
                )
                lipsync_result = lipsync_handler.get()
                synced_url = extract_media_url(lipsync_result, ("video", "output"))
                if synced_url:
                    current_video_url = synced_url

        # Step 3: Optional Background Music
        if music in MUSIC_PROMPTS:
            music_handler = fal_client.submit(
                "fal-ai/stable-audio",
                arguments={
                    "prompt": MUSIC_PROMPTS[music],
                    "seconds_total": 10
                }
            )
            music_result = music_handler.get()
            music_url = extract_media_url(music_result, ("audio_file", "audio", "output"))

            if music_url:
                merge_handler = fal_client.submit(
                    "fal-ai/ffmpeg-api/merge-audio-video",
                    arguments={
                        "video_url": current_video_url,
                        "audio_url": music_url
                    }
                )
                merge_result = merge_handler.get()
                merged_url = extract_media_url(merge_result, ("video", "output"))
                if merged_url:
                    current_video_url = merged_url

        # Step 4: Optional Auto-Captions Burn-In
        if captions and dialogue:
            caption_handler = fal_client.submit(
                "fal-ai/auto-caption",
                arguments={
                    "video_url": current_video_url,
                    "font_color": "white",
                    "highlight_color": "yellow",
                    "position": "bottom"
                }
            )
            caption_result = caption_handler.get()
            captioned_url = extract_media_url(caption_result, ("video", "output"))
            if captioned_url:
                current_video_url = captioned_url

        return {
            "status": "success",
            "message": "Video generated successfully via Fal.ai",
            "video_url": current_video_url
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
        print(f"[License Issued] Order {order_id} created for {customer_email}")
        
    return {"status": "received"}
