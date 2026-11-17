#!/usr/bin/env python3
import hashlib
import ipaddress
import json
import os
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from vtfconvert import gif_to_vtf

HOST = os.environ.get("VTF_HOST", "127.0.0.1")
PORT = int(os.environ.get("VTF_PORT", "8765"))
PUBLIC_URL = os.environ.get("VTF_PUBLIC_URL", "https://classic-box.org/vtf").rstrip("/")
STATE_DIR = os.environ.get("STATE_DIRECTORY", "/var/lib/vtf-service")
FILES_DIR = os.path.join(STATE_DIR, "files")
DEFAULT_SIZE = int(os.environ.get("VTF_SIZE", "64"))
MAX_FRAMES = int(os.environ.get("VTF_MAX_FRAMES", "64"))
MAX_BYTES = int(os.environ.get("VTF_MAX_BYTES", str(8 * 1024 * 1024)))
CACHE_DAYS = float(os.environ.get("VTF_CACHE_DAYS", "7"))
RATE_LIMIT = int(os.environ.get("VTF_RATE_LIMIT", "60"))
WORKERS = int(os.environ.get("VTF_WORKERS", "4"))
SIZES = (16, 32, 64, 128, 256)
UA = "ClassicBox-VTF-Service/1.0"

_hosts = os.environ.get("VTF_ALLOWED_HOSTS", "classic-box.org,cdn.discordapp.com,cdn.betterttv.net")
ALLOWED_HOSTS = [] if _hosts.strip() == "*" else [h.strip().lower() for h in _hosts.split(",") if h.strip()]

slots = threading.BoundedSemaphore(WORKERS)
key_locks = {}
guard = threading.Lock()
hits = {}

class ServiceError(Exception):
	def __init__(self, status, text):
		super().__init__(text)
		self.status = status
		self.text = text

class NoRedirect(urllib.request.HTTPRedirectHandler):
	def redirect_request(self, *args, **kwargs):
		return None

opener = urllib.request.build_opener(NoRedirect)

def atomic_write(path, data: bytes):
	fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
	with os.fdopen(fd, "wb") as f:
		f.write(data)
	os.chmod(tmp, 0o644)
	os.replace(tmp, path)

def rate_limited(ip):
	now = time.time()
	with guard:
		window = [t for t in hits.get(ip, []) if now - t < 60]
		window.append(now)
		hits[ip] = window
		return len(window) > RATE_LIMIT

def check_url(url):
	parts = urllib.parse.urlsplit(url)
	if parts.scheme not in ("http", "https") or not parts.hostname:
		raise ServiceError(-2, "Need to provide a url.")

	host = parts.hostname.lower()
	if ALLOWED_HOSTS and not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
		raise ServiceError(-3, "Host is not allowed.")

	port = parts.port or (443 if parts.scheme == "https" else 80)
	try:
		infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
	except socket.gaierror:
		raise ServiceError(-4, "Could not resolve host.")

	for info in infos:
		if not ipaddress.ip_address(info[4][0]).is_global:
			raise ServiceError(-3, "Host is not allowed.")

def download(url):
	for _ in range(4):
		check_url(url)
		req = urllib.request.Request(url, headers={"User-Agent": UA})
		try:
			with opener.open(req, timeout=15) as r:
				data = r.read(MAX_BYTES + 1)
		except urllib.error.HTTPError as e:
			location = e.headers.get("Location")
			if e.code in (301, 302, 303, 307, 308) and location:
				url = urllib.parse.urljoin(url, location)
				continue
			raise ServiceError(-5, f"Download failed: HTTP {e.code}.")
		except (urllib.error.URLError, OSError):
			raise ServiceError(-5, "Download failed.")

		if len(data) > MAX_BYTES:
			raise ServiceError(-6, "File is too large.")
		return data

	raise ServiceError(-5, "Too many redirects.")

def read_cached(key):
	vtf_path = os.path.join(FILES_DIR, key + ".vtf")
	meta_path = os.path.join(FILES_DIR, key + ".json")
	if not (os.path.exists(vtf_path) and os.path.exists(meta_path)):
		return None
	try:
		with open(meta_path) as f:
			return int(json.load(f)["fps"])
	except Exception:
		return None

def convert(url, size):
	key = hashlib.sha256(f"{size}\n{url}".encode()).hexdigest()[:32]

	with guard:
		lock = key_locks.setdefault(key, threading.Lock())

	with lock:
		fps = read_cached(key)
		if fps is None:
			with slots:
				data = download(url)
				try:
					vtf, fps = gif_to_vtf(data, size, MAX_FRAMES)
				except Exception:
					raise ServiceError(-7, "Could not convert the image.")

			atomic_write(os.path.join(FILES_DIR, key + ".vtf"), vtf)
			atomic_write(os.path.join(FILES_DIR, key + ".json"), json.dumps({"fps": fps}).encode())

	return {
		"status": 1,
		"status_text": "OK",
		"url": f"{PUBLIC_URL}/files/{key}.vtf",
		"frame_rate": fps,
	}

def cleanup_loop():
	while True:
		now = time.time()
		cutoff = now - CACHE_DAYS * 86400
		for f in os.listdir(FILES_DIR):
			path = os.path.join(FILES_DIR, f)
			try:
				if os.path.getmtime(path) < cutoff:
					os.remove(path)
			except OSError:
				pass

		with guard:
			for ip in [ip for ip, w in hits.items() if not w or now - w[-1] >= 60]:
				del hits[ip]
			key_locks.clear()

		time.sleep(3600)

class Handler(BaseHTTPRequestHandler):
	server_version = "vtf-service"

	def log_message(self, *args):
		pass

	def reply(self, code, payload):
		body = json.dumps(payload).encode()
		self.send_response(code)
		self.send_header("Content-Type", "application/json")
		self.send_header("Content-Length", str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def process(self, params):
		ip = self.headers.get("X-Real-IP") or self.client_address[0]
		if rate_limited(ip):
			return self.reply(429, {"status": -9, "status_text": "Too many requests."})

		url = (params.get("url") or [""])[0].strip()
		if not url:
			return self.reply(200, {"status": -2, "status_text": "Need to provide a url."})

		try:
			size = int((params.get("size") or [DEFAULT_SIZE])[0])
		except ValueError:
			size = DEFAULT_SIZE
		if size not in SIZES:
			size = DEFAULT_SIZE

		try:
			check_url(url)
			self.reply(200, convert(url, size))
		except ServiceError as e:
			self.reply(200, {"status": e.status, "status_text": e.text})
		except Exception:
			self.reply(200, {"status": -8, "status_text": "Internal error."})

	def do_GET(self):
		parts = urllib.parse.urlsplit(self.path)
		if parts.path != "/get":
			return self.reply(404, {"status": -10, "status_text": "Not found."})
		self.process(urllib.parse.parse_qs(parts.query))

	def do_POST(self):
		if urllib.parse.urlsplit(self.path).path != "/get":
			return self.reply(404, {"status": -10, "status_text": "Not found."})

		try:
			length = min(int(self.headers.get("Content-Length", "0")), 4096)
		except ValueError:
			length = 0
		body = self.rfile.read(length).decode("utf-8", "replace")
		self.process(urllib.parse.parse_qs(body))

def main():
	os.makedirs(FILES_DIR, exist_ok=True)
	threading.Thread(target=cleanup_loop, daemon=True).start()
	ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()

if __name__ == "__main__":
	main()
