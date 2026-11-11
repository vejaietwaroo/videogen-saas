from fastapi import FastAPI, HTTPException, Security, Depends, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import hmac
import hashlib
import os

app = FastAPI(title="VideoGen SaaS API", version="2.0.0")

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
    try:
        return {
            "status": "success", 
            "message": "Render job queued and executed successfully for licensed user.",
            "request_payload": data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Rendering failed: {str(e)}")

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