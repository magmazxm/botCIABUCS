import discord
from discord.ext import commands
from discord import app_commands # นำเข้า app_commands เพื่อใช้ Slash Commands
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
# เปลี่ยน command_prefix เป็น ! หรือลบออกไปเลย เพราะเราจะใช้ Slash Commands แทน
bot = commands.Bot(command_prefix="!", intents=intents)

# สร้าง Command Tree (จำเป็นสำหรับ Slash Commands)
bot.tree = app_commands.CommandTree(bot)

# โหลดหรือเริ่มต้นข้อมูล Session จากไฟล์ session.json
try:
    with open("session.json", "r") as f:
        session_data = json.load(f)
except FileNotFoundError:
    session_data = {}

# -------- Webhook Helper Functions (ฟังก์ชันสำหรับ GitHub Webhook) --------

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
        # เพิ่มลิงก์ไปยัง Commit
        embed.add_field(name="Last Commit", value=f"[📝 {last_commit} by {author}]({commit_url})", inline=False)
        
        # Placeholders
        embed.add_field(name="PRs Open", value="🔄 2", inline=True)
        embed.add_field(name="Issues Open", value="⚠️ 3", inline=True)

        # ปุ่มลิงก์ไปยัง Repository
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="View Repository", url=payload["repository"]["html_url"], style=discord.ButtonStyle.link))
        
        await channel.send(embed=embed, view=view)
        print(f"Successfully sent GitHub notification for push on branch {branch}")

    except Exception as e:
        print(f"Error processing or sending GitHub embed: {e}")

# -------- Aiohttp application setup --------
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
        # รัน update_github_embed ใน background task พร้อมส่ง bot instance ไปด้วย
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

# -------- Helper Function (สำหรับ Commands) --------
async def send_dm_only(user, message):
    """ส่งข้อความ DM ไปหา user หากไม่สามารถส่งในช่องแชทได้"""
    try:
        await user.send(message)
    except:
        print(f"Cannot send DM to {user}")

# -------- Events --------
@bot.event
async def on_ready():
    print(f'🤖 Logged in as {bot.user} (ID: {bot.user.id})')
    
    # *** ส่วนสำคัญ: การลงทะเบียน Slash Commands ***
    try:
        synced = await bot.tree.sync() # สั่งให้ Bot ลงทะเบียนคำสั่งทั้งหมดกับ Discord API
        print(f"✨ Synced {len(synced)} global command(s).")
    except Exception as e:
        print(f"❌ Error syncing commands: {e}")
    # **********************************************
    
    # เริ่ม Webhook server ให้ทำงานพร้อมกับ Bot
    bot.loop.create_task(start_webhook_server())


# --- Class สำหรับ Options ของ /session ---
# กำหนดทางเลือกที่ผู้ใช้สามารถเลือกได้สำหรับพารามิเตอร์ 'action'
class SessionAction(discord.app_commands.Choice):
    def __init__(self, name: str, value: str):
        super().__init__(name=name, value=value)

# กำหนด Slash Command Group
@bot.tree.command(name="session", description="จัดการ Live Share Session ในช่องทำงานเป็นทีม")
@app_commands.describe(
    action="เลือกคำสั่ง: start, status หรือ end",
    link="ลิงก์ Live Share (ใช้เฉพาะกับ action: start)",
)
@app_commands.choices(action=[
    SessionAction(name="เริ่ม Live Share Session", value="start"),
    SessionAction(name="แสดงสถานะ Session ปัจจุบัน", value="status"),
    SessionAction(name="ปิด Session และคำนวณเวลา", value="end")
])
async def session_command(interaction: discord.Interaction, action: str, link: str = None):
    # **ข้อสำคัญ: ใช้ interaction.channel.id แทน ctx.channel.id**
    if interaction.channel_id != DASHBOARD_CHANNEL_ID:
        # ใช้ interaction.response.send_message เพื่อตอบกลับแบบ In-App Reply
        await interaction.response.send_message("❌ คำสั่งนี้ใช้ได้เฉพาะช่อง #live-share-dashboard เท่านั้น", ephemeral=True)
        return

    # **ข้อสำคัญ: ใช้ interaction.response.defer() เพื่อตอบกลับทันที (ถ้าใช้เวลานานกว่า 3 วินาที)**
    # ในกรณีนี้เราอาจจะตอบกลับเลยโดยไม่ต้อง defer ก็ได้ หรือจะ defer ไว้ก่อนแล้วค่อย follow up

    channel = bot.get_channel(DASHBOARD_CHANNEL_ID) # ยังคงใช้ channel เพื่อส่งข้อความ Embed หลัก

    if action == "start":
        if not link:
            await interaction.response.send_message("❌ โปรดใส่ลิงก์ Live Share เมื่อใช้ /session start", ephemeral=True)
            return
            
        # บันทึกข้อมูล
        session_data["link"] = link
        # **ข้อสำคัญ: ใช้ interaction.user.display_name แทน ctx.author.display_name**
        session_data["participants"] = [interaction.user.display_name] 
        session_data["start_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        session_data["end_time"] = None
        with open("session.json", "w") as f:
            json.dump(session_data, f)
        
        # โพสต์ Embed
        embed = discord.Embed(title="💻 VS Code Live Share Session Started!",
                              description="Session สำหรับทำงานร่วมกันได้เริ่มขึ้นแล้ว!",
                              color=0x3498db)
        embed.add_field(name="Session Link", value=f"[🟦 กดตรงนี้เพื่อเข้าร่วม]({link})", inline=False)
        embed.add_field(name="ผู้เริ่ม Session", value=interaction.user.display_name, inline=True)
        embed.add_field(name="เวลาเริ่ม", value=session_data["start_time"], inline=True)
        embed.add_field(name="ผู้เข้าร่วมปัจจุบัน", value=", ".join(session_data["participants"]), inline=False)

        # เนื่องจากต้องการให้ Embed เป็นข้อความหลักในช่องแชท
        # เราจะตอบกลับ Slash Command ด้วยข้อความสั้นๆ Ephemeral ก่อน
        await interaction.response.send_message("✅ เริ่ม Session แล้ว!", ephemeral=True)
        # แล้วส่ง Embed ตามมาเป็นข้อความปกติ (Follow up)
        await channel.send(embed=embed)
        
    elif action == "status":
        if not session_data.get("link"):
            await interaction.response.send_message("⚠️ ขณะนี้ไม่มี Live Share Session ที่กำลังทำงานอยู่", ephemeral=True)
            return

        embed = discord.Embed(title="💻 VS Code Live Share Session Status",
                              description="สถานะปัจจุบันของ Session ที่กำลังใช้งาน",
                              color=0xf39c12)
        embed.add_field(name="Session Link", value=f"[🟦 กดตรงนี้]({session_data.get('link','-')})", inline=False)
        embed.add_field(name="เวลาเริ่ม", value=session_data.get("start_time","-"), inline=True)
        embed.add_field(name="สิ้นสุด", value="N/A", inline=True)
        embed.add_field(name="ผู้เข้าร่วม", value=", ".join(session_data.get("participants",[])) or "(ยังไม่มี)", inline=False)
        
        # ตอบกลับด้วย Embed โดยตรง
        await interaction.response.send_message(embed=embed)

    elif action == "end":
        if not session_data.get("link"):
            await interaction.response.send_message("⚠️ ไม่มี Live Share Session ที่จะให้ปิด", ephemeral=True)
            return
            
        end_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        session_data["end_time"] = end_time_str
        
        # คำนวณระยะเวลา (อย่างง่าย)
        try:
            start_t = time.mktime(time.strptime(session_data["start_time"], "%Y-%m-%d %H:%M:%S"))
            end_t = time.mktime(time.strptime(end_time_str, "%Y-%m-%d %H:%M:%S"))
            duration_sec = end_t - start_t
            hours = int(duration_sec // 3600)
            minutes = int((duration_sec % 3600) // 60)
            duration_text = f"{hours} ชั่วโมง {minutes} นาที"
        except:
            duration_text = "-"

        embed = discord.Embed(title="💻 Live Share Session Ended",
                              description="Session สิ้นสุดลงแล้ว ขอขอบคุณที่เข้าร่วม!",
                              color=0xe74c3c)
        embed.add_field(name="Session Link", value=f"[🟦 ลิงก์ Session ที่ผ่านมา]({session_data.get('link','-')})", inline=False)
        embed.add_field(name="เวลาเริ่ม", value=session_data.get("start_time","-"), inline=True)
        embed.add_field(name="เวลาสิ้นสุด", value=session_data.get("end_time","-"), inline=True)
        embed.add_field(name="ระยะเวลา", value=duration_text, inline=True)
        embed.add_field(name="ผู้เข้าร่วม", value=", ".join(session_data.get("participants",[])) or "(ไม่มี)", inline=False)
        
        # ล้างข้อมูล Session
        session_data.clear()
        with open("session.json", "w") as f:
            json.dump(session_data, f)
        
        # ตอบกลับ Slash Command ด้วยข้อความสั้นๆ Ephemeral ก่อน
        await interaction.response.send_message("✅ ปิด Session เรียบร้อยแล้ว!", ephemeral=True)
        # แล้วส่ง Embed ตามมาเป็นข้อความปกติ
        await channel.send(embed=embed)


# -------- Run Bot --------
# bot.run(TOKEN)
# ข้อควรจำ: โค้ดนี้ถูกแก้ไขเพื่อให้รองรับ Slash Commands และควรจะทำให้คำสั่ง /session ปรากฏขึ้นมาแนะนำ
