from fastapi import FastAPI, HTTPException, Security, Depends, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import hmac
import hashlib
import os
import fal_client

app = FastAPI(title="VideoGen SaaS API", version="2.1.0")

# Serve frontend dashboard directly
@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(html_path):
        return FileResponse(html_path)
    return {"error": "index.html not found in backend folder"}

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

@app.post("/api/render")
def secure_render(data: dict, user: dict = Depends(get_current_user)):
    prompt_text = data.get("prompt")
    if not prompt_text:
        raise HTTPException(status_code=400, detail="Prompt is required")

    # Ensure FAL_KEY is available in environment
    fal_key = os.environ.get("FAL_KEY")
    if not fal_key:
        raise HTTPException(status_code=500, detail="FAL_KEY environment variable is not configured on server.")

    try:
      
        # Call Fal.ai Text-to-Video model securely
        handler = fal_client.submit(
            "minimax/h3-max/text-to-video",
            arguments={
                "prompt": prompt_text,
                "prompt_expansion_mode": "disabled"
            }
        )
        result = handler.get()
        
        # Extract the video file URL from Fal.ai response
        video_url = None
        if "video" in result and "url" in result["video"]:
            video_url = result["video"]["url"]
        elif "output" in result:
            video_url = result["output"]

        if not video_url:
            raise HTTPException(status_code=500, detail="Video generated, but media URL could not be retrieved.")

        return {
            "status": "success", 
            "message": "Video generated successfully via Fal.ai",
            "video_url": video_url,
            "request_payload": data
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
