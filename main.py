import os
import discord
from discord.ext import commands
from discord.ui import Button, View
import aiosqlite
from dotenv import load_dotenv
import asyncio

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=".", intents=intents)

DB = "tickets.db"

# ================= DATABASE =================
async def init_db():
    async with aiosqlite.connect(DB) as db:
        # Tickets table
        await db.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            channel_id INTEGER,
            status TEXT
        )
        """)
        # Settings table
        await db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            guild_id INTEGER PRIMARY KEY,
            logs_channel INTEGER,
            support_role INTEGER,
            ticket_limit INTEGER DEFAULT 50
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

async def get_guild_settings(guild_id):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT logs_channel, support_role, ticket_limit FROM settings WHERE guild_id=?", (guild_id,))
        row = await cur.fetchone()
        if not row:
            return None, None, 50
        return row  # logs_channel, support_role, ticket_limit

# ================= ADMIN COMMANDS =================
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

            # Check ticket limit
            cur = await db.execute("SELECT ticket_limit FROM settings WHERE guild_id=?", (interaction.guild.id,))
            row = await cur.fetchone()
            limit = row[0] if row else 50
            cur = await db.execute("SELECT COUNT(*) FROM tickets WHERE status='open'")
            total_open = (await cur.fetchone())[0]
            if total_open >= limit:
                return await interaction.response.send_message("❌ Ticket limit reached!", ephemeral=True)

            # Set channel permissions
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                self.user: discord.PermissionOverwrite(read_messages=True)
            }
            cur = await db.execute("SELECT support_role FROM settings WHERE guild_id=?", (interaction.guild.id,))
            row = await cur.fetchone()
            if row and row[0]:
                role = interaction.guild.get_role(row[0])
                if role:
                    overwrites[role] = discord.PermissionOverwrite(read_messages=True)

            channel = await interaction.guild.create_text_channel(f"ticket-{self.user.name}", overwrites=overwrites)

            await db.execute("INSERT INTO tickets (user_id, channel_id, status) VALUES (?, ?, ?)", (self.user.id, channel.id, "open"))
            await db.commit()

            await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)
            await channel.send(f"{self.user.mention} your ticket has been created.", view=CloseView(self.user))

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

# ================= SEND TICKET BUTTON =================
@bot.command()
@admin()
async def ticketmsg(ctx):
    view = TicketView(None)
    await ctx.send("🎫 Click below to create a ticket!", view=view)

# ================= READY =================
@bot.event
async def on_ready():
    await init_db()
    print(f"✅ {bot.user} is online!")

bot.run(TOKEN)
