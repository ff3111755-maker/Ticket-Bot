import os
import discord
import aiosqlite
import asyncio
from discord.ext import commands
from discord.ui import View, Button

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
            channel_id INTEGER
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            guild_id INTEGER PRIMARY KEY,
            logs_channel INTEGER,
            support_role INTEGER,
            ticket_category INTEGER,
            panel_description TEXT,
            ticket_message TEXT
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
        cur = await db.execute(
            "SELECT logs_channel, support_role, ticket_category, panel_description, ticket_message FROM settings WHERE guild_id=?",
            (guild_id,)
        )
        row = await cur.fetchone()

    return {
        "logs": row[0] if row else None,
        "role": row[1] if row else None,
        "category": row[2] if row else None,
        "panel_desc": row[3] if row else "Click the button below to open a ticket.",
        "ticket_msg": row[4] if row else "@User your ticket has been created."
    }

# ================= ADMIN COMMANDS =================
@bot.command()
@admin()
async def setlogs(ctx, channel: discord.TextChannel):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (guild_id, logs_channel) VALUES (?,?)",
            (ctx.guild.id, channel.id)
        )
        await db.commit()
    await ctx.send("✅ Logs channel set")

@bot.command()
@admin()
async def setrole(ctx, role: discord.Role):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (guild_id, support_role) VALUES (?,?)",
            (ctx.guild.id, role.id)
        )
        await db.commit()
    await ctx.send("✅ Support role set")

@bot.command()
@admin()
async def setcategory(ctx, category: discord.CategoryChannel):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (guild_id, ticket_category) VALUES (?,?)",
            (ctx.guild.id, category.id)
        )
        await db.commit()
    await ctx.send("✅ Ticket category set")

@bot.command()
@admin()
async def setticketdesc(ctx, *, text):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (guild_id, panel_description) VALUES (?,?)",
            (ctx.guild.id, text)
        )
        await db.commit()
    await ctx.send("✅ Panel description updated")

@bot.command()
@admin()
async def tktmsg(ctx, *, text):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (guild_id, ticket_message) VALUES (?,?)",
            (ctx.guild.id, text)
        )
        await db.commit()
    await ctx.send("✅ Ticket creation message updated")

# ================= VIEWS =================
class CloseView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket ❌", style=discord.ButtonStyle.red)
    async def close(self, interaction: discord.Interaction, button: Button):
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "DELETE FROM tickets WHERE channel_id=?",
                (interaction.channel.id,)
            )
            await db.commit()

        await interaction.response.send_message("🔒 Closing ticket...")
        await asyncio.sleep(2)
        await interaction.channel.delete()

        settings = await get_settings(interaction.guild.id)
        if settings["logs"]:
            log = interaction.guild.get_channel(settings["logs"])
            if log:
                await log.send(f"📌 Ticket closed → `{interaction.channel.name}`")

class TicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket 🎫", style=discord.ButtonStyle.green)
    async def create(self, interaction: discord.Interaction, button: Button):
        async with aiosqlite.connect(DB) as db:
            # Remove broken tickets
            await db.execute(
                "DELETE FROM tickets WHERE user_id=? AND channel_id NOT IN "
                "(SELECT id FROM sqlite_master)",
                (interaction.user.id,)
            )
            await db.commit()

            cur = await db.execute(
                "SELECT COUNT(*) FROM tickets WHERE user_id=?",
                (interaction.user.id,)
            )
            if (await cur.fetchone())[0] > 0:
                return await interaction.response.send_message(
                    "❌ You already have an open ticket.",
                    ephemeral=True
                )

        settings = await get_settings(interaction.guild.id)

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True)
        }

        if settings["role"]:
            role = interaction.guild.get_role(settings["role"])
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True)

        category = interaction.guild.get_channel(settings["category"]) if settings["category"] else None

        channel = await interaction.guild.create_text_channel(
            f"ticket-{interaction.user.name}",
            overwrites=overwrites,
            category=category
        )

        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT INTO tickets (user_id, channel_id) VALUES (?,?)",
                (interaction.user.id, channel.id)
            )
            await db.commit()

        msg = settings["ticket_msg"].replace("@User", interaction.user.mention)
        if settings["role"]:
            role = interaction.guild.get_role(settings["role"])
            if role:
                msg = msg.replace("@SupportRole", role.mention)

        await interaction.response.send_message(
            f"✅ Ticket created: {channel.mention}",
            ephemeral=True
        )

        await channel.send(msg, view=CloseView())

        if settings["logs"]:
            log = interaction.guild.get_channel(settings["logs"])
            if log:
                await log.send(f"📌 Ticket opened by {interaction.user.mention} → {channel.mention}")

# ================= PANEL =================
@bot.command()
@admin()
async def ticketpanel(ctx):
    settings = await get_settings(ctx.guild.id)
    embed = discord.Embed(
        title="🎫 Support Tickets",
        description=settings["panel_desc"],
        color=discord.Color.green()
    )
    await ctx.send(embed=embed, view=TicketView())

# ================= READY =================
@bot.event
async def on_ready():
    print(f"✅ {bot.user} is online")

bot.run(TOKEN)
