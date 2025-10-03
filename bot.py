import discord
from discord.ext import commands, tasks
import json
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DASHBOARD_CHANNEL_ID = int(os.getenv("DASHBOARD_CHANNEL_ID"))

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Load or initialize session data
try:
    with open("session.json", "r") as f:
        session_data = json.load(f)
except FileNotFoundError:
    session_data = {}

# -------- Helper Function --------
async def send_dm_only(user, message):
    try:
        await user.send(message)
    except:
        print(f"Cannot send DM to {user}")

# -------- Commands --------
@bot.command(name="session")
async def session(ctx, action=None, link=None):
    if ctx.channel.id != DASHBOARD_CHANNEL_ID:
        await send_dm_only(ctx.author, "❌ คำสั่งนี้ใช้ได้เฉพาะช่อง #live-share-dashboard")
        return

    channel = bot.get_channel(DASHBOARD_CHANNEL_ID)

    if action == "start":
        if not link:
            await ctx.send("❌ โปรดใส่ลิงก์ Live Share")
            return
        session_data["link"] = link
        session_data["participants"] = []
        session_data["start_time"] = "10:00"  # placeholder
        session_data["end_time"] = None
        with open("session.json", "w") as f:
            json.dump(session_data, f)
        
        embed = discord.Embed(title="💻 VS Code Live Share Session",
                              color=0x3498db)
        embed.add_field(name="Session Link", value=f"[🟦 กดตรงนี้]({link})", inline=False)
        embed.add_field(name="ผู้เข้าร่วม", value="(ยังไม่มี)", inline=False)
        embed.add_field(name="เริ่ม", value=session_data["start_time"], inline=True)
        embed.add_field(name="สิ้นสุด", value="-", inline=True)
        embed.add_field(name="ระยะเวลา", value="-", inline=True)
        await channel.send(embed=embed)

    elif action == "status":
        embed = discord.Embed(title="💻 VS Code Live Share Session",
                              color=0x3498db)
        embed.add_field(name="Session Link", value=f"[🟦 กดตรงนี้]({session_data.get('link','-')})", inline=False)
        embed.add_field(name="ผู้เข้าร่วม", value=", ".join(session_data.get("participants",[])) or "(ยังไม่มี)", inline=False)
        embed.add_field(name="เริ่ม", value=session_data.get("start_time","-"), inline=True)
        embed.add_field(name="สิ้นสุด", value=session_data.get("end_time","-"), inline=True)
        embed.add_field(name="ระยะเวลา", value="-", inline=True)
        await channel.send(embed=embed)

    elif action == "end":
        session_data["end_time"] = "11:23"  # placeholder
        # ระยะเวลาคำนวณง่าย ๆ
        embed = discord.Embed(title="💻 Live Share Session Ended",
                              color=0xe74c3c)
        embed.add_field(name="Session Link", value=f"[🟦 กดตรงนี้]({session_data.get('link','-')})", inline=False)
        embed.add_field(name="ผู้เข้าร่วม", value=", ".join(session_data.get("participants",[])) or "(ยังไม่มี)", inline=False)
        embed.add_field(name="เริ่ม", value=session_data.get("start_time","-"), inline=True)
        embed.add_field(name="สิ้นสุด", value=session_data.get("end_time","-"), inline=True)
        embed.add_field(name="ระยะเวลา", value="1 ชั่วโมง 23 นาที", inline=True)
        await channel.send(embed=embed)

        session_data.clear()
        with open("session.json", "w") as f:
            json.dump(session_data, f)

    else:
        await send_dm_only(ctx.author, "❌ โปรดใช้คำสั่ง: !session start/status/end <link>")

# -------- Run Bot --------
bot.run(TOKEN)
