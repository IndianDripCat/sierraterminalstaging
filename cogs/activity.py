import discord
from discord.ext import commands, tasks
from discord import app_commands, Interaction, Embed
import aiohttp
import os
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime

# MongoDB collections for activity tracking
mongo_client = AsyncIOMotorClient(os.getenv("MONGO_URI", "mongodb://mongo:ToflcolbjYxOCwRJyIsyoqvIDBISAXgP@interchange.proxy.rlwy.net:32018"))
activity_users_col = mongo_client['sierra_applications']['activity_users']
# structure: {"roblox_id": ..., "roblox_username": ..., "team": ...}
activity_logs_col  = mongo_client['sierra_applications']['activity_logs']
# log entries will include team and whatever the presence API returns

class Activity(commands.Cog):
    """Track Roblox presence for a set of users and expose commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self.activity_logging_enabled = True

    async def cog_load(self):
        # called when the cog is loaded by Discord
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()

        # ensure indexes exist for fast lookups and optional TTL
        try:
            await activity_users_col.create_index("roblox_id", unique=True)
            await activity_logs_col.create_index("roblox_id")
            # keep logs for 30 days (optional); comment out if you want infinite history
            await activity_logs_col.create_index("timestamp", expireAfterSeconds=60 * 60 * 24 * 30)
        except Exception as exc:
            if "OutOfDiskSpace" in str(exc):
                self.activity_logging_enabled = False
                print("[Activity] MongoDB is out of disk space; activity logging is temporarily disabled.")
            else:
                print(f"[Activity] failed to create indexes: {exc}")

        if not self.poll_presence.is_running():
            self.poll_presence.start()

    async def cog_unload(self):
        # cleanup
        if self.poll_presence.is_running():
            self.poll_presence.cancel()
        if self.session and not self.session.closed:
            await self.session.close()

    @tasks.loop(seconds=60.0)
    async def poll_presence(self):
        """Periodic task that queries Roblox for the presence of tracked users."""
        if not self.activity_logging_enabled or not self.bot.is_ready():
            return

        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()

        # collect all Roblox IDs we're tracking along with their team
        cursor = activity_users_col.find({}, {"roblox_id": 1, "team": 1})
        teams = {}
        ids = []
        async for doc in cursor:
            rid = doc.get("roblox_id")
            if rid is not None:
                ids.append(rid)
                teams[rid] = doc.get("team")
        if not ids:
            return

        url = "https://presence.roblox.com/v1/presence/users"
        payload = {"userIds": ids}
        try:
            async with self.session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for entry in data.get("userPresences", []):
                        rid = entry.get("userId")
                        record = {
                            "roblox_id": rid,
                            "team": teams.get(rid),
                            "timestamp": datetime.utcnow(),
                            **entry,
                        }
                        await activity_logs_col.insert_one(record)
        except Exception as exc:
            if "OutOfDiskSpace" in str(exc):
                self.activity_logging_enabled = False
                print("[Activity] MongoDB is out of disk space; skipping further activity log writes until restart.")
            else:
                print(f"[Activity] error polling presence: {exc}")

    @poll_presence.before_loop
    async def before_poll(self):
        try:
            await self.bot.wait_until_ready()
        except RuntimeError:
            # Startup was interrupted before the client finished initialising.
            return

    activity = app_commands.Group(name="activity", description="Roblox activity commands")

    async def _resolve_user(self, interaction: Interaction, username: str | None):
        """Return (roblox_id, roblox_username) or (None, None) on failure."""
        if username:
            verification = interaction.client.get_cog('Verification')
            if verification is None:
                return None, None
            rid = await verification.get_roblox_user_id(username)
            if rid:
                return rid, username
            else:
                return None, None
        # on view command without explicit username we handle lookup inline
        return None, None



    @activity.command(name="view")
    @app_commands.describe(team="Identifier for the team", roblox_username="Roblox username to view (optional)")
    async def activity_view(self, interaction: Interaction, team: str, roblox_username: str | None = None):
        """Show recent presence entries for a user on a given team.

        The username argument is optional; if omitted the command will look up the
        caller's linked account and require that it is tracked for the team.
        """
        # resolve the target user
        if roblox_username:
            rid, uname = await self._resolve_user(interaction, roblox_username)
            if not rid:
                await interaction.response.send_message("Could not look up that username.", ephemeral=True)
                return
        else:
            # attempt to use caller's linked verification record
            db = activity_users_col.database
            vrec = await db['verifications'].find_one({"discord_id": interaction.user.id})
            if not vrec:
                await interaction.response.send_message("You have not linked a Roblox account.", ephemeral=True)
                return
            rid = vrec["roblox_id"]
            uname = vrec["roblox_username"]
            # ensure the user is actually tracked for this team
            exists = await activity_users_col.find_one({"roblox_id": rid, "team": team})
            if not exists:
                await interaction.response.send_message("Your account is not tracked for that team.", ephemeral=True)
                return

        cursor = activity_logs_col.find({"roblox_id": rid, "team": team}).sort("timestamp", -1).limit(5)
        entries = [e async for e in cursor]
        if not entries:
            await interaction.response.send_message(f"No activity recorded for **{uname}** on team **{team}**.")
            return
        description = []
        for e in entries:
            ts = e["timestamp"].strftime("%Y-%m-%d %H:%M UTC")
            status = e.get("userPresenceType", "unknown")
            place = e.get("placeId")
            description.append(f"{ts} – {status}" + (f" (place {place})" if place else ""))
        embed = Embed(title=f"Activity for {uname} (team {team})",
                      description="\n".join(description),
                      color=discord.Color.dark_blue())
        await interaction.response.send_message(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(Activity(bot))
