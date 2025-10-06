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
import urllib.parse
import datetime 
import pytz     

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
    1423975320821829683
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
## Timezone Helper Function
# --------------------------------------------------------------------------------

def get_bkk_time():
    """ดึงเวลาปัจจุบันในโซนเวลา Asia/Bangkok"""
    bkk_timezone = pytz.timezone('Asia/Bangkok')
    now = datetime.datetime.now(bkk_timezone)
    # ฟอร์แมตเวลาโดยไม่รวม Timezone Info
    return now.strftime("%Y-%m-%d %H:%M:%S")

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
## Slash Command: /announce (Public Post + Role Check) - REVISED FOR OPTIONAL FIELDS
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

# 1. สร้าง Modal Class (เปลี่ยน required เป็น False สำหรับ Title และ Description)
class AnnouncementModal(discord.ui.Modal, title='📝 สร้างข้อความประชาสัมพันธ์'):

    # Text Input 1: Title (หัวเรื่อง) - OPTIONAL
    title_input = discord.ui.TextInput(
        label='หัวเรื่อง (Title) - ไม่บังคับ',
        placeholder='สรุป Live Session ประจำสัปดาห์ / อัปเดตแพตช์ใหม่',
        max_length=256,
        required=False  # <--- OPTIONAL
    )

    # Text Input 2: Description (เนื้อหา) - OPTIONAL
    description_input = discord.ui.TextInput(
        label='เนื้อหา (รองรับ Markdown) - ไม่บังคับ',
        placeholder='กรอกเนื้อหารายละเอียดทั้งหมดที่นี่...',
        style=discord.TextStyle.paragraph,
        required=False  # <--- OPTIONAL
    )

    # Text Input 3: Image URL (ลิงก์รูปภาพ) - OPTIONAL
    image_url_input = discord.ui.TextInput(
        label='ลิงก์รูปภาพ (Image URL - ไม่บังคับ)',
        placeholder='ต้องเป็นลิงก์ที่จบด้วย .png, .jpg, .gif, .webp เท่านั้น',
        max_length=2000,
        required=False
    )
    
    # Text Input 4: Mention Choice (เลือก Mention) - OPTIONAL
    mention_input = discord.ui.TextInput(
        label='เลือกการแท็ก (@everyone, Role ID/ชื่อบทบาท) - ไม่บังคับ',
        placeholder='@everyone, @testers, 1423975320821829683',
        max_length=100,
        required=False
    )

    # ฟังก์ชันที่ทำงานเมื่อผู้ใช้กด Submit
    async def on_submit(self, interaction: discord.Interaction):
        title = self.title_input.value or ""
        description = self.description_input.value or ""
        image_url = self.image_url_input.value or ""
        mention_choice = self.mention_input.value.strip() or ""

        # 0. ตรวจสอบว่ามีการกรอกข้อมูลที่จำเป็นสำหรับ Embed หรือไม่
        if not title.strip() and not description.strip() and not image_url.strip():
             await interaction.response.send_message("❌ **ข้อผิดพลาด:** ประกาศต้องมีอย่างน้อย **หัวเรื่อง** หรือ **เนื้อหา** หรือ **ลิงก์รูปภาพ** อย่างใดอย่างหนึ่ง", ephemeral=True)
             return
             
        # กำหนด Title สำรองหากไม่มีการกรอก
        display_title = title.strip() if title.strip() else "📢 ข้อความประกาศจากฝ่ายประชาสัมพันธ์"

        # 1. สร้าง Embed
        embed = discord.Embed(
            title=display_title,
            description=description.strip() or discord.Embed.Empty, # ใช้ Empty หากไม่มีเนื้อหา
            color=discord.Color.red()
        )
        embed.set_footer(text=f"ประกาศโดย: {interaction.user.display_name} | เวลา: {get_bkk_time()}",
                         icon_url=interaction.user.display_avatar.url)

        # 2. ตั้งค่ารูปภาพ (ถ้ามี)
        valid_image_url = False
        if image_url.strip() and image_url.strip().startswith('http'):
            parsed_url = urllib.parse.urlparse(image_url.strip())
            if parsed_url.path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                embed.set_image(url=image_url.strip())
                valid_image_url = True

        # 3. กำหนดข้อความสำหรับ Mention
        message_content = ""
        mention_status = "" # สถานะเพื่อแจ้งผู้ใช้

        if mention_choice:
            # 3.1 ตรวจสอบ @everyone
            if mention_choice.lower() in ["@everyone", "@here"]:
                message_content = mention_choice
                mention_status = f"ทำการแท็ก: **{mention_choice}**"
            
            # 3.2 ตรวจสอบ Role ID หรือ Role Mention String
            else:
                role_id = None
                try:
                    if mention_choice.startswith('<@&') and mention_choice.endswith('>'):
                        role_id = int(mention_choice[3:-1])
                    elif mention_choice.isdigit():
                         role_id = int(mention_choice)
                except ValueError:
                    pass # ไม่ใช่ Role ID/Mention

                role = None
                if role_id:
                    role = interaction.guild.get_role(role_id)
                
                # ถ้ายังไม่เจอ ให้ลองหาจากชื่อ
                if not role:
                     role = discord.utils.get(interaction.guild.roles, name=mention_choice.lstrip('@'))
                
                if role:
                    message_content = role.mention 
                    mention_status = f"ทำการแท็ก Role: **{role.name}**"
                else:
                    message_content = "" # ไม่แท็กอะไร
                    mention_status = f"⚠️ ไม่พบ Role ที่ต้องการแท็ก: **{mention_choice}**"

        
        # 4. การตอบกลับ:
        await interaction.response.send_message("<a:45696190630e4f208144d0582a0b0414:1423939335928938506> กำลังโพสต์ประชาสัมพันธ์สาธารณะ...", ephemeral=True)

        # 4.2 ใช้ followup เพื่อส่งข้อความจริงแบบสาธารณะ
        try:
             # ส่งข้อความจริง (พร้อม embed และ mention)
             await interaction.followup.send(content=message_content, embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True, roles=True), wait=True)
        except Exception as e:
             await interaction.edit_original_response(content=f"❌ เกิดข้อผิดพลาดในการโพสต์: {e}", embeds=[])
             return
             

        # 4.3 แจ้งเตือนผู้ใช้ (Ephemeral)
        success_message = "<a:45696190630e4f208144d0582a0b0414:1423939335928938506> โพสต์ประชาสัมพันธ์สาธารณะสำเร็จแล้ว!"
        
        if mention_status:
             success_message += f"\n- {mention_status}"
             
        if image_url.strip() and not valid_image_url:
            success_message += "\n- ⚠️ ลิงก์รูปภาพไม่ถูกต้อง - โพสต์ข้อความหลักโดยไม่มีรูปภาพ"
        elif image_url.strip() and valid_image_url:
             success_message += "\n- 🖼️ โพสต์พร้อมรูปภาพ"
        
        if not title.strip() and not description.strip() and image_url.strip():
             success_message += "\n- 📢 โพสต์เฉพาะรูปภาพเท่านั้น"
        elif not title.strip() and not description.strip() and not image_url.strip():
             success_message += "\n- 📢 โพสต์เฉพาะข้อความแท็กเท่านั้น"

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
        session_data["start_time"] = get_bkk_time() # <--- แก้ไข: ใช้เวลาไทย
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

        end_time_str = get_bkk_time() # <--- แก้ไข: ใช้เวลาไทย
        current_link = session_data.get("link")
        current_message_id = session_data.get("last_message_id")
        current_participants = session_data.get("participants", [])
        current_start_time = session_data.get("start_time", "-")

        # 1. คำนวณระยะเวลา (แก้ไขให้รองรับ Timezone)
        duration_text = "-"
        try:
            # ใช้ pytz ในการสร้าง objects ที่มี timezone
            bkk_tz = pytz.timezone('Asia/Bangkok')
            
            # โหลดเวลาเริ่มและเวลาสิ้นสุดเป็น datetime object 
            # (กำหนดให้เป็นเวลาไทย)
            start_dt = bkk_tz.localize(datetime.datetime.strptime(current_start_time, "%Y-%m-%d %H:%M:%S"))
            end_dt = bkk_tz.localize(datetime.datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S"))
            
            # คำนวณ Delta
            time_difference = end_dt - start_dt
            
            duration_sec = time_difference.total_seconds()
            hours = int(duration_sec // 3600)
            minutes = int((duration_sec % 3600) // 60)
            
            # ป้องกันระยะเวลาเป็นลบ (อาจเกิดจากไฟล์ session.json ถูกแก้ไข)
            if duration_sec < 0:
                 duration_text = "❌ เวลาเริ่ม/จบ ไม่ถูกต้อง"
            else:
                 duration_text = f"{hours} ชั่วโมง {minutes} นาที"
                 
        except Exception as e:
            print(f"Error calculating duration: {e}")
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
