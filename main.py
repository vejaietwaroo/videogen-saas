from fastapi import FastAPI, HTTPException, Security, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import os
import fal_client

app = FastAPI(title="VideoGen SaaS API", version="4.2.0")

@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    return HTMLResponse(content="<h1>Error: index.html not found</h1>", status_code=404)

security = HTTPBearer()
SECRET_KEY = "your-secure-production-jwt-secret-key"

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
    prompt_text = data.get("prompt", "").strip()
    aspect_ratio = data.get("aspect_ratio", "9:16")
    avatar = data.get("avatar", "none")
    dialogue = data.get("dialogue", "").strip()

    if not prompt_text:
        raise HTTPException(status_code=400, detail="Prompt is required")

    if not os.environ.get("FAL_KEY"):
        raise HTTPException(status_code=500, detail="FAL_KEY environment variable is not configured on server.")

    try:
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
            raise HTTPException(status_code=500, detail="Video generated, but media URL could not be retrieved.")

        return {
            "status": "success",
            "message": "Video generated successfully via Fal.ai",
            "video_url": current_video_url
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fal.ai rendering failed: {str(e)}")
