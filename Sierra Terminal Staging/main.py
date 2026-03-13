import discord
from discord.ext import commands
from discord import ui, ButtonStyle, Embed, Colour, Interaction, SelectOption
import os
from dotenv import load_dotenv
import random
import string
import motor.motor_asyncio
import os
from datetime import datetime
import asyncio

# Utility to generate the embed footer with current date/time
def get_footer():
    now = datetime.now()
    formatted = now.strftime('Today at %I:%M %p').lstrip('0').replace(' 0', ' ')
    return f"Terminal • {formatted}"
from datetime import datetime

# --- CONFIG ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
GUILD_ID = 1397835682621423691
APPLICATION_CHANNEL_ID = 1461241340737228903
REVIEW_CHANNELS = {
    'sc0': 1461246189012779090,
    'sc1': 1461246203344851190,
    'sd': 1461246233908613234,
    'md': 1461246248949518458,
    'scd': 1461246261561655327
}
NOTIFY_CHANNEL_ID = 1461248905017692251

ROLE_IDS = {
    'sc0': 1461241834499215524,
    'sc1': 1461241794036633742,
    'sd': 1461241807496155310,
    'scd': 1461242523086491764,
    'md': 1461242546536579143,
    'extra1': 1461242559711150234,
    'extra2': 1461243381945729192
}

APPLICATIONS = {
    'sc0': {
        'name': 'Security Class 0',
        'role': ROLE_IDS['sc0'],
        'questions': ["yes", "yes yes", "yes no yes"]
    },
    'sc1': {
        'name': 'Security Class 1',
        'role': ROLE_IDS['sc1'],
        'questions': ["why", "no", "why yes plz", "shh"]
    },
    'sd': {
        'name': 'Security Department',
        'role': [ROLE_IDS['sd'], ROLE_IDS['scd'], ROLE_IDS['md'], ROLE_IDS['extra1'], ROLE_IDS['extra2']],
        'questions': ["but", "whyy", "sure"]
    },
    'md': {
        'name': 'Medical Department',
        'role': [ROLE_IDS['sd'], ROLE_IDS['scd'], ROLE_IDS['md'], ROLE_IDS['extra1'], ROLE_IDS['extra2']],
        'questions': ["surely", "why not", "xxxx"]
    },
    'scd': {
        'name': 'Scientific Department',
        'role': [ROLE_IDS['sd'], ROLE_IDS['scd'], ROLE_IDS['md'], ROLE_IDS['extra1'], ROLE_IDS['extra2']],
        'questions': ["tb", "fs", "sgsgh"]
    }
}

# --- MONGODB ---
mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = mongo_client['sierra_applications']
applications_col = db['applications']

# --- BOT SETUP ---
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

# --- UTILS ---
def generate_app_id():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=26))

def get_application_key_by_name(name):
    for k, v in APPLICATIONS.items():
        if v['name'] == name:
            return k
    return None

def has_any_role(member, role_ids):
    def get_footer():
        now = datetime.now()
        formatted = now.strftime('Today at %I:%M %p').lstrip('0').replace(' 0', ' ')
        return f"Terminal • {formatted}"
    if isinstance(role_ids, int):
        role_ids = [role_ids]
    return any(role.id in role_ids for role in member.roles)

# --- VIEWS & COMPONENTS ---
class ApplicationPortalView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            ui.Button(
                label="📝 Create New Application",
                style=ButtonStyle.success,
                custom_id="create_application"
            )
        )

class ApplicationTypeSelect(ui.Select):
    def __init__(self, member: discord.Member):
        options = [
            SelectOption(label="Security Class 0", description="Apply for Security Class 0", value="sc0"),
            SelectOption(label="Security Class 1", description="Apply for Security Class 1", value="sc1"),
            SelectOption(label="Security Department", description="Apply for the Security Department", value="sd"),
            SelectOption(label="Scientific Department", description="Apply for the Scientific Department", value="scd"),
            SelectOption(label="Medical Department", description="Apply for the Medical Department", value="md"),
        ]
        super().__init__(placeholder="Choose an application..", min_values=1, max_values=1, options=options, custom_id="application_type_select")
        self.member = member

    async def callback(self, interaction: Interaction):
        app_key = self.values[0]
        app = APPLICATIONS[app_key]
        # Permission check
        if app_key in ['sc0', 'sc1']:
            if not has_any_role(self.member, app['role']):
                embed = Embed(title="🤚 Insufficient Permissions", description=f"in order to apply for this application, you require the <@&{app['role']}>", color=Colour.red())
                embed.set_footer(text=get_footer())
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
        else:
            # Departmental applications
            if not has_any_role(self.member, app['role']):
                embed = Embed(title="🤚 No Access", description="You do not have access to apply for this department.", color=Colour.red())
                embed.add_field(name="✅ Permissions", value="> - **Security Class 1** or above\n> - **Systems Operator**")
                embed.set_footer(text=get_footer())
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
        # Passed permission check, start DM flow
        await start_application_dm(interaction.user, app_key, interaction)

class ApplicationTypeView(ui.View):
    def __init__(self, member):
        super().__init__(timeout=120)
        self.add_item(ApplicationTypeSelect(member))

# --- DM FLOW ---
async def start_application_dm(user: discord.User, app_key: str, interaction: Interaction):
    app = APPLICATIONS[app_key]
    try:
        dm = await user.create_dm()
        # Rules embed
        embed = Embed(
            title=f"{app['name']} Application",
            description="You are about to apply for a role/department within SRI - Sierra 7. Please agree to the rules below:",
            color=Colour.dark_blue()
        )
        embed.add_field(
            name="✍️ Rules",
            value="> - Troll applications will be voided and may be punished at the discretion of the application reviewer or departmental command.\n> - Use of artificial intelligence is punishable and can warrant a blacklist from holding a security clearance or joining a department.\n> - Upon agreeance to the aforementioned rules, please click the green \"✅ Begin\" button below."
        )
        embed.set_footer(text=get_footer())
        view = BeginApplicationView(app_key)
        await dm.send(embed=embed, view=view)
        await interaction.response.send_message(content="Check your DMs to begin your application!", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(content="Unable to DM you. Please enable DMs and try again.", ephemeral=True)

class BeginApplicationView(ui.View):
    def __init__(self, app_key):
        super().__init__(timeout=120)
        self.app_key = app_key
        self.add_item(
            ui.Button(label="✅ Begin", style=ButtonStyle.success, custom_id=f"begin_{app_key}")
        )

# --- QUESTION FLOW ---
user_applications = {}  # user_id: {app_key, answers, current_q}

class ConfirmApplicationView(ui.View):
    def __init__(self, app_key, answers):
        super().__init__(timeout=120)
        self.app_key = app_key
        self.answers = answers
        self.add_item(
            ui.Button(label="✅ Confirm", style=ButtonStyle.success, custom_id=f"confirm_{app_key}")
        )

async def send_question(dm, app_key, q_idx, answers):
    app = APPLICATIONS[app_key]
    embed = Embed(
        title=f"{app['name']} Application",
        description=f"{q_idx+1}/{len(app['questions'])}: {app['questions'][q_idx]}",
        color=Colour.dark_blue()
    )
    embed.set_footer(text=get_footer())
    await dm.send(embed=embed)

# --- BOT EVENTS & HANDLERS ---
@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    print(f'Bot is in {len(bot.guilds)} server(s)')
    guild = bot.get_guild(GUILD_ID)
    if guild:
        channel = guild.get_channel(APPLICATION_CHANNEL_ID)
        if channel:
            # Try to find an existing portal embed
            async for message in channel.history(limit=20):
                if message.author == bot.user and message.embeds:
                    embed = message.embeds[0]
                    if embed.title and embed.title.lower() == "application portal":
                        # Edit to restore button
                        view = ApplicationPortalView()
                        await message.edit(embed=embed, view=view)
                        print("Restored application portal embed.")
                        break
            else:
                # Not found, send new
                embed = Embed(
                    title="application portal",
                    description="click below and apply for something",
                    color=Colour.dark_blue()
                )
                view = ApplicationPortalView()
                await channel.send(embed=embed, view=view)
                print("Sent new application portal embed.")
        else:
            print(f"Channel {APPLICATION_CHANNEL_ID} not found.")
    else:
        print(f"Guild {GUILD_ID} not found.")

@bot.event
async def on_interaction(interaction: Interaction):
    if not interaction.type == discord.InteractionType.component:
        return
    custom_id = interaction.data.get('custom_id')
    user = interaction.user
    member = interaction.guild.get_member(user.id) if interaction.guild else None
    # Create Application Button
    if custom_id == "create_application":
        # Check if user is appblocked
        try:
            from mongo_db import MongoDB
            db = MongoDB()
        except ImportError:
            db = None
        is_blocked = False
        if db:
            try:
                is_blocked = db.is_user_blocked(interaction.user.id)
            except Exception:
                is_blocked = False
        if is_blocked:
            embed = Embed(
                title="⛔ Application Blocked",
                description="You are currently blocked from submitting applications! Please contact an application reviewer.",
                color=Colour.red()
            )
            embed.set_footer(text=get_footer())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed = Embed(title="❗ Choose an application", description="Please choose an application below.", color=Colour.dark_blue())
        embed.set_footer(text=get_footer())
        view = ApplicationTypeView(member)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return
    # Begin Application Button
    if custom_id.startswith("begin_"):
        app_key = custom_id.split("_", 1)[1]
        user_applications[user.id] = {"app_key": app_key, "answers": [None]*len(APPLICATIONS[app_key]['questions']), "current_q": 0}
        dm = await user.create_dm()
        await send_question(dm, app_key, 0, user_applications[user.id]['answers'])
        return
    # Confirm Application Button
    if custom_id.startswith("confirm_"):
        app_key = custom_id.split("_", 1)[1]
        app_data = user_applications.get(user.id)
        if not app_data:
            return
        # Submitting...
        dm = await user.create_dm()
        embed = Embed(title="Submitting...", description="This will only take a few moments...", color=Colour.dark_blue())
        embed.set_footer(text=get_footer())
        msg = await dm.send(embed=embed)
        # Store in DB
        app_id = generate_app_id()
        answers = app_data['answers']
        await applications_col.insert_one({
            "app_id": app_id,
            "user_id": user.id,
            "app_key": app_key,
            "answers": answers,
            "status": "Pending"
        })
        # Send to review channel
        review_channel_id = REVIEW_CHANNELS[app_key]
        guild = bot.get_guild(GUILD_ID)
        review_channel = guild.get_channel(review_channel_id)
        review_embed = Embed(
            title=f"{APPLICATIONS[app_key]['name']} Application | {user}",
            description=f"**Application ID:** `{app_id}`\n**Application Status:** `Pending`",
            color=Colour.dark_blue()
        )
        # Footer: list all questions
        questions = APPLICATIONS[app_key]['questions']
        for idx, q in enumerate(questions):
            review_embed.add_field(name=q, value=answers[idx] or "(no answer)", inline=False)
        review_view = ReviewApplicationView(app_id, user.id, app_key)
        await review_channel.send(embed=review_embed, view=review_view)
        # Notify user
        submitted_embed = Embed(title="Submitted!", description="Your application has been succesfully submitted!", color=Colour.green())
        submitted_embed.set_footer(text=get_footer())
        await msg.edit(embed=submitted_embed)
        # Notify log channel
        # Removed 'Application Submitted' embed from notification channel
        return
    # Accept/Deny Buttons
    if custom_id.startswith("accept_") or custom_id.startswith("deny_"):
        # Handled in ReviewApplicationView
        pass

# --- REVIEW VIEW ---
class ReviewApplicationView(ui.View):
    def __init__(self, app_id, user_id, app_key):
        super().__init__(timeout=None)
        self.app_id = app_id
        self.user_id = user_id
        self.app_key = app_key
        self.add_item(ui.Button(label="✅ Accept", style=ButtonStyle.success, custom_id=f"accept_{app_id}"))
        self.add_item(ui.Button(label="❌ Deny", style=ButtonStyle.danger, custom_id=f"deny_{app_id}"))
    async def interaction_check(self, interaction: Interaction) -> bool:
        return True
    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
    async def handle_accept(self, interaction: Interaction):
        await applications_col.update_one({"app_id": self.app_id}, {"$set": {"status": "Accepted"}})
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)
        user = await bot.fetch_user(self.user_id)
        embed = Embed(title=f"Application Accepted | {user}", description=f"Your {APPLICATIONS[self.app_key]['name']} application has been **accepted.**", color=Colour.green())
        embed.set_footer(text=get_footer())
        await user.send(embed=embed)
        guild = bot.get_guild(GUILD_ID)
        notify_channel = guild.get_channel(NOTIFY_CHANNEL_ID)
        await notify_channel.send(f"<@{user.id}>", embed=embed)
        await interaction.response.send_message("Application accepted.", ephemeral=True)
    async def handle_deny(self, interaction: Interaction):
        class DenyReasonModal(ui.Modal, title="Deny Reason"):
            reason = ui.TextInput(label="Why did you deny this application?", style=discord.TextStyle.paragraph)
            async def on_submit(self, modal_interaction: Interaction):
                await applications_col.update_one({"app_id": self.app_id}, {"$set": {"status": "Denied", "deny_reason": self.reason.value}})
                for item in self.children:
                    item.disabled = True
                review_embed = interaction.message.embeds[0].copy()
                review_embed.description += f"\n**Reason:**\n> {self.reason.value}"
                await interaction.message.edit(embed=review_embed, view=self)
                user = await bot.fetch_user(self.user_id)
                embed = Embed(title=f"Application Rejected | {user}", description=f"Your {APPLICATIONS[self.app_key]['name']} application has been **rejected.**", color=Colour.red())
                embed.set_footer(text=get_footer())
                await user.send(embed=embed)
                guild = bot.get_guild(GUILD_ID)
                notify_channel = guild.get_channel(NOTIFY_CHANNEL_ID)
                embed2 = Embed(title=f"Application Rejected | {user}", description=f"Your {APPLICATIONS[self.app_key]['name']} application has been **rejected.**", color=Colour.red())
                embed2.add_field(name="✍️ Reason", value=self.reason.value)
                embed2.set_footer(text=get_footer())
                await notify_channel.send(f"<@{user.id}>", embed=embed2)
                await modal_interaction.response.send_message("Application denied.", ephemeral=True)
        await interaction.response.send_modal(DenyReasonModal())

# --- MESSAGE HANDLING FOR DM ANSWERS ---
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if isinstance(message.channel, discord.DMChannel):
        user_id = message.author.id
        app_data = user_applications.get(user_id)
        if not app_data:
            return
        app_key = app_data['app_key']
        q_idx = app_data['current_q']
        app = APPLICATIONS[app_key]
        if q_idx < len(app['questions']):
            app_data['answers'][q_idx] = message.content
            # Wait for confirm or next question
            if q_idx + 1 < len(app['questions']):
                app_data['current_q'] += 1
                await send_question(message.channel, app_key, app_data['current_q'], app_data['answers'])
            else:
                # All questions answered
                view = ConfirmApplicationView(app_key, app_data['answers'])
                embed = Embed(title=f"{app['name']} Application", description="You are about to conclude your application. Please click \"✅ Confirm\" below when you have checked on all of your answers and ensured that they are correct.", color=Colour.dark_blue())
                embed.set_footer(text=get_footer())
                await message.channel.send(embed=embed, view=view)
    await bot.process_commands(message)

# --- COGS ---
async def load_cogs():
    await bot.load_extension("cogs.appblock")
    await bot.load_extension("cogs.verification")
    await bot.load_extension("cogs.roles")
    await bot.load_extension("cogs.utilities")
    # new activity tracking cog
    await bot.load_extension("cogs.activity")

async def main():
    await load_cogs()
    await bot.start(BOT_TOKEN)

# --- SYNC COMMAND ---
@bot.command(name="sync")
@commands.is_owner()
async def sync_commands(ctx):
    """Sync all application commands and reload all cogs."""
    # Reload all cogs in the cogs directory
    import os
    import importlib
    cogs_dir = os.path.join(os.path.dirname(__file__), "cogs")
    for filename in os.listdir(cogs_dir):
        if filename.endswith(".py") and not filename.startswith("__"):
            ext = f"cogs.{filename[:-3]}"
            try:
                await bot.reload_extension(ext)
            except commands.ExtensionNotLoaded:
                await bot.load_extension(ext)
    # Sync commands
    synced = await bot.tree.sync()
    await ctx.send(f"Synced {len(synced)} commands and reloaded all cogs.")

if __name__ == "__main__":
    asyncio.run(main())
