"""Local video/stream downloader + cutter.

Run:  .venv\\Scripts\\python.exe app.py   (or double-click run.bat)
Then open http://127.0.0.1:8765
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
BIN = ROOT / "bin"
FOLDERS = {"downloads": ROOT / "downloads", "clips": ROOT / "clips"}
for p in FOLDERS.values():
    p.mkdir(exist_ok=True)

EXE = ".exe" if os.name == "nt" else ""
# put the portable ffmpeg on PATH too: some yt-dlp code paths (section downloads) only look there
os.environ["PATH"] = str(BIN) + os.pathsep + os.environ.get("PATH", "")
FFMPEG = str(BIN / f"ffmpeg{EXE}") if (BIN / f"ffmpeg{EXE}").exists() else shutil.which("ffmpeg")
FFPROBE = str(BIN / f"ffprobe{EXE}") if (BIN / f"ffprobe{EXE}").exists() else shutil.which("ffprobe")
if not FFMPEG or not FFPROBE:
    sys.exit("ffmpeg/ffprobe not found. Run setup.bat (puts them in .\\bin).")

MEDIA_EXT = {".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3", ".opus", ".ts", ".flv", ".m4v", ".aac", ".wav"}
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

QUALITY = {
    "best": "bv*+ba/b",
    "2160": "bv*[height<=2160]+ba/b[height<=2160]/b",
    "1440": "bv*[height<=1440]+ba/b[height<=1440]/b",
    "1080": "bv*[height<=1080]+ba/b[height<=1080]/b",
    "720": "bv*[height<=720]+ba/b[height<=720]/b",
    "480": "bv*[height<=480]+ba/b[height<=480]/b",
    # H.264 + AAC: plays everywhere (old TVs, phones, editors) but max ~1080p on YouTube
    "compat": "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/b[vcodec^=avc1]/bv*+ba/b",
    "audio": "ba[ext=m4a]/ba/b",
}


# ---------------------------------------------------------------- jobs
@dataclass
class Job:
    kind: str                       # "download" | "cut"
    title: str
    params: dict
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: str = "queued"          # queued | running | done | error | cancelled
    progress: float = 0.0
    detail: str = ""
    error: str = ""
    output: list = field(default_factory=list)
    created: float = field(default_factory=time.time)
    _cancel: bool = False
    _proc: subprocess.Popen | None = None

    def public(self):
        keys = ("kind", "title", "id", "status", "progress", "detail", "error", "created")
        d = {k: getattr(self, k) for k in keys}
        d["output"] = list(self.output)
        d["url"] = self.params.get("url", "")
        d["section_start"] = self.params.get("section_start")
        d["section_end"] = self.params.get("section_end")
        return d


JOBS: dict[str, Job] = {}
LOCK = threading.Lock()
DL_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dl")
CUT_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cut")


class Cancelled(Exception):
    pass


def fmt_bytes(n):
    if not n:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def fmt_time(s):
    if s is None:
        return "?"
    s = int(s)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def ts_label(s: float) -> str:
    s = int(s)
    return f"{s // 3600:02d}h{s // 60 % 60:02d}m{s % 60:02d}s"


# ---------------------------------------------------------------- download
class _Logger:
    def __init__(self, job):
        self.job = job

    def debug(self, msg):
        if msg.startswith("[download] Destination") or msg.startswith("[Merger]"):
            self.job.detail = msg.split("] ", 1)[-1][:200]

    info = debug

    def warning(self, msg):
        pass

    def error(self, msg):
        self.job.error = strip_ansi(msg)[:500]


def run_download(job: Job):
    if job._cancel:
        job.status = "cancelled"
        return
    p = job.params
    job.status = "running"
    job.detail = "Fetching info…"

    def hook(d):
        if job._cancel:
            raise Cancelled()
        if d["status"] == "downloading":
            done = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if d.get("fragment_count"):
                job.progress = 100 * (d.get("fragment_index") or 0) / d["fragment_count"]
            elif total:
                job.progress = 100 * done / total
            fmt = d.get("info_dict", {}).get("format_id", "")
            job.detail = (f"[{fmt}] {fmt_bytes(done)} / {fmt_bytes(total)} · "
                          f"{fmt_bytes(d.get('speed'))}/s · ETA {fmt_time(d.get('eta'))}")
        elif d["status"] == "finished":
            job.detail = "Post-processing (merging audio/video)…"

    def pp_hook(d):
        if job._cancel:
            raise Cancelled()
        if d["status"] == "started":
            job.detail = f"Post-processing: {d.get('postprocessor')}…"

    audio = p.get("quality") == "audio"
    opts = {
        "format": QUALITY.get(p.get("quality") or "best", QUALITY["best"]),
        "outtmpl": str(FOLDERS["downloads"] / "%(title).120B [%(id)s].%(ext)s"),
        "ffmpeg_location": str(Path(FFMPEG).parent),
        "noplaylist": not p.get("playlist"),
        "windowsfilenames": True,
        "continuedl": True,                     # resume .part files
        "concurrent_fragment_downloads": 8,     # parallel HLS/DASH fragments
        "retries": 15,
        "fragment_retries": 30,
        "skip_unavailable_fragments": False,
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
        "logger": _Logger(job),
        "js_runtimes": {"deno": {}, "node": {}},  # needed for YouTube signature solving
        "live_from_start": bool(p.get("live_from_start")),
    }
    if not audio:
        opts["merge_output_format"] = "mp4"  # ffmpeg muxes VP9/AV1/Opus into MP4 fine; all browsers play it
    if p.get("cookies_browser"):
        opts["cookiesfrombrowser"] = (p["cookies_browser"],)
    if p.get("section_start") is not None or p.get("section_end") is not None:
        start = p.get("section_start") or 0
        end = p.get("section_end") or float("inf")
        opts["download_ranges"] = yt_dlp.utils.download_range_func(None, [(start, end)])
        opts["force_keyframes_at_cuts"] = bool(p.get("precise_section"))
        span = f"{ts_label(start)}-{'end' if end == float('inf') else ts_label(end)}"
        tag = f"Part {p['part_label']} ({span})" if p.get("part_label") else f"part {span}"
        opts["outtmpl"] = str(FOLDERS["downloads"] / f"%(title).120B [%(id)s] {tag}.%(ext)s")

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(p["url"], download=True)
        entries = info.get("entries") or [info]
        for e in entries:
            if not e:
                continue
            job.title = e.get("title") or job.title
            for rd in e.get("requested_downloads") or []:
                fp = rd.get("filepath")
                if fp:
                    job.output.append(Path(fp).name)
        job.progress = 100
        job.status = "done"
        job.detail = "Finished"
    except (Cancelled, yt_dlp.utils.DownloadCancelled):
        job.status = "cancelled"
        job.detail = "Cancelled — partial data kept, Retry resumes"
    except Exception as e:  # noqa: BLE001
        if job._cancel:
            job.status = "cancelled"
            job.detail = "Cancelled — partial data kept, Retry resumes"
        else:
            job.status = "error"
            job.error = job.error or str(e)[:500]


# ---------------------------------------------------------------- cutting
def probe(path: Path) -> dict:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height",
         "-of", "json", str(path)],
        capture_output=True, text=True, creationflags=NO_WINDOW)
    import json
    try:
        return json.loads(out.stdout)
    except ValueError:
        return {}


def ffmpeg_run(job: Job, args: list[str], duration: float, done_before: float, total: float):
    cmd = [FFMPEG, "-hide_banner", "-y", "-nostats", "-progress", "pipe:1", *args]
    job._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 creationflags=NO_WINDOW)
    err_lines: list[str] = []
    t = threading.Thread(target=lambda: err_lines.extend(job._proc.stderr), daemon=True)
    t.start()
    for line in job._proc.stdout:
        if line.startswith("out_time_us="):
            try:
                cur = int(line.split("=")[1]) / 1e6
                job.progress = min(99.9, 100 * (done_before + min(cur, duration)) / max(total, 0.001))
            except ValueError:
                pass
    rc = job._proc.wait()
    t.join(timeout=2)
    if job._cancel:
        raise Cancelled()
    if rc != 0:
        raise RuntimeError("ffmpeg failed: " + "".join(err_lines[-6:])[-500:])


def run_cut(job: Job):
    if job._cancel:
        job.status = "cancelled"
        return
    p = job.params
    job.status = "running"
    src = safe_path(p["folder"], p["name"])
    segs = sorted(((float(s["start"]), float(s["end"])) for s in p["segments"]), key=lambda x: x[0])
    precise = p["mode"] == "precise"
    has_video = any(s.get("codec_type") == "video" for s in probe(src).get("streams", []))
    ext = (".mp4" if has_video else ".m4a") if precise else src.suffix.lower()
    base = re.sub(r'[<>:"/\\|?*]', "_", (p.get("name_out") or src.stem).strip())[:120] or "clip"
    total = sum(e - s for s, e in segs)
    tmpdir = FOLDERS["clips"] / f".tmp_{job.id}"
    outputs: list[Path] = []

    def seg_args(s, e, out: Path):
        a = ["-ss", f"{s:.3f}", "-i", str(src), "-t", f"{e - s:.3f}", "-map", "0:v:0?", "-map", "0:a?"]
        if precise:
            if has_video:
                a += ["-c:v", "libx264", "-preset", p.get("preset") or "veryfast",
                      "-crf", str(p.get("crf") or 18), "-pix_fmt", "yuv420p"]
            a += ["-c:a", "aac", "-b:a", "192k"]
        else:
            a += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
        if out.suffix.lower() in (".mp4", ".mov", ".m4v", ".m4a"):
            a += ["-movflags", "+faststart"]
        return a + [str(out)]

    def unique(path: Path) -> Path:
        i, cand = 2, path
        while cand.exists():
            cand = path.with_name(f"{path.stem} ({i}){path.suffix}")
            i += 1
        return cand

    try:
        done = 0.0
        merge = p.get("merge") and len(segs) > 1
        if merge:
            tmpdir.mkdir(exist_ok=True)
        for i, (s, e) in enumerate(segs):
            job.detail = f"Cutting segment {i + 1}/{len(segs)} ({'precise' if precise else 'fast'})"
            out = (tmpdir / f"seg{i:03d}{ext}") if merge else unique(
                FOLDERS["clips"] / f"{base}_{ts_label(s)}-{ts_label(e)}{ext}")
            ffmpeg_run(job, seg_args(s, e, out), e - s, done, total)
            outputs.append(out)
            done += e - s
        if merge:
            job.detail = "Joining segments…"
            lst = tmpdir / "list.txt"
            lst.write_text("".join(f"file '{o.as_posix()}'\n" for o in outputs), encoding="utf-8")
            final = unique(FOLDERS["clips"] / f"{base}_merged{ext}")
            args = ["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy"]
            if ext in (".mp4", ".m4a"):
                args += ["-movflags", "+faststart"]
            ffmpeg_run(job, args + [str(final)], total, 0, total)
            outputs = [final]
        job.output = [o.name for o in outputs]
        job.progress = 100
        job.status = "done"
        job.detail = f"Saved {len(outputs)} file(s) to clips/"
    except Cancelled:
        job.status = "cancelled"
        job.detail = "Cancelled"
    except Exception as e:  # noqa: BLE001
        job.status = "error"
        job.error = str(e)[:500]
    finally:
        if tmpdir.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------- api
app = FastAPI(title="Vid Downloader")


def safe_path(folder: str, name: str) -> Path:
    base = FOLDERS.get(folder)
    if base is None or not name or "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "bad path")
    path = (base / name).resolve()
    if path.parent != base.resolve() or not path.is_file():
        raise HTTPException(404, "not found")
    return path


class DownloadReq(BaseModel):
    url: str
    quality: str = "best"
    section_start: float | None = None
    section_end: float | None = None
    precise_section: bool = False
    live_from_start: bool = False
    playlist: bool = False
    cookies_browser: str | None = None
    part_label: str | None = None   # e.g. "2 of 5" when downloading a long video in parts


class InfoReq(BaseModel):
    url: str
    cookies_browser: str | None = None


class Segment(BaseModel):
    start: float
    end: float


class CutReq(BaseModel):
    folder: str
    name: str
    segments: list[Segment]
    mode: str = "fast"
    merge: bool = False
    name_out: str | None = None
    crf: int = 18
    preset: str = "veryfast"


def get_job(job_id: str) -> Job:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


def submit(job: Job):
    with LOCK:
        JOBS[job.id] = job
    (DL_POOL if job.kind == "download" else CUT_POOL).submit(run_download if job.kind == "download" else run_cut, job)
    return job.public()


@app.post("/api/download")
def api_download(req: DownloadReq):
    if not re.match(r"^https?://", req.url.strip()):
        raise HTTPException(400, "URL must start with http:// or https://")
    req.url = req.url.strip()
    return submit(Job("download", req.url, req.model_dump()))


@app.post("/api/info")
def api_info(req: InfoReq):
    """Read title/duration without downloading (used by 'Split into parts')."""
    if not re.match(r"^https?://", req.url.strip()):
        raise HTTPException(400, "URL must start with http:// or https://")
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True,
            "js_runtimes": {"deno": {}, "node": {}}}
    if req.cookies_browser:
        opts["cookiesfrombrowser"] = (req.cookies_browser,)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(req.url.strip(), download=False, process=False)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, strip_ansi(str(e))[:400])
    if info.get("_type") == "playlist":
        raise HTTPException(400, "This is a playlist. Use a link to a single video to split it into parts.")
    return {"title": info.get("title"), "duration": info.get("duration"),
            "is_live": bool(info.get("is_live")), "id": info.get("id")}


@app.post("/api/cut")
def api_cut(req: CutReq):
    safe_path(req.folder, req.name)
    segs = [s for s in req.segments if s.end - s.start > 0.05]
    if not segs:
        raise HTTPException(400, "No valid segments (end must be after start)")
    if req.mode not in ("fast", "precise"):
        raise HTTPException(400, "bad mode")
    params = req.model_dump()
    params["segments"] = [s.model_dump() for s in segs]
    return submit(Job("cut", f"Cut: {req.name}", params))


@app.get("/api/jobs")
def api_jobs():
    with LOCK:
        return sorted((j.public() for j in JOBS.values()), key=lambda j: -j["created"])


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel(job_id: str):
    job = get_job(job_id)
    job._cancel = True
    if job._proc and job._proc.poll() is None:
        job._proc.kill()
    if job.status == "queued":
        job.status = "cancelled"
    return job.public()


@app.post("/api/jobs/{job_id}/retry")
def api_retry(job_id: str):
    old = get_job(job_id)
    with LOCK:
        JOBS.pop(job_id, None)
    return submit(Job(old.kind, old.title, old.params))


@app.delete("/api/jobs/{job_id}")
def api_remove_job(job_id: str):
    with LOCK:
        job = JOBS.get(job_id)
        if job and job.status not in ("queued", "running"):
            JOBS.pop(job_id)
    return {"ok": True}


@app.get("/api/files")
def api_files():
    res = []
    for folder, base in FOLDERS.items():
        for f in base.iterdir():
            if f.is_file() and f.suffix.lower() in MEDIA_EXT:
                st = f.stat()
                res.append({"folder": folder, "name": f.name, "size": st.st_size, "mtime": st.st_mtime})
    return sorted(res, key=lambda r: -r["mtime"])


@app.get("/api/probe/{folder}/{name}")
def api_probe(folder: str, name: str):
    return probe(safe_path(folder, name))


@app.delete("/api/files/{folder}/{name}")
def api_delete(folder: str, name: str):
    safe_path(folder, name).unlink()
    return {"ok": True}


@app.post("/api/reveal/{folder}/{name}")
def api_reveal(folder: str, name: str):
    path = safe_path(folder, name)
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(path)])
    return {"ok": True}


@app.post("/api/shutdown")
def api_shutdown():
    for job in JOBS.values():  # stop running ffmpeg/yt-dlp work cleanly; .part files stay resumable
        job._cancel = True
        if job._proc and job._proc.poll() is None:
            job._proc.kill()
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return {"ok": True}


@app.get("/favicon.ico")
def favicon():
    return FileResponse(ROOT / "static" / "icon.ico")


@app.get("/media/{folder}/{name}")
def media(folder: str, name: str):
    return FileResponse(safe_path(folder, name))  # supports HTTP Range -> seeking in huge files


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8765))
    threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    # bound to localhost only: this API can delete files, never expose it on a network
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
