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
import urllib.parse
import datetime
import pytz

# โหลด Environment Variables
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DASHBOARD_CHANNEL_ID = int(os.getenv("DASHBOARD_CHANNEL_ID"))
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")

ALLOWED_ANNOUNCER_ROLES = [
    1423975320821829683
]

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
    if not GITHUB_WEBHOOK_SECRET:
        print("ERROR: GITHUB_WEBHOOK_SECRET is not set.")
        return False
    mac = hmac.new(GITHUB_WEBHOOK_SECRET.encode(), msg=request_body, digestmod=hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    return hmac.compare_digest(expected, signature)

async def update_github_embed(payload, bot_client):
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
    bkk_timezone = pytz.timezone('Asia/Bangkok')
    now = datetime.datetime.now(bkk_timezone)
    return now.strftime("%Y-%m-%d %H:%M:%S")

# --------------------------------------------------------------------------------
## Aiohttp application setup (Webhook Server)
# --------------------------------------------------------------------------------
webhook_app = web.Application()

async def handle_webhook(request):
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

webhook_app.router.add_post("/webhook", handle_webhook)

async def start_webhook_server():
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
    bot.loop.create_task(start_webhook_server())

# --------------------------------------------------------------------------------
## Slash Command: /announce
# --------------------------------------------------------------------------------
def is_announcer(interaction: discord.Interaction) -> bool:
    if interaction.guild and interaction.user.id == interaction.guild.owner_id:
        return True
    if ALLOWED_ANNOUNCER_ROLES and interaction.guild:
        user_role_ids = [role.id for role in interaction.user.roles]
        if any(role_id in user_role_ids for role_id in ALLOWED_ANNOUNCER_ROLES):
            return True
    return False

class AnnouncementModal(discord.ui.Modal, title='📝 สร้างข้อความประชาสัมพันธ์'):
    title_input = discord.ui.TextInput(label='หัวเรื่อง (Title)', placeholder='สรุป Live Session / อัปเดตแพตช์ใหม่', max_length=256, required=True)
    description_input = discord.ui.TextInput(label='เนื้อหา (รองรับ Markdown)', placeholder='กรอกเนื้อหารายละเอียดทั้งหมดที่นี่...', style=discord.TextStyle.paragraph, required=True)
    image_url_input = discord.ui.TextInput(label='ลิงก์รูปภาพ (Image URL - ไม่บังคับ)', placeholder='ต้องเป็นลิงก์ที่ลงท้าย .png, .jpg, .gif, .webp', max_length=2000, required=False)
    mention_input = discord.ui.TextInput(label='แท็กใคร? (@everyone, @here หรือ Discord ID)', placeholder='ว่างไว้ = ไม่แท็กใคร', max_length=100, required=False)

    async def on_submit(self, interaction: discord.Interaction):
        title = self.title_input.value
        description = self.description_input.value
        image_url = self.image_url_input.value
        mention_text = self.mention_input.value.strip()

        embed = discord.Embed(title=title, description=description, color=discord.Color.red())
        embed.set_footer(text=f"ประกาศโดย: {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)

        if image_url and image_url.startswith("http"):
            parsed_url = urllib.parse.urlparse(image_url)
            if parsed_url.path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                embed.set_image(url=image_url)

        content = ""
        if mention_text.lower() == "@everyone":
            content = "@everyone"
        elif mention_text.lower() == "@here":
            content = "@here"
        elif mention_text.isdigit():
            content = f"<@{mention_text}>"

        await interaction.response.send_message("<a:1249347622158860308:1422185419491246101> กำลังโพสต์ประชาสัมพันธ์...", ephemeral=True)
        await interaction.followup.send(content=content, embed=embed)
        await interaction.edit_original_response("<a:45696190630e4f208144d0582a0b0414:1423939335928938506> โพสต์ประชาสัมพันธ์สำเร็จแล้ว!")

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await interaction.followup.send(f'❌ เกิดข้อผิดพลาด: {error}', ephemeral=True)

@bot.tree.command(name="announce", description="📢 สร้างข้อความประชาสัมพันธ์ (จำกัดสิทธิ์)")
@app_commands.check(is_announcer)
async def announce_command(interaction: discord.Interaction):
    await interaction.response.send_modal(AnnouncementModal())

@announce_command.error
async def announce_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้คำสั่งนี้.", ephemeral=True)
    else:
        print(f"Error in announce_command: {error}")
        await interaction.response.send_message("❌ เกิดข้อผิดพลาด.", ephemeral=True)

# --------------------------------------------------------------------------------
## Slash Command: /session
# --------------------------------------------------------------------------------
class SessionAction(discord.app_commands.Choice):
    def __init__(self, name: str, value: str):
        super().__init__(name=name, value=value)

@bot.tree.command(name="session", description="▶️ จัดการ Live Share Session ในช่องทำงานเป็นทีม")
@app_commands.describe(action="เลือกคำสั่ง: start, status หรือ end", link="ลิงก์ Live Share (ใช้เฉพาะกับ action: start)")
@app_commands.choices(action=[
    SessionAction(name="▶️ เริ่ม Live Share Session", value="start"),
    SessionAction(name="ℹ️ แสดงสถานะ Session ปัจจุบัน", value="status"),
    SessionAction(name="⏹️ ปิด Session และคำนวณเวลา", value="end")
])
async def session_command(interaction: discord.Interaction, action: str, link: str = None):
    user_name = interaction.user.display_name
    if interaction.channel_id != DASHBOARD_CHANNEL_ID:
        await interaction.response.send_message("❌ คำสั่งนี้ใช้ได้เฉพาะช่อง #live-share-dashboard เท่านั้น", ephemeral=True)
        return
    if action == "start":
        if not link:
            await interaction.response.send_message("❌ โปรดใส่ลิงก์ Live Share", ephemeral=True)
            return
        session_data["link"] = link
        session_data["participants"] = [user_name]
        session_data["start_time"] = get_bkk_time()
        session_data["end_time"] = None
        session_data["last_message_id"] = None
        with open("session.json", "w") as f:
            json.dump(session_data, f)
        ephemeral_message = f"<a:45696190630e4f208144d0582a0b0414:1423939335928938506> **Session เริ่มต้นแล้ว!**\nโฮสต์: {user_name}"
        await interaction.response.send_message(ephemeral_message, ephemeral=True)
        embed = discord.Embed(title="<a:67c3e29969174247b000f7c7318660f:1423939328928780338> VS Code Live Share Session Started! <a:67c3e29969174247b000f7c7318660f:1423939328928780338>", description="Session เริ่มขึ้นแล้ว! กดปุ่มเพื่อเข้าร่วม", color=0x3498db)
        embed.add_field(name="ผู้เริ่ม Session", value=user_name, inline=True)
        embed.add_field(name="เวลาเริ่ม", value=session_data["start_time"], inline=True)
        embed.add_field(name="ผู้เข้าร่วมปัจจุบัน", value=", ".join(session_data["participants"]), inline=False)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="🖱️: ̗̀➛ เข้าร่วม Session (LIVE)", url=link, style=discord.ButtonStyle.green))
        sent_message = await interaction.followup.send(embed=embed, view=view, wait=True)
        session_data["last_message_id"] = sent_message.id
        with open("session.json", "w") as f:
            json.dump(session_data, f)
    elif action == "status":
        if not session_data.get("link"):
            await interaction.response.send_message("❌ ไม่มี Live Share Session ที่กำลังทำงานอยู่", ephemeral=True)
            return
        embed = discord.Embed(title="<a:1249347622158860308:1422185419491246101> สถานะ Live Share Session ปัจจุบัน", description=f"<a:2a3404eb19f54b10b16e83768f5937ae:1423939322947829841> Session กำลังทำงานอยู่ (จัดการโดย {user_name})", color=0xf39c12)
        embed.add_field(name="เวลาเริ่ม", value=session_data.get("start_time","-"), inline=True)
        embed.add_field(name="ผู้เข้าร่วม", value=", ".join(session_data.get("participants",[])) or "(ยังไม่มี)", inline=False)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="🔗 ลิงก์ Session ปัจจุบัน", url=session_data.get('link','-'), style=discord.ButtonStyle.green))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    elif action == "end":
        if not session_data.get("link"):
            await interaction.response.send_message("❌ ไม่มี Live Share Session ที่จะให้ปิด", ephemeral=True)
            return
        end_time_str = get_bkk_time()
        current_link = session_data.get("link")
        current_message_id = session_data.get("last_message_id")
        current_participants = session_data.get("participants", [])
        current_start_time = session_data.get("start_time", "-")
        duration_text = "-"
        try:
            bkk_tz = pytz.timezone('Asia/Bangkok')
            start_dt = bkk_tz.localize(datetime.datetime.strptime(current_start_time, "%Y-%m-%d %H:%M:%S"))
            end_dt = bkk_tz.localize(datetime.datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S"))
            delta = end_dt - start_dt
            if delta.total_seconds() < 0:
                duration_text = "❌ เวลาเริ่ม/จบ ไม่ถูกต้อง"
            else:
                hours = int(delta.total_seconds() // 3600)
                minutes = int((delta.total_seconds() % 3600) // 60)
                duration_text = f"{hours} ชั่วโมง {minutes} นาที"
        except Exception as e:
            print(f"Error calculating duration: {e}")
        session_data.clear()
        with open("session.json", "w") as f:
            json.dump(session_data, f)
        ephemeral_message = f"<a:45696190630e4f208144d0582a0b0414:1423939335928938506> **Session ถูกปิดแล้ว!**\nผู้ปิด Session: {user_name}"
        await interaction.response.send_message(ephemeral_message, ephemeral=True)
        embed = discord.Embed(title="<a:810020134865338368:1423938901671804968> Live Share Session Ended", description="Session สิ้นสุดลงแล้ว", color=0xe74c3c)
        embed.add_field(name="เวลาเริ่ม", value=current_start_time, inline=True)
        embed.add_field(name="เวลาสิ้นสุด", value=end_time_str, inline=True)
        embed.add_field(name="ระยะเวลา", value=duration_text, inline=True)
        embed.add_field(name="ผู้เข้าร่วม", value=", ".join(current_participants) or "(ไม่มี)", inline=False)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="🔗 ลิงก์ Session ที่ผ่านมา", url=current_link, style=discord.ButtonStyle.secondary))
        await interaction.followup.send(embed=embed, view=view)
        if current_message_id:
            try:
                channel_obj = bot.get_channel(DASHBOARD_CHANNEL_ID)
                if channel_obj:
                    old_message = await channel_obj.fetch_message(current_message_id)
                    old_embed = old_message.embeds[0]
                    old_embed.title = "<a:45696190630e4f208144d0582a0b0414:1423939335928938506> VS Code Live Share Session Started! (Finished)"
                    old_embed.description = "Session นี้สิ้นสุดแล้ว ดูสรุปด้านล่าง"
                    await old_message.edit(embed=old_embed, view=None)
            except discord.NotFound:
                print(f"Warning: Original START message with ID {current_message_id} not found.")

# --------------------------------------------------------------------------------
## Run Bot
# --------------------------------------------------------------------------------
bot.run(TOKEN)
