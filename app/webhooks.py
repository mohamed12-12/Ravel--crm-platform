import hashlib
import hmac
import json
from functools import wraps
from flask import request, jsonify
from app.logger import webhook_logger

def validate_meta_signature(app_secret: str):
    """
    Decorator to validate Meta X-Hub-Signature-256.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not app_secret:
                webhook_logger.error("META_APP_SECRET not configured")
                return jsonify({"error": "config_error"}), 500
                
            signature = request.headers.get("X-Hub-Signature-256")
            if not signature:
                webhook_logger.warning("Missing X-Hub-Signature-256")
                return jsonify({"error": "missing_signature"}), 401
                
            # Signature format is 'sha256=...'
            if not signature.startswith("sha256="):
                return jsonify({"error": "invalid_signature_format"}), 401
                
            expected_signature = signature.split("=")[1]
            actual_signature = hmac.new(
                app_secret.encode("utf-8"),
                request.data,
                hashlib.sha256
            ).hexdigest()
            
            if not hmac.compare_digest(expected_signature, actual_signature):
                webhook_logger.warning("Signature mismatch")
                return jsonify({"error": "signature_mismatch"}), 401
                
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def verify_webhook(verify_token: str):
    """
    Verification logic for Meta GET /webhook.
    """
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    
    if mode and token:
        if mode == "subscribe" and token == verify_token:
            webhook_logger.info("Webhook verified successfully")
            return challenge, 200
        else:
            webhook_logger.warning(f"Verification failed: token={token}")
            return "Forbidden", 403
    return "Not Found", 404
