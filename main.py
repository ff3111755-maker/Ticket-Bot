import os
import discord
import aiosqlite
import asyncio
import chat_exporter
from discord.ext import commands
from discord.ui import View, Button
from datetime import datetime

TOKEN = os.getenv("DISCORD_TOKEN")
DB = "tickets.db"

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=".", intents=intents)

# ================= DATABASE =================
async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
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
        SELECT logs_channel, support_role, ticket_category, ticket_limit, panel_description, ticket_message
        FROM settings WHERE guild_id=?
        """, (guild_id,))
        row = await cur.fetchone()

    if row:
        return {
            "logs_channel": row[0],
            "support_role": row[1],
            "ticket_category": row[2],
            "ticket_limit": row[3],
            "panel_description": row[4],
            "ticket_message": row[5]
        }

    return {
        "logs_channel": None,
        "support_role": None,
        "ticket_category": None,
        "ticket_limit": 50,
        "panel_description": "Click the button below to open a ticket!",
        "ticket_message": "@User Your ticket has been created."
    }

# ================= ADMIN COMMANDS =================
@bot.command()
@admin()
async def setlogs(ctx, channel: discord.TextChannel):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR REPLACE INTO settings (guild_id, logs_channel) VALUES (?,?)",
                         (ctx.guild.id, channel.id))
        await db.commit()
    await ctx.send("✅ Logs channel set")

@bot.command()
@admin()
async def setrole(ctx, role: discord.Role):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR REPLACE INTO settings (guild_id, support_role) VALUES (?,?)",
                         (ctx.guild.id, role.id))
        await db.commit()
    await ctx.send("✅ Support role set")

@bot.command()
@admin()
async def setcategory(ctx, category: discord.CategoryChannel):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR REPLACE INTO settings (guild_id, ticket_category) VALUES (?,?)",
                         (ctx.guild.id, category.id))
        await db.commit()
    await ctx.send("✅ Ticket category set")

@bot.command()
@admin()
async def setticketdesc(ctx, *, text):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR REPLACE INTO settings (guild_id, panel_description) VALUES (?,?)",
                         (ctx.guild.id, text))
        await db.commit()
    await ctx.send("✅ Panel description updated")

@bot.command()
@admin()
async def tktmsg(ctx, *, text):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR REPLACE INTO settings (guild_id, ticket_message) VALUES (?,?)",
                         (ctx.guild.id, text))
        await db.commit()
    await ctx.send("✅ Ticket message updated")

@bot.command()
@admin()
async def ticketpanel(ctx):
    settings = await get_settings(ctx.guild.id)
    embed = discord.Embed(
        title="🎫 Support Ticket Panel",
        description=settings["panel_description"],
        color=discord.Color.green()
    )
    await ctx.send(embed=embed, view=TicketView(ctx.author))

# ================= TICKET VIEW =================
class TicketView(View):
    def __init__(self, user):
        super().__init__(timeout=None)
        self.user = user

    @discord.ui.button(label="Create Ticket 🎫", style=discord.ButtonStyle.green)
    async def create(self, interaction: discord.Interaction, button: Button):
        settings = await get_settings(interaction.guild.id)

        async with aiosqlite.connect(DB) as db:
            cur = await db.execute("SELECT COUNT(*) FROM tickets WHERE user_id=? AND status='open'", (interaction.user.id,))
            if (await cur.fetchone())[0] > 0:
                return await interaction.response.send_message("❌ You already have an open ticket", ephemeral=True)

            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True)
            }

            if settings["support_role"]:
                role = interaction.guild.get_role(settings["support_role"])
                if role:
                    overwrites[role] = discord.PermissionOverwrite(read_messages=True)

            category = interaction.guild.get_channel(settings["ticket_category"])
            channel = await interaction.guild.create_text_channel(
                f"ticket-{interaction.user.name}",
                overwrites=overwrites,
                category=category
            )

            await db.execute("INSERT INTO tickets VALUES (?,?,?)",
                             (interaction.user.id, channel.id, "open"))
            await db.commit()

        msg = settings["ticket_message"]
        msg = msg.replace("@User", interaction.user.mention)
        if settings["support_role"]:
            role = interaction.guild.get_role(settings["support_role"])
            if role:
                msg = msg.replace("@SupportRole", role.mention)

        await channel.send(msg, view=CloseView())
        await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)

# ================= CLOSE + TRANSCRIPT =================
class CloseView(View):
    @discord.ui.button(label="Close Ticket ❌", style=discord.ButtonStyle.red)
    async def close(self, interaction: discord.Interaction, button: Button):
        settings = await get_settings(interaction.guild.id)

        transcript = await chat_exporter.export(
            interaction.channel,
            limit=None,
            tz_info="UTC",
            guild=interaction.guild
        )

        filename = f"transcript-{interaction.channel.id}.html"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(transcript)

        if settings["logs_channel"]:
            log = interaction.guild.get_channel(settings["logs_channel"])
            if log:
                await log.send(
                    content=f"📄 Transcript for **{interaction.channel.name}**",
                    file=discord.File(filename)
                )

        await interaction.response.send_message("🔒 Closing ticket...")
        await asyncio.sleep(2)
        await interaction.channel.delete()

# ================= READY =================
@bot.event
async def on_ready():
    await init_db()
    print(f"✅ {bot.user} online")

bot.run(TOKEN)
