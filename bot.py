import discord
from discord.ext import commands
import json
import os
from dotenv import load_dotenv
import asyncio
from aiohttp import web
import hmac
import hashlib
import time # เพิ่มไลบรารี time สำหรับจัดการเวลา

# โหลด Environment Variables
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DASHBOARD_CHANNEL_ID = int(os.getenv("DASHBOARD_CHANNEL_ID"))
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")

# การตั้งค่า Bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

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
    # เริ่ม Webhook server ให้ทำงานพร้อมกับ Bot
    bot.loop.create_task(start_webhook_server())


# -------- Commands: Live Share Session --------
@bot.command(name="session")
async def session(ctx, action=None, *, link=None): # ใช้ *, link=None เพื่อรับลิงก์ที่มีช่องว่างได้
    if ctx.channel.id != DASHBOARD_CHANNEL_ID:
        await send_dm_only(ctx.author, "❌ คำสั่งนี้ใช้ได้เฉพาะช่อง #live-share-dashboard")
        return

    channel = bot.get_channel(DASHBOARD_CHANNEL_ID)

    if action == "start":
        if not link:
            await ctx.send("❌ โปรดใส่ลิงก์ Live Share")
            return
            
        # บันทึกข้อมูล
        session_data["link"] = link
        session_data["participants"] = [ctx.author.display_name] # ให้คนเริ่มเป็น Participant คนแรก
        session_data["start_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) # ใช้เวลาจริง
        session_data["end_time"] = None
        with open("session.json", "w") as f:
            json.dump(session_data, f)
        
        # โพสต์ Embed (ตรวจสอบแล้วว่าโค้ดส่วนนี้ถูกต้อง)
        embed = discord.Embed(title="💻 VS Code Live Share Session Started!",
                              description="Session สำหรับทำงานร่วมกันได้เริ่มขึ้นแล้ว!",
                              color=0x3498db)
        embed.add_field(name="Session Link", value=f"[🟦 กดตรงนี้เพื่อเข้าร่วม]({link})", inline=False)
        embed.add_field(name="ผู้เริ่ม Session", value=ctx.author.display_name, inline=True)
        embed.add_field(name="เวลาเริ่ม", value=session_data["start_time"], inline=True)
        embed.add_field(name="ผู้เข้าร่วมปัจจุบัน", value=", ".join(session_data["participants"]), inline=False)

        await channel.send(embed=embed)
        await ctx.message.delete() # ลบข้อความคำสั่ง !session start เพื่อความสะอาด
        
    elif action == "status":
        if not session_data.get("link"):
            await ctx.send("⚠️ ขณะนี้ไม่มี Live Share Session ที่กำลังทำงานอยู่")
            return

        embed = discord.Embed(title="💻 VS Code Live Share Session Status",
                              description="สถานะปัจจุบันของ Session ที่กำลังใช้งาน",
                              color=0xf39c12)
        embed.add_field(name="Session Link", value=f"[🟦 กดตรงนี้]({session_data.get('link','-')})", inline=False)
        embed.add_field(name="เวลาเริ่ม", value=session_data.get("start_time","-"), inline=True)
        embed.add_field(name="สิ้นสุด", value="N/A", inline=True)
        embed.add_field(name="ผู้เข้าร่วม", value=", ".join(session_data.get("participants",[])) or "(ยังไม่มี)", inline=False)
        
        await channel.send(embed=embed)

    elif action == "end":
        if not session_data.get("link"):
            await ctx.send("⚠️ ไม่มี Live Share Session ที่จะให้ปิด")
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
        
        await channel.send(embed=embed)
        
        # ล้างข้อมูล Session
        session_data.clear()
        with open("session.json", "w") as f:
            json.dump(session_data, f)
        await ctx.message.delete()

    else:
        # ข้อความแจ้งเตือนคำสั่งที่ไม่ถูกต้อง (ส่งเป็น DM)
        await send_dm_only(ctx.author, "❌ โปรดใช้คำสั่ง: !session start <link> | !session status | !session end")

# -------- Run Bot --------
bot.run(TOKEN)
