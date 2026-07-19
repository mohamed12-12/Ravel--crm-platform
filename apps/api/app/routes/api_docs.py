from __future__ import annotations

from flask import Blueprint, jsonify, request

from services.api_contracts import build_crm_openapi_contract


api_docs_bp = Blueprint("api_docs", __name__, url_prefix="/api")


@api_docs_bp.get("/openapi.json")
def openapi_contract():
    return jsonify(build_crm_openapi_contract(base_url=request.host_url.rstrip("/")))
