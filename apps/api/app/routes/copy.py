# app/routes/copy.py
from flask import Blueprint, request, jsonify
from app.services.copy_guard import CopyGuard, CopyGuardError

copy_bp = Blueprint('copy', __name__, url_prefix='/api/copy')
copy_guard = CopyGuard()

@copy_bp.route('/render', methods=['GET', 'POST'])
def render():
    """
    Renders a message template.
    Accepts JSON body or query params.
    """
    if request.method == 'POST':
        data = request.get_json() or {}
    else:
        # For GET, we might need to parse variables from a JSON string or individual params
        data = request.args.to_dict()
        # Handle the 'variables' dict if passed as a string in query params
        if 'variables' in data and isinstance(data['variables'], str):
            import json
            try:
                data['variables'] = json.loads(data['variables'])
            except:
                pass

    message_key = data.get('message_key')
    language = data.get('language', 'ar')
    variables = data.get('variables', {})

    if not message_key:
        return jsonify({"error": "message_key is required"}), 422

    try:
        rendered = copy_guard.render(message_key, language, variables)
        return jsonify(rendered)
    except CopyGuardError as e:
        return jsonify({"error": str(e)}), 422
    except Exception as e:
        return jsonify({"error": "An unexpected error occurred", "details": str(e)}), 500

@copy_bp.route('/templates', methods=['GET'])
def list_templates():
    return jsonify(copy_guard.list_templates())

@copy_bp.route('/validate', methods=['POST'])
def validate():
    data = request.get_json() or {}
    message_key = data.get('message_key')
    variables = data.get('variables', {})

    if not message_key:
        return jsonify({"error": "message_key is required"}), 422

    try:
        missing = copy_guard.validate_variables(message_key, variables)
        return jsonify({"missing": missing, "is_valid": len(missing) == 0})
    except CopyGuardError as e:
        return jsonify({"error": str(e)}), 422
