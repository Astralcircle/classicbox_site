#!/usr/bin/env python3
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
GUILD_ID = os.environ["DISCORD_GUILD_ID"]
OUT_DIR = os.environ.get("EMOJI_DIR", "/var/www/emojis")
BASE_URL = os.environ.get("EMOJI_BASE_URL", "https://classic-box.org/emojis").rstrip("/")
SIZE = int(os.environ.get("EMOJI_SIZE", "64"))

API = "https://discord.com/api/v10"
UA = "DiscordRelay-EmojiSync (https://classic-box.org, 1.0)"

def http_get(url, auth=False, retries=5):
	headers = {"User-Agent": UA}
	if auth:
		headers["Authorization"] = "Bot " + TOKEN
	for attempt in range(retries):
		req = urllib.request.Request(url, headers=headers)
		try:
			with urllib.request.urlopen(req, timeout=30) as r:
				return r.read()
		except urllib.error.HTTPError as e:
			if e.code == 429:
				try:
					wait = float(json.loads(e.read()).get("retry_after", 5))
				except Exception:
					wait = 5
				time.sleep(wait + 0.5)
				continue
			raise
		except urllib.error.URLError:
			if attempt == retries - 1:
				raise
			time.sleep(3)
	raise RuntimeError("too many retries: " + url)

def fetch_emojis():
	try:
		return json.loads(http_get(f"{API}/guilds/{GUILD_ID}/emojis", auth=True))
	except urllib.error.HTTPError as e:
		if e.code not in (403, 404):
			raise
		return json.loads(http_get(f"{API}/guilds/{GUILD_ID}/preview", auth=True))["emojis"]

def atomic_write(path, data: bytes):
	fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
	with os.fdopen(fd, "wb") as f:
		f.write(data)
	os.chmod(tmp, 0o644)
	os.replace(tmp, path)

def main():
	os.makedirs(OUT_DIR, exist_ok=True)

	result = {}
	wanted_files = {"emojis.json"}

	for e in fetch_emojis():
		name = re.sub(r"\W", "_", e["name"])
		if name in result:
			name = f"{name}_{e['id']}"

		ext = "gif" if e.get("animated") else "png"
		filename = f"{name}.{ext}"
		path = os.path.join(OUT_DIR, filename)
		id_marker = path + ".id"

		up_to_date = (
			os.path.exists(path)
			and os.path.exists(id_marker)
			and open(id_marker).read() == e["id"]
		)
		if not up_to_date:
			url = f"https://cdn.discordapp.com/emojis/{e['id']}.{ext}?size={SIZE}"
			try:
				atomic_write(path, http_get(url))
				atomic_write(id_marker, e["id"].encode())
			except Exception:
				continue

		wanted_files.update((filename, filename + ".id"))
		result[name] = f"{BASE_URL}/{filename}"

	for f in os.listdir(OUT_DIR):
		if f not in wanted_files:
			os.remove(os.path.join(OUT_DIR, f))

	data = json.dumps(dict(sorted(result.items())), ensure_ascii=False, indent=2).encode("utf-8")
	json_path = os.path.join(OUT_DIR, "emojis.json")
	if not os.path.exists(json_path) or open(json_path, "rb").read() != data:
		atomic_write(json_path, data)

if __name__ == "__main__":
	main()
