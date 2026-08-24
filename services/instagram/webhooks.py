import hashlib
import hmac
from functools import wraps
from flask import has_request_context, request, jsonify

# Lazy import: services.ai_agent.ai_agent_app's package __init__ pulls in the
# full server module, which itself imports this module - importing
# webhook_logger at module load time creates a circular import whenever
# something imports services.instagram.webhooks before ai_agent_app has
# finished initializing (e.g. a standalone test module). Resolving it inside
# the functions below sidesteps the cycle without restructuring the package.
def _webhook_logger():
    from services.ai_agent.ai_agent_app.logger import webhook_logger

    return webhook_logger


def _log_rejected_attempt(reason: str, **extra: object) -> None:
    """Structured, greppable log line for a rejected/invalid webhook attempt.

    This is the durable record for security monitoring: no dedicated audit
    table exists for webhook traffic, so callers (e.g. CloudWatch Logs on
    EC2) alert on the "webhook_rejected" marker instead. Callable outside an
    active Flask request (e.g. from tests, or batch re-processing) - falls
    back to omitting request-specific fields rather than raising.
    """
    details = " ".join(f"{key}={value!r}" for key, value in extra.items())
    if has_request_context():
        remote_addr = request.headers.get("X-Forwarded-For", request.remote_addr)
        path = request.path
    else:
        remote_addr = "n/a"
        path = "n/a"
    _webhook_logger().warning(
        "webhook_rejected reason=%s remote_addr=%s path=%s %s",
        reason,
        remote_addr,
        path,
        details,
    )


def validate_meta_signature(app_secret: str):
    """
    Decorator to validate Meta X-Hub-Signature-256.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not app_secret:
                _webhook_logger().warning("META_APP_SECRET not configured - allowing request (router or local mode)")
                return f(*args, **kwargs)

            # Internal router / localhost requests bypass strict signature check if missing/mismatched
            client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
            is_internal_or_loopback = any(
                ip in client_ip for ip in ("127.0.0.1", "::1", "localhost")
            ) or bool(request.headers.get("X-Router-Request"))

            signature = request.headers.get("X-Hub-Signature-256")
            if not signature:
                _log_rejected_attempt("missing_signature")
                if is_internal_or_loopback:
                    _webhook_logger().info("Proceeding with internal/router request despite missing Meta signature")
                    return f(*args, **kwargs)
                return jsonify({"error": "missing_signature"}), 401

            # Signature format is 'sha256=...'
            if not signature.startswith("sha256="):
                _log_rejected_attempt("invalid_signature_format")
                if is_internal_or_loopback:
                    return f(*args, **kwargs)
                return jsonify({"error": "invalid_signature_format"}), 401

            expected_signature = signature.split("=", 1)[1]
            actual_signature = hmac.new(
                app_secret.encode("utf-8"),
                request.data,
                hashlib.sha256
            ).hexdigest()

            if not hmac.compare_digest(expected_signature, actual_signature):
                _log_rejected_attempt("signature_mismatch")
                if is_internal_or_loopback:
                    _webhook_logger().info("Proceeding with internal/router request despite signature mismatch")
                    return f(*args, **kwargs)
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
        if mode == "subscribe" and token and hmac.compare_digest(token, verify_token or ""):
            _webhook_logger().info("Webhook verified successfully")
            return challenge, 200
        else:
            _log_rejected_attempt("verify_token_mismatch", mode=mode)
            return "Forbidden", 403
    _log_rejected_attempt("missing_handshake_params", mode=mode, has_token=bool(token))
    return "Not Found", 404


def filter_entries_for_page(payload: dict, expected_page_id: str) -> tuple[list, list]:
    """Split webhook entries into (accepted, rejected) by page/IG business ID.

    Meta webhook payloads carry the receiving page's ID as entry["id"]. When
    META_PAGE_ID is configured, entries addressed to any other page are
    dropped and logged rather than processed - defense in depth in case this
    app is ever subscribed to more than one page/app, or a misdelivered
    callback arrives.
    """
    entries = payload.get("entry") or [] if isinstance(payload, dict) else []
    if not expected_page_id:
        return list(entries), []
    accepted, rejected = [], []
    for entry in entries:
        if isinstance(entry, str):
            try:
                entry_dict = json.loads(entry)
            except Exception:
                entry_dict = {}
        elif isinstance(entry, dict):
            entry_dict = entry
        else:
            entry_dict = {}
        entry_id = str(entry_dict.get("id") or "").strip()
        if entry_id == expected_page_id:
            accepted.append(entry)
        else:
            rejected.append(entry)
            _log_rejected_attempt("unexpected_page_id", entry_id=entry_id, expected=expected_page_id)
    return accepted, rejected
