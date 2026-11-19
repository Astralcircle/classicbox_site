#!/usr/bin/env python3
import hmac
import json
import os
import secrets
import string
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("IMG_HOST", "127.0.0.1")
PORT = int(os.environ.get("IMG_PORT", "8766"))
PUBLIC_URL = os.environ.get("IMG_PUBLIC_URL", "https://classic-box.org/img").rstrip("/")
ID_LENGTH = 8
ALPHABET = string.ascii_letters + string.digits
STATE_DIR = os.environ.get("STATE_DIRECTORY", "/var/lib/image-storage")
FILES_DIR = os.path.join(STATE_DIR, "files")
TMP_DIR = os.path.join(STATE_DIR, "tmp")
KEY_FILE = os.path.join(STATE_DIR, "key.json")
REGISTER_IPS = {ip.strip() for ip in os.environ.get("IMG_REGISTER_IPS", "194.147.90.27").split(",") if ip.strip()}
KEY_TTL = int(os.environ.get("IMG_KEY_TTL", "86400"))
IMAGE_TTL = int(os.environ.get("IMG_TTL", "86400"))
MAX_TOTAL = int(os.environ.get("IMG_MAX_TOTAL", str(1024 ** 3)))
MAX_IMAGE = int(os.environ.get("IMG_MAX_BYTES", str(5 * 1024 * 1024)))
RATE_LIMIT = int(os.environ.get("IMG_RATE_LIMIT", "20"))

state_lock = threading.Lock()
storage_lock = threading.Lock()
current = None
hits = {}

def remove(path):
	try:
		os.remove(path)
	except OSError:
		pass

def atomic_write(path, data: bytes, mode):
	fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
	with os.fdopen(fd, "wb") as f:
		f.write(data)
	os.chmod(tmp, mode)
	os.replace(tmp, path)

def detect_ext(data):
	if data.startswith(b"\x89PNG\r\n\x1a\n"):
		return "png"
	if data.startswith(b"\xff\xd8\xff"):
		return "jpg"
	return None

def load_key():
	global current
	try:
		with open(KEY_FILE) as f:
			saved = json.load(f)
		if time.time() < saved["expires"]:
			current = {"key": saved["key"], "expires": saved["expires"]}
	except Exception:
		current = None

def register_key():
	global current
	key = secrets.token_urlsafe(32)
	expires = int(time.time()) + KEY_TTL
	with state_lock:
		current = {"key": key, "expires": expires}
		atomic_write(KEY_FILE, json.dumps(current).encode(), 0o600)
	return key, expires

def key_valid(token):
	with state_lock:
		active = current
	if not active or time.time() >= active["expires"]:
		return False
	return hmac.compare_digest(token.encode("utf-8", "replace"), active["key"].encode())

def rate_limited(ip):
	now = time.time()
	with state_lock:
		window = [t for t in hits.get(ip, []) if now - t < 60]
		window.append(now)
		hits[ip] = window
		return len(window) > RATE_LIMIT

def enforce_limits():
	with storage_lock:
		now = time.time()
		entries = []
		for entry in os.scandir(FILES_DIR):
			try:
				st = entry.stat()
			except OSError:
				continue
			if now - st.st_mtime > IMAGE_TTL:
				remove(entry.path)
				continue
			entries.append((st.st_mtime, st.st_size, entry.path))

		total = sum(e[1] for e in entries)
		entries.sort()
		for _, size, path in entries:
			if total <= MAX_TOTAL:
				break
			remove(path)
			total -= size

def new_name(ext):
	while True:
		name = "".join(secrets.choice(ALPHABET) for _ in range(ID_LENGTH)) + "." + ext
		if not os.path.exists(os.path.join(FILES_DIR, name)):
			return name

def store(data, ext):
	name = new_name(ext)
	fd, tmp = tempfile.mkstemp(dir=TMP_DIR)
	with os.fdopen(fd, "wb") as f:
		f.write(data)
	os.chmod(tmp, 0o644)
	os.replace(tmp, os.path.join(FILES_DIR, name))
	enforce_limits()
	return name

def cleanup_loop():
	while True:
		time.sleep(60)
		enforce_limits()
		now = time.time()
		with state_lock:
			for ip in [ip for ip, w in hits.items() if not w or now - w[-1] >= 60]:
				del hits[ip]

class Handler(BaseHTTPRequestHandler):
	server_version = "image-storage"

	def log_message(self, *args):
		pass

	def reply(self, code, payload):
		body = json.dumps(payload).encode()
		self.send_response(code)
		self.send_header("Content-Type", "application/json")
		self.send_header("Content-Length", str(len(body)))
		self.send_header("Cache-Control", "no-store")
		self.end_headers()
		self.wfile.write(body)

	def do_GET(self):
		self.reply(404, {"error": "not found"})

	def do_POST(self):
		path = self.path.split("?", 1)[0]
		ip = self.headers.get("X-Real-IP") or self.client_address[0]

		if rate_limited(ip):
			return self.reply(429, {"error": "too many requests"})

		if path == "/register":
			return self.register(ip)
		if path == "/upload":
			return self.upload()
		self.reply(404, {"error": "not found"})

	def register(self, ip):
		if ip not in REGISTER_IPS:
			return self.reply(403, {"error": "forbidden"})

		key, expires = register_key()
		self.reply(200, {"key": key, "expires": expires, "ttl": KEY_TTL})

	def upload(self):
		if not key_valid(self.headers.get("X-Key", "")):
			return self.reply(401, {"error": "invalid key"})

		try:
			length = int(self.headers.get("Content-Length", ""))
		except ValueError:
			return self.reply(411, {"error": "content-length required"})

		if length <= 0:
			return self.reply(400, {"error": "empty body"})
		if length > MAX_IMAGE:
			return self.reply(413, {"error": "image is too large"})

		data = self.rfile.read(length)
		if len(data) != length:
			return self.reply(400, {"error": "incomplete body"})

		ext = detect_ext(data)
		if not ext:
			return self.reply(415, {"error": "only png and jpg are allowed"})

		name = store(data, ext)
		self.reply(200, {"url": f"{PUBLIC_URL}/files/{name}", "expires": int(time.time()) + IMAGE_TTL})

def main():
	os.makedirs(FILES_DIR, exist_ok=True)
	os.makedirs(TMP_DIR, exist_ok=True)
	for f in os.listdir(TMP_DIR):
		remove(os.path.join(TMP_DIR, f))

	load_key()
	enforce_limits()
	threading.Thread(target=cleanup_loop, daemon=True).start()
	ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()

if __name__ == "__main__":
	main()
