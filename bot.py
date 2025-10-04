import discord
from discord.ext import commands
from discord import app_commands 
import json
import os
from dotenv import load_dotenv
import asyncio
from aiohttp import web
import hmac
import hashlib
import time

# โหลด Environment Variables
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DASHBOARD_CHANNEL_ID = int(os.getenv("DASHBOARD_CHANNEL_ID"))
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")

# การตั้งค่า Bot
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="!", intents=intents) 

# โหลดหรือเริ่มต้นข้อมูล Session
try:
    with open("session.json", "r") as f:
        session_data = json.load(f)
except FileNotFoundError:
    session_data = {}

# --------------------------------------------------------------------------------
# Webhook Helper Functions (ส่วนของ GitHub Webhook - คงเดิม)
# --------------------------------------------------------------------------------

def verify_signature(request_body, signature):
    """ตรวจสอบลายเซ็นของ GitHub webhook เพื่อยืนยันความถูกต้องของ request"""
    if not GITHUB_WEBHOOK_SECRET:
        print("ERROR: GITHUB_WEBHOOK_SECRET is not set.")
        return False
        
    mac = hmac.new(GITHUB_WEBHOOK_SECRET.encode(), msg=request_body, digestmod=hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    return hmac.compare_digest(expected, signature)

async def update_github_embed(payload, bot_client):
    """สร้างและส่ง Discord Embed สำหรับแจ้งเตือนสถานะ GitHub"""
    await bot_client.wait_until_ready()
    channel = bot_client.get_channel(DASHBOARD_CHANNEL_ID)
    
    if channel is None:
        print(f"ERROR: Dashboard channel with ID {DASHBOARD_CHANNEL_ID} not found.")
        return

    try:
        embed = discord.Embed(title="📦 GitHub Repo Status", color=0x3498db)

        repo_name = payload["repository"]["name"]
        branch = payload.get("ref", "unknown/ref").split("/")[-1] 
        last_commit = payload["head_commit"]["message"]
        author = payload["head_commit"]["author"]["name"]
        commit_url = payload["head_commit"]["url"]

        embed.add_field(name="Repo", value=repo_name, inline=False)
        embed.add_field(name="Branch", value=branch, inline=True)
        embed.add_field(name="Last Commit", value=f"[📝 {last_commit} by {author}]({commit_url})", inline=False)
        
        embed.add_field(name="PRs Open", value="🔄 2", inline=True)
        embed.add_field(name="Issues Open", value="⚠️ 3", inline=True)

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="View Repository", url=payload["repository"]["html_url"], style=discord.ButtonStyle.link))
        
        await channel.send(embed=embed, view=view)
        print(f"Successfully sent GitHub notification for push on branch {branch}")

    except Exception as e:
        print(f"Error processing or sending GitHub embed: {e}")

# --------------------------------------------------------------------------------
# Aiohttp application setup (ส่วนของ Webhook Server - คงเดิม)
# --------------------------------------------------------------------------------
webhook_app = web.Application()

async def handle_webhook(request):
    """ฟังก์ชันหลักสำหรับจัดการ request ที่เข้ามาที่ /webhook"""
    body = await request.read()
    signature = request.headers.get("X-Hub-Signature-256")
    
    if not signature or not verify_signature(body, signature):
        print("Webhook received with Invalid signature.")
        return web.Response(status=401, text="Invalid signature")

    event = request.headers.get("X-GitHub-Event")
    
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        print("Failed to decode webhook JSON payload.")
        return web.Response(status=400, text="Invalid JSON")

    if event == "push" and payload.get("ref", "").startswith("refs/heads/"):
        asyncio.create_task(update_github_embed(payload, bot))
        print(f"Received and scheduled push event for repo {payload['repository']['name']}")
    else:
        print(f"Received GitHub event: {event}. Ignoring.")

    return web.Response(text="OK")

# ผูก handler เข้ากับเส้นทาง /webhook
webhook_app.router.add_post("/webhook", handle_webhook)

# -------- Aiohttp Server Startup Function --------
async def start_webhook_server():
    """เริ่มต้น Aiohttp server บน PORT ที่กำหนดโดย environment variable"""
    port = int(os.environ.get("PORT", 5000))
    runner = web.AppRunner(webhook_app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=port) 
    
    print(f"🚀 Starting Aiohttp Webhook Server on 0.0.0.0:{port}...")
    try:
        await site.start()
    except Exception as e:
        print(f"FATAL: Failed to start web server on port {port}. Error: {e}")

# --------------------------------------------------------------------------------
# Events and Command Sync (ส่วนของ Event - คงเดิม)
# --------------------------------------------------------------------------------
@bot.event
async def on_ready():
    print(f'🤖 Logged in as {bot.user} (ID: {bot.user.id})')
    
    # *** ส่วนสำคัญ: การลงทะเบียน Slash Commands (Sync) ***
    try:
        synced = await bot.tree.sync() 
        print(f"✨ Synced {len(synced)} global command(s).")
    except Exception as e:
        print(f"❌ Error syncing commands: {e}")
    
    # เริ่ม Webhook server ให้ทำงานพร้อมกับ Bot
    bot.loop.create_task(start_webhook_server())


# --------------------------------------------------------------------------------
# Slash Command: /session (ส่วนที่ปรับปรุง Ephemeral)
# --------------------------------------------------------------------------------

# --- Class สำหรับ Options ของ /session ---
class SessionAction(discord.app_commands.Choice):
    def __init__(self, name: str, value: str):
        super().__init__(name=name, value=value)

# กำหนด Slash Command Group
@bot.tree.command(name="session", description="<a:67c3e29969174247b000f7c7318660f:1423939328928780338> จัดการ Live Share Session ในช่องทำงานเป็นทีม")
@app_commands.describe(
    action="เลือกคำสั่ง: start, status หรือ end",
    link="ลิงก์ Live Share (ใช้เฉพาะกับ action: start)",
)
@app_commands.choices(action=[
    SessionAction(name="▶️ เริ่ม Live Share Session", value="start"),
    SessionAction(name="ℹ️ แสดงสถานะ Session ปัจจุบัน", value="status"),
    SessionAction(name="⏹️ ปิด Session และคำนวณเวลา", value="end")
])
async def session_command(interaction: discord.Interaction, action: str, link: str = None):
    
    # ดึงชื่อผู้ใช้
    user_name = interaction.user.display_name 

    # 1. ตรวจสอบ Channel ID
    if interaction.channel_id != DASHBOARD_CHANNEL_ID:
        await interaction.response.send_message("<a:809832006988988486:1423939345026388008> คำสั่งนี้ใช้ได้เฉพาะช่อง #live-share-dashboard เท่านั้น", ephemeral=True)
        return

    channel = bot.get_channel(DASHBOARD_CHANNEL_ID)

    if action == "start":
        if not link:
            await interaction.response.send_message("<a:809832006988988486:1423939345026388008> โปรดใส่ลิงก์ Live Share เมื่อใช้ /session start", ephemeral=True)
            return
            
        # 2. บันทึกข้อมูล
        session_data["link"] = link
        session_data["participants"] = [user_name] 
        session_data["start_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        session_data["end_time"] = None
        with open("session.json", "w") as f:
            json.dump(session_data, f)
        
        # *** ปรับปรุง: ข้อความ Ephemeral สำหรับ START (กระชับและไม่มีลิงก์) ***
        # ใช้อิโมจิ: <a:45696190630e4f208144d0582a0b0414:1423939335928938506:>
        ephemeral_message = (
            f"<a:45696190630e4f208144d0582a0b0414:1423939335928938506:> **Session เริ่มต้นแล้ว!**\n"
            f"**โฮสต์:** {user_name} (คุณ)\n"
            f"โพสต์แจ้งเตือนสาธารณะถูกส่งในช่องแล้ว"
        )
        await interaction.response.send_message(ephemeral_message, ephemeral=True)

        # 3. สร้างและโพสต์ Embed (ข้อความสาธารณะ - ยังคงแสดงลิงก์)
        embed = discord.Embed(title="<a:67c3e29969174247b000f7c7318660f:1423939328928780338> VS Code Live Share Session Started! <a:67c3e29969174247b000f7c7318660f:1423939328928780338>",
                              description="Session สำหรับทำงานร่วมกันได้เริ่มขึ้นแล้ว! กดลิงก์เพื่อเข้าร่วม",
                              color=0x3498db)
        embed.add_field(name="Session Link", value=f"[<a:138303skullz:1423938938913165385> กดตรงนี้เพื่อเข้าร่วม]({link})", inline=False)
        embed.add_field(name="ผู้เริ่ม Session", value=user_name, inline=True)
        embed.add_field(name="เวลาเริ่ม", value=session_data["start_time"], inline=True)
        embed.add_field(name="ผู้เข้าร่วมปัจจุบัน", value=", ".join(session_data["participants"]), inline=False)
        
        await channel.send(embed=embed)
        
    elif action == "status":
        if not session_data.get("link"):
            await interaction.response.send_message("<a:809832006988988486:1423939345026388008> ขณะนี้ไม่มี Live Share Session ที่กำลังทำงานอยู่", ephemeral=True)
            return

        # 1. สร้าง Embed แสดงสถานะ (Ephemeral - ยังคงแสดงลิงก์เพื่อให้ผู้ใช้เช็คได้)
        embed = discord.Embed(title="ℹ️ สถานะ Live Share Session ปัจจุบัน", # ปรับให้กระชับ
                              description=f"✅ Session กำลังทำงานอยู่ (จัดการโดยคุณ: {user_name})", # ปรับข้อความ
                              color=0xf39c12)
        embed.add_field(name="Session Link", value=f"[<a:138303skullz:1423938938913165385> ลิงก์ Session]({session_data.get('link','-')})", inline=False)
        embed.add_field(name="เวลาเริ่ม", value=session_data.get("start_time","-"), inline=True)
        embed.add_field(name="ผู้เข้าร่วม", value=", ".join(session_data.get("participants",[])) or "(ยังไม่มี)", inline=False)
        
        # 2. ตอบกลับด้วย Embed (Ephemeral)
        await interaction.response.send_message(embed=embed, ephemeral=True) 

    elif action == "end":
        if not session_data.get("link"):
            await interaction.response.send_message("❌ ไม่มี Live Share Session ที่จะให้ปิด", ephemeral=True)
            return
            
        end_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        
        # 1. คำนวณระยะเวลา
        try:
            start_t = time.mktime(time.strptime(session_data["start_time"], "%Y-%m-%d %H:%M:%S"))
            end_t = time.mktime(time.strptime(end_time_str, "%Y-%m-%d %H:%M:%S"))
            duration_sec = end_t - start_t
            hours = int(duration_sec // 3600)
            minutes = int((duration_sec % 3600) // 60)
            duration_text = f"{hours} ชั่วโมง {minutes} นาที"
        except:
            duration_text = "-"
        
        # 2. ล้างข้อมูล Session
        session_data.clear()
        with open("session.json", "w") as f:
            json.dump(session_data, f)
        
        # *** ปรับปรุง: ข้อความ Ephemeral สำหรับ END (กระชับและไม่มีลิงก์) ***
        # ใช้อิโมจิ: <a:45696190630e4f208144d0582a0b0414:1423939335928938506:>
        ephemeral_message = (
            f"<a:45696190630e4f208144d0582a0b0414:1423939335928938506:> **Session ถูกปิดแล้ว!**\n"
            f"**ผู้ปิด Session:** {user_name} (คุณ)\n"
            f"โพสต์สรุปถูกส่งในช่องแล้ว"
        )
        await interaction.response.send_message(ephemeral_message, ephemeral=True)

        # 3. สร้าง Embed สรุป (ข้อความสาธารณะ - ยังคงแสดงลิงก์)
        embed = discord.Embed(title="<a:810020134865338368:1423938901671804968:> Live Share Session Ended",
                              description="Session สิ้นสุดลงแล้ว ขอขอบคุณที่เข้าร่วม!",
                              color=0xe74c3c)
        embed.add_field(name="Session Link", value=f"[<a:138303skullz:1423938938913165385> ลิงก์ Session ที่ผ่านมา]({session_data.get('link','-')})", inline=False)
        embed.add_field(name="เวลาเริ่ม", value=session_data.get("start_time","-"), inline=True)
        embed.add_field(name="เวลาสิ้นสุด", value=end_time_str, inline=True)
        embed.add_field(name="ระยะเวลา", value=duration_text, inline=True)
        embed.add_field(name="ผู้เข้าร่วม", value=", ".join(session_data.get("participants",[])) or "(ไม่มี)", inline=False)

        await channel.send(embed=embed)


# --------------------------------------------------------------------------------
# Run Bot
# --------------------------------------------------------------------------------
bot.run(TOKEN)
