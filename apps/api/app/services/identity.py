# app/services/identity.py
import re
from datetime import datetime
from app.models.traveler import Traveler
from app.models.booking import TripBooking, CEBooking
from app.models.lead import Lead
from app.models.interaction import Interaction
from app.models.handoff import HandoffQueue
from sqlalchemy import func

def normalize_phone(raw_phone: str) -> str:
    """
    - Strip spaces, dashes, parentheses
    - Add country code if missing (default: Egypt +20)
    - Remove leading 0 before adding country code
    - Handle WhatsApp format: remove @c.us suffix if present
    - Return E.164 format: +20XXXXXXXXXX
    """
    if not raw_phone:
        return ""
    
    # Handle WhatsApp format: remove @c.us or @s.whatsapp.net suffix if present
    phone = str(raw_phone).split('@')[0]
    
    # Strip spaces, dashes, parentheses and other non-numeric chars except +
    phone = re.sub(r'[^\d+]', '', phone)
    
    if not phone:
        return ""
        
    # Handle leading 00 as +
    if phone.startswith('00'):
        phone = '+' + phone[2:]
        
    # If it already has +, just return it
    if phone.startswith('+'):
        return phone
    
    # Remove leading 0 before adding country code
    if phone.startswith('0'):
        phone = phone[1:]
    
    # If number starts with common international prefixes, keep them, else default to +20
    # Egyptian mobile numbers are 10 digits after removing leading 0
    if len(phone) == 10 and phone.startswith(('10', '11', '12', '15')):
        return '+20' + phone
    elif len(phone) > 10 and phone.startswith(('966', '971', '974', '968', '33', '44', '49')):
        return '+' + phone
    return '+20' + phone

def generate_lookup_key(normalized_phone: str) -> str:
    """
    - Return last 10 digits only (for matching across country code variants)
    - Example: +201012345678 → 1012345678
    """
    if not normalized_phone:
        return ""
    # Remove all non-numeric
    digits = re.sub(r'\D', '', normalized_phone)
    return digits[-10:]

def find_duplicates(db_session) -> list[dict]:
    """
    - Query all Travelers grouped by Phone Lookup Key
    - Return groups where count > 1
    - Each group: { match_key, count, records: [Traveler, ...] }
    """
    duplicate_keys = db_session.query(
        Traveler.phone_lookup_key, 
        func.count(Traveler.traveler_id).label('count')
    ).filter(Traveler.phone_lookup_key != None, Traveler.phone_lookup_key != '') \
     .group_by(Traveler.phone_lookup_key) \
     .having(func.count(Traveler.traveler_id) > 1).all()
    
    if not duplicate_keys:
        return []
        
    keys = [item[0] for item in duplicate_keys]
    counts_map = {item[0]: item[1] for item in duplicate_keys}
    
    # Query all travelers matching any of those keys in a single batch query to avoid N+1 queries
    travelers = Traveler.query.filter(Traveler.phone_lookup_key.in_(keys)).all()
    
    # Group travelers in memory by phone_lookup_key
    grouped_travelers = {}
    for t in travelers:
        grouped_travelers.setdefault(t.phone_lookup_key, []).append(t.to_dict())
        
    results = []
    for key in keys:
        records = grouped_travelers.get(key, [])
        results.append({
            "match_key": key,
            "count": counts_map[key],
            "records": records
        })
    return results

def merge_travelers(master_id: str, alias_ids: list[str], db_session) -> dict:
    """
    - Keep master traveler's profile data
    - Move all bookings, leads, interactions from alias IDs to master ID
    - Set alias travelers status = 'Merged' 
    - Add note: 'Merged into {master_id} on {date}'
    - Return: { merged_count, master_id, moved_bookings, moved_leads }
    """
    master = db_session.get(Traveler, master_id)
    if not master:
        raise ValueError(f"Master traveler {master_id} not found")
        
    moved_bookings = 0
    moved_leads = 0
    moved_interactions = 0
    moved_handoffs = 0
    
    merge_date = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    # Deduplicate alias_ids and remove master_id if present
    unique_aliases = [aid for aid in set(alias_ids) if aid != master_id]
    
    for alias_id in unique_aliases:
        alias = db_session.get(Traveler, alias_id)
        if not alias: continue
        
        # Move TripBookings
        bookings = TripBooking.query.filter_by(traveler_id=alias_id).all()
        for b in bookings:
            b.traveler_id = master_id
            moved_bookings += 1
            
        # Move CEBookings
        ce_bookings = CEBooking.query.filter_by(traveler_id=alias_id).all()
        for b in ce_bookings:
            b.traveler_id = master_id
            moved_bookings += 1
            
        # Move Leads
        leads = Lead.query.filter_by(traveler_id=alias_id).all()
        for l in leads:
            l.traveler_id = master_id
            moved_leads += 1
            
        # Move Interactions
        interactions = Interaction.query.filter_by(traveler_id=alias_id).all()
        for i in interactions:
            i.traveler_id = master_id
            moved_interactions += 1
            
        # Move Handoffs
        handoffs = HandoffQueue.query.filter_by(traveler_id=alias_id).all()
        for h in handoffs:
            h.traveler_id = master_id
            moved_handoffs += 1
            
        # Update alias traveler status and notes
        alias.status = 'Merged'
        merge_note = f"Merged into {master_id} on {merge_date}"
        if alias.notes:
            alias.notes = f"{alias.notes}\n{merge_note}"
        else:
            alias.notes = merge_note
        
    db_session.commit()
    
    return {
        "merged_count": len(unique_aliases),
        "master_id": master_id,
        "moved_bookings": moved_bookings,
        "moved_leads": moved_leads,
        "moved_interactions": moved_interactions,
        "moved_handoffs": moved_handoffs
    }
