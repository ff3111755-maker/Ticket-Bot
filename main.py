import os
import discord
from discord.ext import commands
from discord.ui import View, Button
import aiosqlite
import asyncio

TOKEN = os.getenv("DISCORD_TOKEN")  # Set in Railway or environment

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=".", intents=intents)

DB = "tickets.db"

# ================= DATABASE =================
async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            channel_id INTEGER,
            status TEXT
        )
        """)
        await db.execute("""
CREATE TABLE IF NOT EXISTS settings (
    guild_id INTEGER PRIMARY KEY,
    logs_channel INTEGER,
    support_role INTEGER,
    ticket_category INTEGER,
    ticket_limit INTEGER DEFAULT 50,
    panel_description TEXT DEFAULT 'Click the button below to open a ticket!',
    ticket_message TEXT DEFAULT '@User Your ticket has been created.'
)
""")

        await db.commit()

@bot.event
async def setup_hook():
    await init_db()

# ================= HELPERS =================
def admin():
    async def predicate(ctx):
        return ctx.author.guild_permissions.administrator
    return commands.check(predicate)

async def get_settings(guild_id):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("""
        SELECT logs_channel, support_role, ticket_category, ticket_limit, panel_description 
        FROM settings WHERE guild_id=?
        """, (guild_id,))
        row = await cur.fetchone()
        if row:
            return {
                "logs_channel": row[0],
                "support_role": row[1],
                "ticket_category": row[2],
                "ticket_limit": row[3],
                "panel_description": row[4]
            }
        return {
            "logs_channel": None,
            "support_role": None,
            "ticket_category": None,
            "ticket_limit": 50,
            "panel_description": "Click the button below to open a ticket!"
        }

# ================= ADMIN COMMANDS =================
@bot.command()
@admin()
async def tktmsg(ctx, *, message: str):
    """Set custom ticket creation message"""
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (guild_id, ticket_message) VALUES (?, ?)",
            (ctx.guild.id, message)
        )
        await db.commit()
    await ctx.send("✅ Custom ticket creation message updated!")
@bot.command()
@admin()
async def setlogs(ctx, channel: discord.TextChannel):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR REPLACE INTO settings (guild_id, logs_channel) VALUES (?, ?)", (ctx.guild.id, channel.id))
        await db.commit()
    await ctx.send(f"✅ Logs channel set to {channel.mention}")

@bot.command()
@admin()
async def setrole(ctx, role: discord.Role):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR REPLACE INTO settings (guild_id, support_role) VALUES (?, ?)", (ctx.guild.id, role.id))
        await db.commit()
    await ctx.send(f"✅ Support role set to {role.name}")

@bot.command()
@admin()
async def setcategory(ctx, category: discord.CategoryChannel):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR REPLACE INTO settings (guild_id, ticket_category) VALUES (?, ?)", (ctx.guild.id, category.id))
        await db.commit()
    await ctx.send(f"✅ Ticket category set to {category.name}")

@bot.command()
@admin()
async def setticketdesc(ctx, *, description: str):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR REPLACE INTO settings (guild_id, panel_description) VALUES (?, ?)", (ctx.guild.id, description))
        await db.commit()
    await ctx.send(f"✅ Ticket panel description updated.")

@bot.command()
@admin()
async def wipe(ctx):
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM tickets")
        await db.commit()
    await ctx.send("🗑 All tickets wiped!")

# ================= TICKET BUTTON VIEWS =================
class TicketView(View):
    def __init__(self, user):
        super().__init__(timeout=None)
        self.user = user

    @discord.ui.button(label="Create Ticket 🎫", style=discord.ButtonStyle.green)
    async def create(self, interaction: discord.Interaction, button: Button):
        async with aiosqlite.connect(DB) as db:
            # Check if user already has open ticket
            cur = await db.execute("SELECT COUNT(*) FROM tickets WHERE user_id=? AND status='open'", (self.user.id,))
            count = (await cur.fetchone())[0]
            if count > 0:
                return await interaction.response.send_message("❌ You already have an open ticket!", ephemeral=True)

            # Get guild settings
            settings = await get_settings(interaction.guild.id)

            # Check ticket limit
            cur = await db.execute("SELECT COUNT(*) FROM tickets WHERE status='open'")
            total_open = (await cur.fetchone())[0]
            if total_open >= settings["ticket_limit"]:
                return await interaction.response.send_message("❌ Ticket limit reached!", ephemeral=True)

            # Channel permissions
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                self.user: discord.PermissionOverwrite(read_messages=True)
            }

            if settings["support_role"]:
                role = interaction.guild.get_role(settings["support_role"])
                if role:
                    overwrites[role] = discord.PermissionOverwrite(read_messages=True)

            # Ticket category
            category = interaction.guild.get_channel(settings["ticket_category"]) if settings["ticket_category"] else None

            channel = await interaction.guild.create_text_channel(
                f"ticket-{self.user.name}",
                overwrites=overwrites,
                category=category
            )

            # Insert ticket into DB
            await db.execute("INSERT INTO tickets (user_id, channel_id, status) VALUES (?, ?, ?)", (self.user.id, channel.id, "open"))
            await db.commit()

            # Send panel messages
            await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)
            # Get custom ticket message
cur = await db.execute(
    "SELECT ticket_message FROM settings WHERE guild_id=?",
    (interaction.guild.id,)
)
row = await cur.fetchone()
ticket_msg = row[0] if row and row[0] else "@User Your ticket has been created."

# Replace placeholders
ticket_msg = ticket_msg.replace("@User", self.user.mention)

if settings["support_role"]:
    role = interaction.guild.get_role(settings["support_role"])
    if role:
        ticket_msg = ticket_msg.replace("@SupportRole", role.mention)

# Send message in ticket channel
await channel.send(ticket_msg, view=CloseView(self.user))
            # Send to logs channel
            if settings["logs_channel"]:
                log_channel = interaction.guild.get_channel(settings["logs_channel"])
                if log_channel:
                    await log_channel.send(f"📌 Ticket created by {self.user.mention} → {channel.mention}")

class CloseView(View):
    def __init__(self, user):
        super().__init__(timeout=None)
        self.user = user

    @discord.ui.button(label="Close Ticket ❌", style=discord.ButtonStyle.red)
    async def close(self, interaction: discord.Interaction, button: Button):
        async with aiosqlite.connect(DB) as db:
            cur = await db.execute("SELECT status FROM tickets WHERE channel_id=?", (interaction.channel.id,))
            row = await cur.fetchone()
            if not row or row[0] != 'open':
                return await interaction.response.send_message("❌ Ticket already closed or not found", ephemeral=True)

            await db.execute("UPDATE tickets SET status='closed' WHERE channel_id=?", (interaction.channel.id,))
            await db.commit()

        await interaction.response.send_message("✅ Ticket closed! Archiving...")
        await asyncio.sleep(2)
        await interaction.channel.delete()

        # Log closure
        settings = await get_settings(interaction.guild.id)
        if settings["logs_channel"]:
            log_channel = interaction.guild.get_channel(settings["logs_channel"])
            if log_channel:
                await log_channel.send(f"📌 Ticket closed → {interaction.channel.name}")

# ================= PANEL COMMAND =================
@bot.command()
@admin()
async def ticketpanel(ctx):
    settings = await get_settings(ctx.guild.id)
    embed = discord.Embed(
        title="🎫 Support Ticket Panel",
        description=settings["panel_description"],
        color=discord.Color.green()
    )
    view = TicketView(ctx.author)
    await ctx.send(embed=embed, view=view)

# ================= READY =================
@bot.event
async def on_ready():
    await init_db()
    print(f"✅ {bot.user} is online!")

bot.run(TOKEN)
