# app/models/copy_library.py
from app.extensions import db
import pandas as pd

class DMCopyLibrary(db.Model):
    __tablename__ = 'dm_copy_library'
    
    # Primary Key
    message_key = db.Column(db.String(100), primary_key=True)
    
    # Details
    flow_key = db.Column(db.String(100))
    step_key = db.Column(db.String(100))
    channel = db.Column(db.String(50))
    arabic_copy = db.Column(db.Text)
    english_copy = db.Column(db.Text)
    buttons_arabic = db.Column(db.Text)
    buttons_english = db.Column(db.Text)
    variables = db.Column(db.String(255))
    
    # Audit
    source_sheet = db.Column(db.String(100))
    active = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text)

    def __repr__(self):
        return f'<DMCopy {self.message_key}>'

    @classmethod
    def from_excel_row(cls, row: dict):
        """Creates a DMCopyLibrary instance from a pandas DataFrame row."""
        def clean(val):
            if pd.isna(val):
                return None
            return val

        def to_bool(val):
            val = clean(val)
            if val is None: return True
            if isinstance(val, bool): return val
            if str(val).lower() in ('false', 'no', '0'): return False
            return True

        return cls(
            message_key=clean(row.get("Message Key")),
            flow_key=clean(row.get("Flow Key")),
            step_key=clean(row.get("Step Key")),
            channel=clean(row.get("Channel")),
            arabic_copy=clean(row.get("Arabic Copy")),
            english_copy=clean(row.get("English Copy")),
            buttons_arabic=clean(row.get("Buttons Arabic")),
            buttons_english=clean(row.get("Buttons English")),
            variables=clean(row.get("Variables")),
            source_sheet=clean(row.get("Source Sheet")),
            active=to_bool(row.get("Active")),
            notes=clean(row.get("Notes"))
        )

    def to_dict(self):
        """Returns all fields as a JSON-serializable dict."""
        return {
            "message_key": self.message_key,
            "flow_key": self.flow_key,
            "step_key": self.step_key,
            "channel": self.channel,
            "arabic_copy": self.arabic_copy,
            "english_copy": self.english_copy,
            "buttons_arabic": self.buttons_arabic,
            "buttons_english": self.buttons_english,
            "variables": self.variables,
            "source_sheet": self.source_sheet,
            "active": self.active,
            "notes": self.notes
        }

class LanguageTemplate(db.Model):
    __tablename__ = 'language_templates'
    
    id = db.Column(db.Integer, primary_key=True)
    template_key = db.Column(db.String(100))
    language = db.Column(db.String(20)) # ar / en
    use_case = db.Column(db.String(100))
    approved_copy = db.Column(db.Text)
    variables = db.Column(db.String(255))
    status = db.Column(db.String(50))
    notes = db.Column(db.Text)

    def __repr__(self):
        return f'<LangTemplate {self.template_key} ({self.language})>'

    @classmethod
    def from_excel_row(cls, row: dict):
        """Creates a LanguageTemplate instance from a pandas DataFrame row."""
        def clean(val):
            if pd.isna(val):
                return None
            return val

        return cls(
            template_key=clean(row.get("Template Key")),
            language=clean(row.get("Language")),
            use_case=clean(row.get("Use Case")),
            approved_copy=clean(row.get("Approved Copy")),
            variables=clean(row.get("Variables")),
            status=clean(row.get("Status")),
            notes=clean(row.get("Notes"))
        )

    def to_dict(self):
        """Returns all fields as a JSON-serializable dict."""
        return {
            "template_key": self.template_key,
            "language": self.language,
            "use_case": self.use_case,
            "approved_copy": self.approved_copy,
            "variables": self.variables,
            "status": self.status,
            "notes": self.notes
        }
