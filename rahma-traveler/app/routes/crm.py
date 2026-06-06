# app/routes/crm.py
from flask import Blueprint, request, jsonify
from app.services.identity import merge_travelers, find_duplicates
from app.extensions import db
import logging

logger = logging.getLogger(__name__)

crm_bp = Blueprint('crm', __name__, url_prefix='/api/crm')

@crm_bp.route('/resolve-identity', methods=['POST'])
def resolve_identity():
    """
    Merges duplicate travelers into a master traveler record.
    Expects: { "master_id": "TR001", "alias_ids": ["TR002", "TR003"] }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing JSON body"}), 400
        
    master_id = data.get('master_id')
    alias_ids = data.get('alias_ids')
    
    if not master_id or not alias_ids:
        return jsonify({"error": "master_id and alias_ids are required"}), 422
        
    if not isinstance(alias_ids, list):
        return jsonify({"error": "alias_ids must be a list"}), 422

    try:
        result = merge_travelers(master_id, alias_ids, db.session)
        return jsonify({
            "status": "success",
            "message": f"Successfully merged {result['merged_count']} travelers into {master_id}",
            "details": result
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.error(f"Error merging travelers: {e}", exc_info=True)
        return jsonify({"error": "An internal server error occurred during merge."}), 500

@crm_bp.route('/duplicates', methods=['GET'])
def list_duplicates():
    """
    Returns a list of potential duplicate traveler groups.
    """
    try:
        duplicates = find_duplicates(db.session)
        return jsonify({
            "count": len(duplicates),
            "duplicates": duplicates
        })
    except Exception as e:
        logger.error(f"Failed to fetch duplicates: {e}", exc_info=True)
        return jsonify({"error": "Failed to fetch duplicates", "details": str(e)}), 500
