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
# ************************************************
# ⚠️ คุณต้องกำหนดค่าเหล่านี้ ⚠️
# ************************************************
DASHBOARD_CHANNEL_ID = int(os.getenv("DASHBOARD_CHANNEL_ID")) 
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")

# เปลี่ยนตัวเลขเหล่านี้เป็น ID ของบทบาทที่คุณต้องการให้ใช้คำสั่ง /announce ได้
ALLOWED_ANNOUNCER_ROLES = [
    # ใส่ Role ID ที่ได้รับอนุญาตที่นี่ (ตัวเลขเท่านั้น)
]
# ************************************************

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
## GitHub Webhook Helper Functions
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
## Aiohttp application setup (Webhook Server)
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
## Bot Events and Command Sync
# --------------------------------------------------------------------------------
@bot.event
async def on_ready():
    print(f'🤖 Logged in as {bot.user} (ID: {bot.user.id})')
    
    try:
        synced = await bot.tree.sync() 
        print(f"✨ Synced {len(synced)} global command(s).")
    except Exception as e:
        print(f"❌ Error syncing commands: {e}")
    
    # เริ่ม Webhook server ให้ทำงานพร้อมกับ Bot
    bot.loop.create_task(start_webhook_server())

# --------------------------------------------------------------------------------
## Slash Command: /announce (Public Post + Role Check)
# --------------------------------------------------------------------------------

# ฟังก์ชันตรวจสอบสิทธิ์ที่รวม is_owner และ has_any_role
def is_announcer(interaction: discord.Interaction) -> bool:
    """ตรวจสอบว่าผู้ใช้เป็น Guild Owner หรือมีบทบาทที่กำหนดหรือไม่"""
    # 1. ตรวจสอบว่าเป็น Guild Owner หรือไม่ (หัวดิส)
    if interaction.guild and interaction.user.id == interaction.guild.owner_id:
        return True
    
    # 2. ตรวจสอบว่ามีบทบาทที่กำหนดหรือไม่
    if ALLOWED_ANNOUNCER_ROLES and interaction.guild:
        user_role_ids = [role.id for role in interaction.user.roles]
        if any(role_id in user_role_ids for role_id in ALLOWED_ANNOUNCER_ROLES):
            return True
            
    return False

# 1. สร้าง Modal Class
class AnnouncementModal(discord.ui.Modal, title='📝 สร้างข้อความประชาสัมพันธ์'):
    
    # Text Input 1: Title (หัวเรื่อง)
    title_input = discord.ui.TextInput(
        label='หัวเรื่อง (Title)',
        placeholder='สรุป Live Session ประจำสัปดาห์ / อัปเดตแพตช์ใหม่',
        max_length=256,
        required=True
    )
    
    # Text Input 2: Description (เนื้อหา)
    description_input = discord.ui.TextInput(
        label='เนื้อหา (รองรับ Markdown)',
        placeholder='กรอกเนื้อหารายละเอียดทั้งหมดที่นี่...',
        style=discord.TextStyle.paragraph, 
        required=True
    )

    # Text Input 3: Image URL (ลิงก์รูปภาพ - ทางเลือก)
    image_url_input = discord.ui.TextInput(
        label='ลิงก์รูปภาพ (Image URL - ไม่บังคับ)',
        placeholder='ต้องเป็นลิงก์ที่จบด้วย .png, .jpg, .gif, .webp เท่านั้น (รองรับลิงก์ Discord)',
        max_length=2000,
        required=False
    )

    # ฟังก์ชันที่ทำงานเมื่อผู้ใช้กด Submit
    async def on_submit(self, interaction: discord.Interaction):
        title = self.title_input.value
        description = self.description_input.value
        image_url = self.image_url_input.value
        
        # 1. สร้าง Embed
        embed = discord.Embed(
            title=f"<a:torakong:1422185259042078770><a:torakong:1422185259042078770><a:torakong:1422185259042078770>  {title}",
            description=description,
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"ประกาศโดย: {interaction.user.display_name}", 
                         icon_url=interaction.user.display_avatar.url)
        
        # 2. ตั้งค่ารูปภาพ (ถ้ามี)
        valid_image_url = False
        if image_url and image_url.startswith('http') and (image_url.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp'))):
            embed.set_image(url=image_url)
            valid_image_url = True

        # 3. การตอบกลับ:
        # 3.1 ตอบกลับ Ephemeral เพื่อแจ้งว่ากำลังประมวลผล (ต้องทำเป็นอันดับแรก)
        await interaction.response.send_message("<a:45696190630e4f208144d0582a0b0414:1423939335928938506> กำลังโพสต์ประชาสัมพันธ์สาธารณะ...", ephemeral=True)
        
        # 3.2 ใช้ followup เพื่อส่งข้อความจริงแบบสาธารณะ
        message_content = "@everyone" # Ping @everyone ให้ทุกคนเห็นแจ้งเตือน
        
        await interaction.followup.send(content=message_content, embed=embed)
        
        # 3.3 แจ้งเตือนผู้ใช้ (Ephemeral) ถ้าลิงก์รูปภาพมีปัญหา และยืนยันการโพสต์
        success_message = "<a:45696190630e4f208144d0582a0b0414:1423939335928938506> โพสต์ประชาสัมพันธ์สาธารณะสำเร็จแล้ว!"
        if image_url and not valid_image_url and image_url: 
             success_message += "\n⚠️ ลิงก์รูปภาพไม่ถูกต้อง (ต้องเป็นลิงก์ URL ที่สมบูรณ์) - โพสต์ข้อความหลักโดยไม่มีรูปภาพ"

        # แก้ไขข้อความ Ephemeral แรก
        await interaction.edit_original_response(content=success_message)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await interaction.followup.send(f'❌ เกิดข้อผิดพลาดในการส่งประกาศ: {error}', ephemeral=True)


# 2. สร้าง Slash Command ที่เรียก Modal - พร้อมการตรวจสอบสิทธิ์!
@bot.tree.command(name="announce", description="📢 สร้างข้อความประชาสัมพันธ์แบบ Embed ด้วยฟอร์มกรอกข้อมูล (จำกัดสิทธิ์)")
@app_commands.check(is_announcer)
async def announce_command(interaction: discord.Interaction):
    await interaction.response.send_modal(AnnouncementModal())

# 3. การจัดการ Error สำหรับ Check Funtion
@announce_command.error
async def announce_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message(
            "❌ คุณไม่มีสิทธิ์ใช้คำสั่งประชาสัมพันธ์นี้. คำสั่งนี้จำกัดเฉพาะหัวหน้าเซิร์ฟเวอร์และบทบาทที่กำหนดเท่านั้น", 
            ephemeral=True
        )
    else:
        print(f"Error in announce_command: {error}")
        await interaction.response.send_message("❌ เกิดข้อผิดพลาดที่ไม่ทราบสาเหตุในการรันคำสั่ง.", ephemeral=True)


# --------------------------------------------------------------------------------
## Slash Command: /session 
# --------------------------------------------------------------------------------

# --- Class สำหรับ Options ของ /session ---
class SessionAction(discord.app_commands.Choice):
    def __init__(self, name: str, value: str):
        super().__init__(name=name, value=value)

# กำหนด Slash Command Group
@bot.tree.command(name="session", description="▶️ จัดการ Live Share Session ในช่องทำงานเป็นทีม")
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
        # ใช้ Custom Animated Emoji ในข้อความ Ephemeral ได้
        await interaction.response.send_message("<a:809832006988988486:1423939345026388008> คำสั่งนี้ใช้ได้เฉพาะช่อง #live-share-dashboard เท่านั้น", ephemeral=True)
        return

    if action == "start":
        if not link:
            await interaction.response.send_message("<a:809832006988988486:1423939345026388008> โปรดใส่ลิงก์ Live Share เมื่อใช้ /session start", ephemeral=True)
            return
            
        # 2. บันทึกข้อมูล
        session_data["link"] = link
        session_data["participants"] = [user_name] 
        session_data["start_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        session_data["end_time"] = None
        session_data["last_message_id"] = None 
        with open("session.json", "w") as f:
            json.dump(session_data, f)
        
        # 1. ตอบกลับ Ephemeral
        ephemeral_message = (
            f"<a:45696190630e4f208144d0582a0b0414:1423939335928938506> **Session เริ่มต้นแล้ว!**\n"
            f"**โฮสต์:** {user_name} (คุณ)\n"
            f"โพสต์แจ้งเตือนสาธารณะถูกส่งในช่องแล้ว"
        )
        await interaction.response.send_message(ephemeral_message, ephemeral=True)

        # 2. สร้าง Embed สาธารณะ
        embed = discord.Embed(title="<a:67c3e29969174247b000f7c7318660f:1423939328928780338> VS Code Live Share Session Started! <a:67c3e29969174247b000f7c7318660f:1423939328928780338>",
                              description="Session สำหรับทำงานร่วมกันได้เริ่มขึ้นแล้ว! กดปุ่มด้านล่างเพื่อเข้าร่วม",
                              color=0x3498db)
        embed.add_field(name="ผู้เริ่ม Session", value=user_name, inline=True)
        embed.add_field(name="เวลาเริ่ม", value=session_data["start_time"], inline=True)
        embed.add_field(name="ผู้เข้าร่วมปัจจุบัน", value=", ".join(session_data["participants"]), inline=False)
        
        # ใช้ Unicode Emoji 🔗 แทน Custom Animated Emoji ในปุ่มกด
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="🖱️: ̗̀➛ เข้าร่วม Session (LIVE)", url=link, style=discord.ButtonStyle.green)) 
        
        # 3. โพสต์ Embed สาธารณะ: ใช้ FOLLOWUP
        sent_message = await interaction.followup.send(embed=embed, view=view, wait=True)
        
        # บันทึก ID ของข้อความ
        session_data["last_message_id"] = sent_message.id
        with open("session.json", "w") as f:
            json.dump(session_data, f)

        
    elif action == "status":
        if not session_data.get("link"):
            await interaction.response.send_message("<a:809832006988988486:1423939345026388008> ขณะนี้ไม่มี Live Share Session ที่กำลังทำงานอยู่", ephemeral=True)
            return

        # 1. สร้าง Embed แสดงสถานะ 
        embed = discord.Embed(title="<a:1249347622158860308:1422185419491246101> สถานะ Live Share Session ปัจจุบัน",
                              description=f"<a:2a3404eb19f54b10b16e83768f5937ae:1423939322947829841> Session กำลังทำงานอยู่ (จัดการโดยคุณ: {user_name})",
                              color=0xf39c12)
        embed.add_field(name="เวลาเริ่ม", value=session_data.get("start_time","-"), inline=True)
        embed.add_field(name="ผู้เข้าร่วม", value=", ".join(session_data.get("participants",[])) or "(ยังไม่มี)", inline=False)
        
        # ใช้ Unicode Emoji 🔗 แทน Custom Animated Emoji ในปุ่มกด
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="🔗 ลิงก์ Session ปัจจุบัน", url=session_data.get('link','-'), style=discord.ButtonStyle.green))

        # 2. ตอบกลับด้วย Embed (Ephemeral)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True) 

    elif action == "end":
        if not session_data.get("link"):
            await interaction.response.send_message("❌ ไม่มี Live Share Session ที่จะให้ปิด", ephemeral=True)
            return
            
        end_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        current_link = session_data.get("link") 
        current_message_id = session_data.get("last_message_id")
        current_participants = session_data.get("participants", [])
        current_start_time = session_data.get("start_time", "-")
        
        # 1. คำนวณระยะเวลา
        try:
            start_t = time.mktime(time.strptime(current_start_time, "%Y-%m-%d %H:%M:%S"))
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
        
        # 3. ตอบกลับ Ephemeral
        ephemeral_message = (
            f"<a:45696190630e4f208144d0582a0b0414:1423939335928938506> **Session ถูกปิดแล้ว!**\n"
            f"**ผู้ปิด Session:** {user_name} (คุณ)\n"
            f"โพสต์สรุปถูกส่งในช่องแล้ว"
        )
        await interaction.response.send_message(ephemeral_message, ephemeral=True)

        # 4. สร้าง Embed สรุป
        embed = discord.Embed(title="<a:810020134865338368:1423938901671804968> Live Share Session Ended",
                              description="Session สิ้นสุดลงแล้ว ขอขอบคุณที่เข้าร่วม!",
                              color=0xe74c3c)
        embed.add_field(name="เวลาเริ่ม", value=current_start_time, inline=True)
        embed.add_field(name="เวลาสิ้นสุด", value=end_time_str, inline=True)
        embed.add_field(name="ระยะเวลา", value=duration_text, inline=True)
        embed.add_field(name="ผู้เข้าร่วม", value=", ".join(current_participants) or "(ไม่มี)", inline=False)

        # ใช้ Unicode Emoji 🔗 แทน Custom Animated Emoji ในปุ่มกด
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="🔗 ลิงก์ Session ที่ผ่านมา", url=current_link, style=discord.ButtonStyle.secondary))

        # 5. โพสต์ Embed สาธารณะ: ใช้ FOLLOWUP
        await interaction.followup.send(embed=embed, view=view)
        
        # 6. ลบปุ่มออกจากข้อความ 'START' เดิม
        if current_message_id:
            try:
                channel_obj = bot.get_channel(DASHBOARD_CHANNEL_ID)
                if channel_obj:
                    old_message = await channel_obj.fetch_message(current_message_id)
                    old_embed = old_message.embeds[0]
                    # ใช้ Custom Emoji เดิมใน Title ได้
                    old_embed.title = "<a:67c3e29969174247b000f7c7318660f:1423939328928780338> VS Code Live Share Session Started! (Finished)" 
                    old_embed.description = "Session นี้สิ้นสุดลงแล้ว ดูสรุปด้านล่าง"
                    
                    # แก้ไขข้อความเดิมโดยลบปุ่มออก (view=None)
                    await old_message.edit(embed=old_embed, view=None) 
            except discord.NotFound:
                print(f"Warning: Original START message with ID {current_message_id} not found for editing.")


# --------------------------------------------------------------------------------
## Run Bot
# --------------------------------------------------------------------------------
bot.run(TOKEN)
