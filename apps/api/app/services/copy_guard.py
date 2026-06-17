# app/services/copy_guard.py
import re
from app.models.copy_library import DMCopyLibrary
from app.extensions import db

class CopyGuardError(Exception):
    """Raised when a variable is missing or template key not found"""
    pass

class CopyGuard:
    def render(self, message_key: str, language: str, variables: dict) -> dict:
        """
        1. Look up message_key in DMCopyLibrary
        2. Select Arabic or English copy based on language param
        3. Replace {variable} placeholders with values from variables dict
        4. If any variable is missing from the dict, raise CopyGuardError (do NOT guess)
        5. Return: { text: str, buttons: list[str], message_key: str, language: str }
        """
        template = DMCopyLibrary.query.get(message_key)
        if not template:
            raise CopyGuardError(f"Template key '{message_key}' not found.")

        if language.lower() == 'ar':
            text = template.arabic_copy
            buttons_raw = template.buttons_arabic
        else:
            text = template.english_copy
            buttons_raw = template.buttons_english

        if not text:
            raise CopyGuardError(f"Copy for language '{language}' not found for template '{message_key}'.")

        # Normalize placeholders: strip leading/trailing spaces inside curly braces, e.g. { name } -> {name}
        text = re.sub(r'\{\s*(.*?)\s*\}', r'{\1}', text)
        if buttons_raw:
            buttons_raw = re.sub(r'\{\s*(.*?)\s*\}', r'{\1}', buttons_raw)

        # Find all required variables
        required_vars = set(val.split(':')[0].strip() for val in re.findall(r'\{(.*?)\}', text))
        if buttons_raw:
            required_vars.update(val.split(':')[0].strip() for val in re.findall(r'\{(.*?)\}', buttons_raw))

        # Normalize variables dict keys by stripping whitespace
        normalized_variables = {k.strip(): v for k, v in variables.items()}

        # Check for missing variables
        missing = [v for v in required_vars if v not in normalized_variables]
        if missing:
            raise CopyGuardError(f"Missing required variables for '{message_key}': {', '.join(missing)}")

        # Render text
        try:
            rendered_text = text.format(**normalized_variables)
        except KeyError as e:
            raise CopyGuardError(f"Variable {str(e)} missing during formatting.")

        # Render buttons
        buttons = []
        if buttons_raw:
            raw_list = [b.strip() for b in re.split(r'[\n,]', buttons_raw) if b.strip()]
            try:
                buttons = [b.format(**normalized_variables) for b in raw_list]
            except KeyError as e:
                raise CopyGuardError(f"Variable {str(e)} missing in buttons during formatting.")

        return {
            "text": rendered_text,
            "buttons": buttons,
            "message_key": message_key,
            "language": language
        }

    def list_templates(self) -> list[dict]:
        """Return all active templates for the admin Copy Library UI"""
        templates = DMCopyLibrary.query.filter_by(active=True).all()
        return [t.to_dict() for t in templates]
    
    def validate_variables(self, message_key: str, variables: dict) -> list[str]:
        """Return list of missing required variables before rendering"""
        template = DMCopyLibrary.query.get(message_key)
        if not template:
            raise CopyGuardError(f"Template key '{message_key}' not found.")
            
        required_vars = set()
        
        def extract_vars(raw_text):
            if not raw_text:
                return set()
            # Normalize brackets first: { name } -> {name}
            normalized = re.sub(r'\{\s*(.*?)\s*\}', r'{\1}', raw_text)
            return set(val.split(':')[0].strip() for val in re.findall(r'\{(.*?)\}', normalized))
            
        if template.arabic_copy:
            required_vars.update(extract_vars(template.arabic_copy))
        if template.english_copy:
            required_vars.update(extract_vars(template.english_copy))
        if template.buttons_arabic:
            required_vars.update(extract_vars(template.buttons_arabic))
        if template.buttons_english:
            required_vars.update(extract_vars(template.buttons_english))
            
        normalized_variables = {k.strip(): v for k, v in variables.items()}
        missing = [v for v in required_vars if v not in normalized_variables]
        return missing
