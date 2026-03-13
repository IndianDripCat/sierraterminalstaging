import pymongo
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
import random
import string

class MongoDB:
    def __init__(self):
        uri = os.getenv("MONGO_URI")
        self.client = pymongo.MongoClient(uri)
        self.db = self.client['sierra_applications']
        self.blocks = self.db['application_blocks']
        self.mel_tz = ZoneInfo("Australia/Melbourne")

    def is_user_blocked(self, user_id: int) -> bool:
        now = datetime.now(self.mel_tz)
        block = self.blocks.find_one({
            "user_id": user_id,
            "$or": [
                {"expires_at": None},
                {"expires_at": {"$gt": now}}
            ],
            "revoked_at": None
        })
        return block is not None

    def add_application_block(self, user_id, user_name, reason, evidence, issued_by_id, issued_by_name, expires_in, now_override=None):
        now = now_override if now_override else datetime.now(self.mel_tz)
        # Generate a 24-character hex string for block_id
        block_id = ''.join(random.choices('0123456789abcdef', k=24))
        expires_at = None
        if expires_in and expires_in.lower() != "never":
            try:
                if expires_in.endswith('d'):
                    expires_at = now + timedelta(days=int(expires_in[:-1]))
                elif expires_in.endswith('w'):
                    expires_at = now + timedelta(weeks=int(expires_in[:-1]))
                elif expires_in.endswith('mo'):
                    expires_at = now + timedelta(days=30*int(expires_in[:-2]))
            except Exception:
                expires_at = None
        block = {
            "block_id": block_id,
            "user_id": user_id,
            "user_name": user_name,
            "reason": reason,
            "evidence": evidence,
            "issued_by_id": issued_by_id,
            "issued_by_name": issued_by_name,
            "issued_at": now,
            "expires_at": expires_at,
            "revoked_at": None
        }
        self.blocks.insert_one(block)
        block['expires_at'] = expires_at
        return block

    def revoke_application_block(self, block_id, revoked_by_id, revoked_by_name):
        block = self.blocks.find_one({"block_id": block_id, "revoked_at": None})
        if not block:
            return None
        now = datetime.now(self.mel_tz)
        self.blocks.update_one({"block_id": block_id}, {"$set": {"revoked_at": now, "revoked_by_id": revoked_by_id, "revoked_by_name": revoked_by_name}})
        block['revoked_at'] = now
        block['revoked_by_id'] = revoked_by_id
        block['revoked_by_name'] = revoked_by_name
        return block
