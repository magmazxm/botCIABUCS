from aiohttp import web
import hmac
import hashlib
import json
import os
import asyncio
import discord

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DASHBOARD_CHANNEL_ID = int(os.getenv("DASHBOARD_CHANNEL_ID"))
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")

intents = discord.Intents.default()
bot = discord.Client(intents=intents)

# โหลด session info
try:
    with open("session.json", "r") as f:
        session_data = json.load(f)
except FileNotFoundError:
    session_data = {}

# -------- Helper --------
def verify_signature(request_body, signature):
    mac = hmac.new(GITHUB_WEBHOOK_SECRET.encode(), msg=request_body, digestmod=hashlib.sha256)
    expected = "sha256=" + mac.hexdigest()
    return hmac.compare_digest(expected, signature)

async def update_github_embed(payload):
    await bot.wait_until_ready()
    channel = bot.get_channel(DASHBOARD_CHANNEL_ID)
    embed = discord.Embed(title="📦 GitHub Repo Status", color=0x3498db)

    repo_name = payload["repository"]["name"]
    branch = payload["ref"].split("/")[-1]
    last_commit = payload["head_commit"]["message"]
    author = payload["head_commit"]["author"]["name"]

    embed.add_field(name="Repo", value=repo_name, inline=False)
    embed.add_field(name="Branch", value=branch, inline=True)
    embed.add_field(name="Last commit", value=f"📝 {last_commit} by {author}", inline=True)
    embed.add_field(name="PRs open", value="🔄 2", inline=True)      # placeholder
    embed.add_field(name="Issues open", value="⚠️ 3", inline=True)   # placeholder

    # ปุ่มลิงก์สวย ๆ
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="กดตรงนี้", url=payload["repository"]["html_url"], style=discord.ButtonStyle.link))
    await channel.send(embed=embed, view=view)

# -------- Aiohttp server สำหรับ GitHub webhook --------
async def handle(request):
    body = await request.read()
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_signature(body, signature):
        return web.Response(status=401, text="Invalid signature")

    event = request.headers.get("X-GitHub-Event")
    payload = json.loads(body)

    if event == "push":
        asyncio.create_task(update_github_embed(payload))

    return web.Response(text="OK")

app = web.Application()
app.router.add_post("/webhook", handle)

def run_webhook_server():
    web.run_app(app, port=5000)

if __name__ == "__main__":
    run_webhook_server()
