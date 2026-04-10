import os
from datetime import datetime, timezone

import aiohttp
from discord.ext import commands, tasks
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URI = os.getenv(
    "MONGO_URI",
    "mongodb://mongo:ToflcolbjYxOCwRJyIsyoqvIDBISAXgP@interchange.proxy.rlwy.net:32018",
)
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["sierra_applications"]
verifications_col = db["verifications"]
eaa_watchlist_col = db["eaa_watchlist"]
eaa_group_history_col = db["eaa_group_history"]

TRACKED_GROUP_INFO = {
    35798374: "Atlantis Testing",
    326076454: "Anomaly Actors",
    1059704936: "Executive Assurance Administration",
    996060743: "Files & Recordkeeping",
    884774428: "Office of Admissions",
    693222577: "Office of the Administrator",
    945711481: "Judicial Branch",
    34364967: "Internal Security Department",
    496209906: "Ethics Committee",
    647495903: "Engineering & Technical Services",
    454398709: "Department of External Relations",
    34365046: "Administrative Department",
    348257864: "Internal Tribunal Department",
    1032907325: "Manufacturing Department",
    727230617: "Emergency Medical Unit",
    34364928: "Medical Department",
    982198426: "Moderation",
    1022611648: "Triton-1 \"Bulls\"",
    854011952: "Military Police",
    34230571: "Scientific Department",
    34688572: "Mobile Task Forces",
    34688574: "Alpha-1",
    121792765: "Zeta-6 \"Masks\"",
    689805150: "Theta-9 \"Commandos\"",
    371785680: "Security Response Unit",
    34230495: "Security Department",
}
TRACKED_GROUPS = set(TRACKED_GROUP_INFO)


def get_group_link(group_id: int) -> str:
    return f"https://www.roblox.com/groups/{group_id}/group"


def get_group_name(group_id: int, api_name: str | None = None) -> str:
    name = (api_name or "").strip()
    if name:
        if "|" in name:
            cleaned = name.split("|", 1)[1].strip(" -")
            if cleaned:
                return cleaned
        if len(name) > 12:
            return name.strip(" -")

    mapped_name = TRACKED_GROUP_INFO.get(group_id)
    if mapped_name:
        return mapped_name

    if name:
        return name
    return f"Group {group_id}"


def get_rank_name(role_name: str | None = None, rank_id: int | None = None) -> str:
    try:
        parsed_rank = int(rank_id) if rank_id is not None else 0
    except (TypeError, ValueError):
        parsed_rank = 0

    name = (role_name or "").strip()
    lowered = name.lower()

    if name and lowered not in {"unknown", "unknown rank", "[unknown rank]"}:
        if lowered == "guest" and parsed_rank > 1:
            return f"Rank {parsed_rank}"
        return name

    if parsed_rank > 0:
        return f"Rank {parsed_rank}"
    return "Guest"


class EAATracker(commands.Cog):
    """Tracks tracked Roblox department joins and leaves for later EAA checks."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self.tracking_enabled = True

    async def cog_load(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))

        try:
            await eaa_watchlist_col.create_index("roblox_id", unique=True)
            await eaa_group_history_col.create_index(
                [("roblox_id", 1), ("group_id", 1), ("left_at", 1)]
            )
            await eaa_group_history_col.create_index("last_seen_at")
        except Exception as exc:
            if "OutOfDiskSpace" in str(exc):
                self.tracking_enabled = False
                print("[EAA Tracker] MongoDB is out of disk space; group history tracking is temporarily disabled.")
            else:
                print(f"[EAA Tracker] failed to create indexes: {exc}")

        if not self.track_group_membership.is_running():
            self.track_group_membership.start()

    async def cog_unload(self):
        if self.track_group_membership.is_running():
            self.track_group_membership.cancel()
        if self.session and not self.session.closed:
            await self.session.close()

    async def _fetch_json(self, url: str, **kwargs) -> dict:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))

        try:
            async with self.session.get(url, **kwargs) as resp:
                if resp.status == 200:
                    return await resp.json()
                return {}
        except Exception as exc:
            print(f"[EAA Tracker] request failed for {url}: {exc}")
            return {}

    async def _iter_tracked_users(self):
        seen_ids: set[int] = set()

        async for record in verifications_col.find({}, {"roblox": 1, "roblox_id": 1, "roblox_username": 1}):
            roblox = record.get("roblox", {})
            roblox_id = roblox.get("sub") or roblox.get("id") or record.get("roblox_id")
            roblox_username = (
                roblox.get("preferred_username")
                or roblox.get("username")
                or roblox.get("name")
                or record.get("roblox_username")
            )
            try:
                roblox_id = int(roblox_id)
            except (TypeError, ValueError):
                continue

            if roblox_id in seen_ids:
                continue

            seen_ids.add(roblox_id)
            yield roblox_id, roblox_username or f"Roblox User {roblox_id}"

        async for record in eaa_watchlist_col.find({}, {"roblox_id": 1, "roblox_username": 1}):
            try:
                roblox_id = int(record.get("roblox_id"))
            except (TypeError, ValueError):
                continue

            if roblox_id in seen_ids:
                continue

            seen_ids.add(roblox_id)
            yield roblox_id, record.get("roblox_username") or f"Roblox User {roblox_id}"

    async def _fetch_current_memberships(self, roblox_id: int) -> dict[int, dict]:
        data = await self._fetch_json(f"https://groups.roblox.com/v1/users/{roblox_id}/groups/roles")
        memberships: dict[int, dict] = {}

        for entry in data.get("data", []):
            group = entry.get("group", {})
            role = entry.get("role", {})
            group_id = group.get("id")
            if group_id not in TRACKED_GROUPS:
                continue

            group_id = int(group_id)
            memberships[group_id] = {
                "group_name": get_group_name(group_id, group.get("name")),
                "rank_id": role.get("rank"),
                "rank_name": get_rank_name(role.get("name"), role.get("rank")),
                "group_link": get_group_link(group_id),
            }

        return memberships

    @tasks.loop(minutes=30.0)
    async def track_group_membership(self):
        if not self.tracking_enabled or not self.bot.is_ready():
            return

        now = datetime.now(timezone.utc)

        async for roblox_id, roblox_username in self._iter_tracked_users():
            try:
                current_memberships = await self._fetch_current_memberships(roblox_id)
                open_records = {
                    doc["group_id"]: doc
                    async for doc in eaa_group_history_col.find({"roblox_id": roblox_id, "left_at": None})
                }

                for group_id, membership in current_memberships.items():
                    existing = open_records.get(group_id)
                    payload = {
                        "roblox_username": roblox_username,
                        "group_name": membership["group_name"],
                        "group_link": membership["group_link"],
                        "rank_id": membership["rank_id"],
                        "rank_name": membership["rank_name"],
                        "last_seen_at": now,
                    }

                    if existing:
                        await eaa_group_history_col.update_one({"_id": existing["_id"]}, {"$set": payload})
                    else:
                        await eaa_group_history_col.insert_one(
                            {
                                "roblox_id": roblox_id,
                                "roblox_username": roblox_username,
                                "group_id": group_id,
                                "group_name": membership["group_name"],
                                "group_link": membership["group_link"],
                                "rank_id": membership["rank_id"],
                                "rank_name": membership["rank_name"],
                                "joined_at": now,
                                "left_at": None,
                                "last_seen_at": now,
                            }
                        )

                for group_id, existing in open_records.items():
                    if group_id not in current_memberships:
                        await eaa_group_history_col.update_one(
                            {"_id": existing["_id"]},
                            {"$set": {"left_at": now, "last_seen_at": now}},
                        )
            except Exception as exc:
                if "OutOfDiskSpace" in str(exc):
                    self.tracking_enabled = False
                    print("[EAA Tracker] MongoDB is out of disk space; disabling group history tracking.")
                    return
                print(f"[EAA Tracker] error syncing {roblox_username} ({roblox_id}): {exc}")

    @track_group_membership.before_loop
    async def before_track(self):
        try:
            await self.bot.wait_until_ready()
        except RuntimeError:
            return


async def setup(bot: commands.Bot):
    await bot.add_cog(EAATracker(bot))
