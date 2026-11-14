#!/usr/bin/env python3
"""VideoGrabber — local download queue for the Chrome extension.

Listens on 127.0.0.1:PORT, accepts download jobs from the extension,
runs them one by one through yt-dlp and shows progress in a Tk window.
The listening socket doubles as the single-instance lock.
"""
import json
import os
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "VideoGrabber"
PORT = 47823
CONFIG_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.json"

CHROME_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")

DEFAULT_CONFIG = {
    "download_dir": str(Path.home() / "Downloads" / "VideoGrabber"),
    # Keyed by the host of the page the video was found on (subdomains match too).
    "site_rules": {
        "courses.theartofcoachingvolleyball.com": {
            "referer": "https://player.mediadelivery.net/",
            "user_agent": CHROME_UA,
            "headers": {"Origin": "https://player.mediadelivery.net"},
        }
    },
}

# Finder-launched apps get a bare PATH; make yt-dlp / ffmpeg findable.
os.environ["PATH"] = os.pathsep.join(
    ["/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local" / "bin"),
     os.environ.get("PATH", "/usr/bin:/bin")])


def load_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    tmp.replace(CONFIG_PATH)


def rule_for(page_url, cfg):
    host = (urlparse(page_url).hostname or "").lower()
    for site, rule in cfg.get("site_rules", {}).items():
        site = site.lower()
        if host == site or host.endswith("." + site):
            return rule
    return None


def safe_filename(title):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", title or "")
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name or "video")[:180]


def unique_base(folder, name):
    """Return folder/name (no extension) not clashing with existing files."""
    folder = Path(folder)
    candidate, n = name, 2
    while any(p.stem == candidate for p in folder.glob(glob_escape(candidate) + ".*")):
        candidate = f"{name} ({n})"
        n += 1
    return folder / candidate


def glob_escape(s):
    return re.sub(r"([\[\]*?])", r"[\1]", s)


class Job:
    _next_id = 1

    def __init__(self, url, title, page_url):
        self.id = Job._next_id
        Job._next_id += 1
        self.url = url
        self.title = title
        self.page_url = page_url
        self.status = "queued"   # queued | running | done | error | cancelled
        self.percent = 0.0
        self.info = ""
        self.output = None


class Downloader(threading.Thread):
    """Runs queued jobs strictly one at a time."""

    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.jobs = queue.Queue()
        self.proc = None
        self.current = None
        self.cancel_requested = False

    def add(self, job):
        self.jobs.put(job)

    def cancel_current(self):
        if self.proc and self.proc.poll() is None:
            self.cancel_requested = True
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)  # yt-dlp + its ffmpeg
            except ProcessLookupError:
                pass

    def run(self):
        while True:
            job = self.jobs.get()
            if job.status == "cancelled":
                continue
            self.current = job
            self.cancel_requested = False
            try:
                self.download(job)
            except Exception as e:  # keep the worker alive whatever happens
                job.status, job.info = "error", str(e)
            self.current = None
            self.proc = None
            self.app.notify(job)

    def build_cmd(self, job, out_base):
        ytdlp = shutil.which("yt-dlp")
        if not ytdlp:
            raise RuntimeError("yt-dlp не найден (brew install yt-dlp)")
        cmd = [ytdlp, "--newline", "--progress", "--no-colors", "--no-playlist",
               "--progress-template",
               "download:VGP|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
               "%(progress.total_bytes_estimate)s|%(progress.fragment_index)s|"
               "%(progress.fragment_count)s|%(progress._speed_str)s|%(progress._eta_str)s",
               "-o", str(out_base).replace("%", "%%") + ".%(ext)s",
               "--print", "after_move:VGF|%(filepath)s"]
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            cmd += ["--ffmpeg-location", ffmpeg]
        rule = rule_for(job.page_url, self.app.cfg)
        if rule:
            if rule.get("referer"):
                cmd += ["--referer", rule["referer"]]
            if rule.get("user_agent"):
                cmd += ["--user-agent", rule["user_agent"]]
            for k, v in rule.get("headers", {}).items():
                cmd += ["--add-header", f"{k}: {v}"]
        cmd.append(job.url)
        return cmd

    def download(self, job):
        folder = Path(self.app.cfg["download_dir"]).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        out_base = unique_base(folder, safe_filename(job.title))
        cmd = self.build_cmd(job, out_base)
        job.status, job.info = "running", "запуск yt-dlp…"
        self.app.notify(job)

        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, bufsize=1, start_new_session=True)
        tail = []
        for line in self.proc.stdout:
            line = line.rstrip()
            if line.startswith("VGP|"):
                self.parse_progress(job, line)
                self.app.notify(job)
            elif line.startswith("VGF|"):
                job.output = line[4:]
            elif line:
                tail = (tail + [line])[-15:]
                if "[Merger]" in line or "[FixupM3u8]" in line or "Fixup" in line:
                    job.info = "обработка ffmpeg…"
                    self.app.notify(job)
        rc = self.proc.wait()
        if self.cancel_requested:
            job.status, job.info = "cancelled", "отменено"
            for p in out_base.parent.glob(glob_escape(out_base.name) + ".*"):
                if p.name.endswith((".part", ".ytdl")) or ".part-Frag" in p.name:
                    p.unlink(missing_ok=True)
        elif rc == 0:
            job.status, job.percent = "done", 100.0
            job.info = Path(job.output).name if job.output else "готово"
        else:
            job.status = "error"
            errs = [l for l in tail if "ERROR" in l]
            job.info = (errs or tail or [f"yt-dlp завершился с кодом {rc}"])[-1]

    @staticmethod
    def parse_progress(job, line):
        def num(s):
            try:
                return float(s)
            except (TypeError, ValueError):
                return None
        _, done, total, est, fi, fc, speed, eta = (line.split("|") + [""] * 8)[:8]
        done, total, est, fi, fc = map(num, (done, total, est, fi, fc))
        if fi is not None and fc:
            job.percent = min(100.0, fi / fc * 100)
            frag = f"фрагмент {int(fi)}/{int(fc)}"
        elif done is not None and (total or est):
            job.percent = min(100.0, done / (total or est) * 100)
            frag = ""
        else:
            frag = ""
        size = f"{done / 1048576:.1f} МБ" if done else ""
        parts = [f"{job.percent:.1f}%", frag, size,
                 speed.strip() if speed and "NA" not in speed else "",
                 f"осталось {eta.strip()}" if eta and "NA" not in eta else ""]
        job.info = " · ".join(p for p in parts if p)


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self._send(204, {})

        def do_GET(self):
            if self.path == "/ping":
                self._send(200, app.status())
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"error": "bad json"})
            if self.path == "/download":
                url = (data.get("url") or "").strip()
                if not url.startswith(("http://", "https://")):
                    return self._send(400, {"error": "нет URL видео"})
                job = app.enqueue(url, data.get("title") or "video", data.get("page_url") or "")
                self._send(200, {"ok": True, "id": job.id, **app.status()})
            elif self.path == "/show":
                app.ui(app.bring_to_front)
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not found"})
    return Handler


class App:
    def __init__(self, server_socket):
        self.cfg = load_config()
        self.all_jobs = []
        self.lock = threading.Lock()
        self.ui_queue = queue.Queue()

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("760x460")
        self.root.minsize(560, 320)
        self.build_ui()

        self.worker = Downloader(self)
        self.worker.start()

        self.server = ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(self),
                                          bind_and_activate=False)
        self.server.socket.close()
        self.server.socket = server_socket
        self.server.server_activate()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.createcommand("tk::mac::Quit", self.on_close)
        self.root.createcommand("tk::mac::ReopenApplication", self.bring_to_front)
        self.root.after(100, self.pump)

    # ---------- UI ----------
    def build_ui(self):
        pad = {"padx": 10, "pady": 6}
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="Папка:").pack(side="left")
        self.dir_var = tk.StringVar(value=self.cfg["download_dir"])
        ttk.Entry(top, textvariable=self.dir_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=6)
        ttk.Button(top, text="Выбрать…", command=self.choose_dir).pack(side="left")
        ttk.Button(top, text="Открыть", command=self.open_dir).pack(side="left", padx=(6, 0))

        cur = ttk.LabelFrame(self.root, text="Текущая загрузка")
        cur.pack(fill="x", **pad)
        self.cur_title = tk.StringVar(value="—")
        self.cur_info = tk.StringVar(value="Ожидание задач от расширения")
        ttk.Label(cur, textvariable=self.cur_title, font=("Helvetica", 13, "bold")).pack(
            anchor="w", padx=8, pady=(6, 2))
        self.bar = ttk.Progressbar(cur, maximum=100, mode="determinate")
        self.bar.pack(fill="x", padx=8, pady=2)
        row = ttk.Frame(cur)
        row.pack(fill="x", padx=8, pady=(2, 8))
        ttk.Label(row, textvariable=self.cur_info).pack(side="left")
        ttk.Button(row, text="Отменить", command=self.worker_cancel).pack(side="right")

        lst = ttk.LabelFrame(self.root, text="Очередь и история")
        lst.pack(fill="both", expand=True, **pad)
        cols = ("status", "title", "info")
        self.tree = ttk.Treeview(lst, columns=cols, show="headings", height=8)
        for c, w, t in (("status", 110, "Статус"), ("title", 300, "Название"), ("info", 300, "")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, stretch=(c != "status"))
        self.tree.pack(fill="both", expand=True, padx=8, pady=8)
        self.tree.bind("<Double-1>", self.reveal_selected)
        menu = tk.Menu(self.tree, tearoff=0)
        menu.add_command(label="Убрать из очереди", command=self.remove_selected)
        menu.add_command(label="Показать в Finder", command=self.reveal_selected)
        self.tree.bind("<Button-2>", lambda e: self.popup(menu, e))
        self.tree.bind("<Control-Button-1>", lambda e: self.popup(menu, e))

    STATUS_RU = {"queued": "⏳ в очереди", "running": "⬇️ загрузка", "done": "✅ готово",
                 "error": "❌ ошибка", "cancelled": "⛔ отменено"}

    def popup(self, menu, e):
        iid = self.tree.identify_row(e.y)
        if iid:
            self.tree.selection_set(iid)
            menu.tk_popup(e.x_root, e.y_root)

    def selected_job(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return next((j for j in self.all_jobs if str(j.id) == sel[0]), None)

    def remove_selected(self):
        job = self.selected_job()
        if job and job.status == "queued":
            job.status, job.info = "cancelled", "убрано из очереди"
            self.refresh_row(job)

    def reveal_selected(self, _e=None):
        job = self.selected_job()
        if job and job.output and Path(job.output).exists():
            subprocess.run(["open", "-R", job.output])

    def choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.cfg["download_dir"], mustexist=False)
        if d:
            self.cfg["download_dir"] = d
            self.dir_var.set(d)
            save_config(self.cfg)

    def open_dir(self):
        d = Path(self.cfg["download_dir"]).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(d)])

    def worker_cancel(self):
        self.worker.cancel_current()

    def bring_to_front(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def refresh_row(self, job):
        vals = (self.STATUS_RU[job.status], job.title, "" if job.status == "running" else job.info)
        if self.tree.exists(str(job.id)):
            self.tree.item(str(job.id), values=vals)
        else:
            self.tree.insert("", "end", iid=str(job.id), values=vals)
        if job.status == "running":
            self.cur_title.set(job.title)
            self.cur_info.set(job.info)
            self.bar["value"] = job.percent
        elif self.worker.current is None and job.status in ("done", "error", "cancelled"):
            self.cur_title.set("—" if job.status != "error" else f"Ошибка: {job.title}")
            self.cur_info.set(job.info if job.status == "error" else "Ожидание задач от расширения")
            self.bar["value"] = 0

    # ---------- thread bridge ----------
    def ui(self, fn):
        self.ui_queue.put(fn)

    def notify(self, job):
        self.ui(lambda: self.refresh_row(job))

    def pump(self):
        try:
            while True:
                self.ui_queue.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    # ---------- API ----------
    def enqueue(self, url, title, page_url):
        job = Job(url, title, page_url)
        with self.lock:
            self.all_jobs.append(job)
        self.worker.add(job)
        self.notify(job)
        return job

    def status(self):
        cur = self.worker.current
        queued = sum(1 for j in self.all_jobs if j.status == "queued")
        return {"app": APP_NAME, "ok": True, "download_dir": self.cfg["download_dir"],
                "queued": queued,
                "current": {"title": cur.title, "percent": round(cur.percent, 1),
                            "info": cur.info} if cur else None}

    def on_close(self):
        busy = self.worker.current or any(j.status == "queued" for j in self.all_jobs)
        if busy and not messagebox.askyesno(APP_NAME, "Есть незавершённые загрузки. Выйти?"):
            return
        self.worker.cancel_current()
        self.server.shutdown()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def acquire_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", PORT))
        return s
    except OSError:
        s.close()
        return None


def main():
    sock = acquire_port()
    if sock is None:
        # Already running: ask the existing instance to show itself, then quit.
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}/show", data=b"{}", method="POST")
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass
        sys.exit(0)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    App(sock).run()


if __name__ == "__main__":
    main()
