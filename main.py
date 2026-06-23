from pathlib import Path
import importlib
import importlib.util
import json
import os
import sys
import shutil
import subprocess
import threading
import time
import re
from datetime import datetime
from collections import namedtuple


def configure_tcl_tk_runtime():
    """Point Tkinter at bundled Tcl/Tk files when the local Python probe fails."""
    base_directories = [Path(__file__).resolve().parent]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_directories.append(Path(sys._MEIPASS))

    for base_directory in base_directories:
        candidates = [
            (base_directory / "_tcl_data", base_directory / "_tk_data"),
            (
                base_directory / "dist" / "SmartCompress_v2.2" / "_tcl_data",
                base_directory / "dist" / "SmartCompress_v2.2" / "_tk_data",
            ),
        ]
        for tcl_directory, tk_directory in candidates:
            if (tcl_directory / "init.tcl").is_file() and (tk_directory / "tk.tcl").is_file():
                os.environ.setdefault("TCL_LIBRARY", str(tcl_directory))
                os.environ.setdefault("TK_LIBRARY", str(tk_directory))
                return


configure_tcl_tk_runtime()

import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import tkinterdnd2 as dnd
from PIL import Image

heif_module = importlib.util.find_spec("pillow_heif")
if heif_module:
    importlib.import_module("pillow_heif").register_heif_opener()


# -----------------------------
# File support and app constants
# -----------------------------
PHOTO_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".gif",
    ".tiff",
    ".heic",
}
VIDEO_EXTENSIONS = {
    ".mp4",
    ".avi",
    ".mov",
    ".mkv",
    ".wmv",
    ".flv",
    ".webm",
    ".3gp",
    ".m4v",
}
BYTES_PER_MB = 1024 * 1024
FFMPEG_TIMEOUT_SECONDS = 3600
DEFAULT_IMAGE_QUALITY = 75
DEFAULT_GEOMETRY = "1180x680"
FFMPEG_EXECUTABLES = ("ffmpeg.exe", "ffprobe.exe", "ffplay.exe")
APP_VERSION = "2.2"


def get_data_directory():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).parent


DATA_DIRECTORY = get_data_directory()
SETTINGS_FILE = DATA_DIRECTORY / "settings.json"
HISTORY_FILE = DATA_DIRECTORY / "history.json"
ERROR_LOG_FILE = DATA_DIRECTORY / "error_log.txt"

def get_ffmpeg_tools():
    """Find the portable FFmpeg tools, preferring files beside the app."""
    search_directories = [DATA_DIRECTORY]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled_directory = Path(sys._MEIPASS)
        if bundled_directory not in search_directories:
            search_directories.append(bundled_directory)
    script_directory = Path(__file__).resolve().parent
    if script_directory not in search_directories:
        search_directories.append(script_directory)

    ffmpeg_subfolder = DATA_DIRECTORY / "ffmpeg"
    if ffmpeg_subfolder.is_dir() and ffmpeg_subfolder not in search_directories:
        search_directories.append(ffmpeg_subfolder)

    tools = {}
    for executable_name in FFMPEG_EXECUTABLES:
        for directory in search_directories:
            candidate = directory / executable_name
            if candidate.is_file():
                tools[executable_name] = str(candidate)
                break

    # Development fallback only. Portable releases always win because the
    # application directory is searched first.
    for executable_name in FFMPEG_EXECUTABLES:
        if executable_name not in tools:
            system_tool = shutil.which(executable_name)
            if system_tool:
                tools[executable_name] = system_tool
    return tools


def get_missing_ffmpeg_tools():
    tools = get_ffmpeg_tools()
    return [name for name in FFMPEG_EXECUTABLES if name not in tools]


def get_ffmpeg_path():
    return get_ffmpeg_tools().get("ffmpeg.exe")

_hw_encoder = None
_hw_encoder_checked = False

def get_hw_encoder(ffmpeg_path):
    global _hw_encoder, _hw_encoder_checked
    if _hw_encoder_checked:
        return _hw_encoder
    _hw_encoder_checked = True
    try:
        # An encoder being listed does not mean the installed hardware/driver can
        # initialize it. Test one frame before selecting it for real files.
        process = subprocess.run(
            [ffmpeg_path, "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        output = process.stdout.lower()
        for encoder in ("h264_nvenc", "h264_qsv", "h264_amf"):
            if encoder not in output:
                continue
            test = subprocess.run(
                [
                    ffmpeg_path, "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=size=64x64:rate=1",
                    "-frames:v", "1", "-c:v", encoder, "-f", "null", "-",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if test.returncode == 0:
                _hw_encoder = encoder
                break
    except Exception:
        pass
    return _hw_encoder

def log_error(filename, error_message, failure_reason):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(ERROR_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] File: {filename}\n")
            f.write(f"Reason: {failure_reason}\n")
            f.write(f"Error: {error_message}\n")
            f.write("-" * 40 + "\n")
    except Exception:
        pass

def load_settings():
    quality = DEFAULT_IMAGE_QUALITY
    geometry = DEFAULT_GEOMETRY
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
                if "quality" in data:
                    quality = max(0, min(100, int(data["quality"])))
                
                if "geometry" in data:
                    geometry = data["geometry"]

        except Exception:
            pass
    return quality, geometry

def save_settings(quality, geometry):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump({"quality": int(quality), "geometry": geometry}, f)
    except Exception:
        pass

def load_history():
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_to_history(
    total_files,
    original_size,
    compressed_size,
    saved_size,
    saved_percent,
    output_folder="",
    compression_time=0,
):
    now = datetime.now()
    history = load_history()
    session = {
        "date_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "total_files": total_files,
        "original_size": original_size,
        "compressed_size": compressed_size,
        "saved_size": saved_size,
        "saved_percent": saved_percent,
        "output_folder": str(output_folder),
        "compression_time": compression_time,
    }
    history.append(session)
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=4)
    except Exception:
        pass

selected_folder = ""
selected_paths = []
save_location = ""
last_output_folder = ""
cancel_requested = threading.Event()
compression_running = False
current_ffmpeg_process = None
current_ffmpeg_lock = threading.Lock()
drag_drop_available = False


class CompressionCancelled(Exception):
    """Raised when the user cancels an active compression session."""


# -----------------------------
# File discovery and formatting
# -----------------------------
def format_eta(seconds):
    if seconds is None or seconds < 0:
        return "--:--"
    if seconds < 60:
        return f"~{int(seconds)} sec"
    if seconds < 3600:
        return f"~{int(seconds // 60)} min"
    
    return f"~{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"


def format_duration(seconds):
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_size(size_in_bytes):
    return f"{size_in_bytes / BYTES_PER_MB:.2f} MB"


def is_inside_folder(file_path, folder_path):
    if not folder_path:
        return False

    try:
        Path(file_path).resolve().relative_to(Path(folder_path).resolve())
        return True
    except ValueError:
        return False


def get_supported_files(folder_path, extensions, excluded_folder=None):
    return sorted(
        [
            item
            for item in Path(folder_path).rglob("*")
            if item.is_file()
            and item.suffix.lower() in extensions
            and not is_inside_folder(item, excluded_folder)
        ]
    )


def get_image_files(folder_path, excluded_folder=None):
    return get_supported_files(folder_path, PHOTO_EXTENSIONS, excluded_folder)


def get_video_files(folder_path, excluded_folder=None):
    return get_supported_files(folder_path, VIDEO_EXTENSIONS, excluded_folder)


def get_media_counts(folder_path):
    image_count = len(get_image_files(folder_path))
    video_count = len(get_video_files(folder_path))
    return image_count, video_count


def get_scan_summary(folder_path, excluded_folder=None):
    all_files = sorted(
        [
            item
            for item in Path(folder_path).rglob("*")
            if item.is_file() and not is_inside_folder(item, excluded_folder)
        ]
    )
    found_extensions = sorted(
        {item.suffix.lower() if item.suffix else "[no extension]" for item in all_files}
    )

    return len(all_files), found_extensions


def normalize_source_paths(paths):
    """Keep only existing files/folders and remove duplicate drop entries."""
    normalized = []
    seen = set()
    for raw_path in paths:
        try:
            path = Path(raw_path).resolve()
        except Exception:
            continue
        if not path.exists() or path in seen:
            continue
        if path.is_file() and path.suffix.lower() not in PHOTO_EXTENSIONS | VIDEO_EXTENSIONS:
            continue
        normalized.append(path)
        seen.add(path)
    return normalized


def collect_media_from_sources(paths, excluded_folder=None):
    """Return media files from dropped files, one folder, or multiple folders."""
    media_files = []
    for source in normalize_source_paths(paths):
        if source.is_file():
            if not is_inside_folder(source, excluded_folder):
                media_files.append(source)
            continue
        media_files.extend(get_supported_files(source, PHOTO_EXTENSIONS | VIDEO_EXTENSIONS, excluded_folder))
    return sorted(dict.fromkeys(media_files))


def get_output_path_for_source(input_file, sources, output_folder):
    """Preserve old single-folder output layout and avoid collisions for mixed drops."""
    input_path = Path(input_file).resolve()
    normalized_sources = normalize_source_paths(sources)
    folder_sources = [source for source in normalized_sources if source.is_dir()]

    if len(normalized_sources) == 1 and folder_sources:
        return Path(output_folder) / input_path.relative_to(folder_sources[0])

    for folder in folder_sources:
        try:
            return Path(output_folder) / folder.name / input_path.relative_to(folder)
        except ValueError:
            continue

    return Path(output_folder) / input_path.name


def describe_sources(paths):
    normalized = normalize_source_paths(paths)
    if not normalized:
        return "Not Selected"
    if len(normalized) == 1:
        return str(normalized[0])
    folder_count = sum(1 for path in normalized if path.is_dir())
    file_count = sum(1 for path in normalized if path.is_file())
    return f"{folder_count} folder(s), {file_count} file(s) selected"


# -----------------------------
# Status and report helpers
# -----------------------------
def update_status(status, message_type="info"):
    selected_text = describe_sources(selected_paths) if selected_paths else (selected_folder or "Not Selected")
    save_text = save_location if save_location else "Not Selected"

    selected_value.configure(text=selected_text)
    save_value.configure(text=save_text)

    text_color = "#d1d5db"
    if message_type == "info":
        text_color = "#93c5fd"
    if message_type == "success":
        text_color = "#5eead4"
    elif message_type == "error":
        text_color = "#f87171"
    elif message_type == "muted":
        text_color = "#94a3b8"

    status_value.configure(text=status, text_color=text_color)


def update_statistics(
    current_file="None",
    progress=0,
    files_processed=0,
    total_files=0,
    compressed_images=0,
    compressed_videos=0,
    failed_files=0,
    original_size=0,
    compressed_size=0,
    eta="--:--",
    elapsed_time="0s",
    average_time="--",
    remaining_time="--:--",
    compression_speed="0.00 MB/s",
):
    saved_size = max(0, original_size - compressed_size)
    saved_percent = (saved_size / original_size * 100) if original_size else 0
    values = {
        "current_file": current_file,
        "progress": f"{progress:.1f}%",
        "files_processed": f"{files_processed} / {total_files}",
        "remaining_files": str(max(0, total_files - files_processed)),
        "elapsed_time": elapsed_time,
        "average_time": average_time,
        "remaining_time": remaining_time,
        "compression_speed": compression_speed,
        "compressed_images": str(compressed_images),
        "compressed_videos": str(compressed_videos),
        "failed_files": str(failed_files),
        "original_size": format_size(original_size),
        "compressed_size": format_size(compressed_size),
        "saved_size": format_size(saved_size),
        "saved_percentage": f"{saved_percent:.1f}%",
        "eta": eta,
    }
    for key, value in values.items():
        if key in statistics_values:
            statistics_values[key].configure(text=value)


def schedule_statistics(**values):
    app.after(0, lambda snapshot=values: update_statistics(**snapshot))


def build_runtime_metrics(session_start, files_processed, total_files, bytes_done):
    elapsed = max(0.001, time.time() - session_start)
    avg_time = elapsed / files_processed if files_processed else None
    remaining_files = max(0, total_files - files_processed)
    remaining = avg_time * remaining_files if avg_time is not None else None
    speed = (bytes_done / BYTES_PER_MB) / elapsed
    return {
        "elapsed_time": format_duration(elapsed),
        "average_time": format_duration(avg_time) if avg_time is not None else "--",
        "remaining_time": format_eta(remaining) if remaining is not None else "--:--",
        "compression_speed": f"{speed:.2f} MB/s",
        "eta": format_eta(remaining) if remaining is not None else "--:--",
    }


def add_live_log(message, message_type="info"):
    """Add newest compression log entry at the top of the live log card."""
    if "live_log_frame" not in globals():
        return

    colors = {
        "success": "#5eead4",
        "error": "#f87171",
        "info": "#bfdbfe",
        "muted": "#94a3b8",
    }
    entry = ctk.CTkLabel(
        live_log_frame,
        text=f"{datetime.now().strftime('%H:%M:%S')}  {message}",
        font=("Segoe UI", 11),
        text_color=colors.get(message_type, "#bfdbfe"),
        anchor="w",
        justify="left",
        wraplength=500,
    )
    existing_entries = live_log_frame.winfo_children()
    if existing_entries:
        entry.pack(fill="x", padx=10, pady=2, before=existing_entries[0])
    else:
        entry.pack(fill="x", padx=10, pady=2)
    app.after(50, lambda: getattr(live_log_frame, "_parent_canvas", None) and live_log_frame._parent_canvas.yview_moveto(0))


def clear_live_log():
    if "live_log_frame" not in globals():
        return
    for child in live_log_frame.winfo_children():
        child.destroy()


def show_friendly_message(title, message):
    try:
        messagebox.showinfo(title, message)
    except Exception:
        update_status(message, "error")


def open_folder_in_explorer(folder_path):
    if not folder_path or not Path(folder_path).is_dir():
        show_friendly_message("Folder not found", "Output folder not found.")
        return
    if os.name == "nt":
        os.startfile(str(folder_path))
    else:
        subprocess.Popen(["xdg-open", str(folder_path)])


def show_completion_popup(summary):
    popup = ctk.CTkToplevel(app)
    popup.title("Compression Completed")
    popup.geometry("520x520")
    popup.resizable(False, False)
    popup.transient(app)
    popup.configure(fg_color=APP_BG)

    card = ctk.CTkFrame(popup, corner_radius=18, fg_color=SURFACE, border_width=1, border_color=BORDER)
    card.pack(fill="both", expand=True, padx=18, pady=18)

    ctk.CTkLabel(
        card,
        text="Compression Completed Successfully",
        font=("Segoe UI", 22, "bold"),
        text_color="#5eead4",
    ).pack(padx=20, pady=(22, 6))
    ctk.CTkLabel(
        card,
        text="Your compressed files are ready.",
        font=("Segoe UI", 13),
        text_color=MUTED,
    ).pack(padx=20, pady=(0, 16))

    metrics_frame = ctk.CTkFrame(card, corner_radius=14, fg_color=CARD)
    metrics_frame.pack(fill="x", padx=20, pady=(0, 16))
    rows = (
        ("Images", summary["images"]),
        ("Videos", summary["videos"]),
        ("Failed", summary["failed"]),
        ("Original Size", format_size(summary["original_size"])),
        ("Compressed Size", format_size(summary["compressed_size"])),
        ("Saved Size", format_size(summary["saved_size"])),
        ("Saved %", f"{summary['saved_percent']:.1f}%"),
        ("Total Time", format_duration(summary["total_time"])),
    )
    for row, (label, value) in enumerate(rows):
        metrics_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(metrics_frame, text=label, font=("Segoe UI", 12), text_color=MUTED).grid(
            row=row, column=0, padx=16, pady=5, sticky="w"
        )
        ctk.CTkLabel(metrics_frame, text=str(value), font=("Segoe UI", 12, "bold"), text_color=TEXT).grid(
            row=row, column=1, padx=16, pady=5, sticky="e"
        )

    button_frame = ctk.CTkFrame(card, fg_color="transparent")
    button_frame.pack(fill="x", padx=20, pady=(4, 20))
    button_frame.grid_columnconfigure((0, 1), weight=1)
    ctk.CTkButton(
        button_frame,
        text="Open Output Folder",
        height=38,
        corner_radius=9,
        fg_color=PRIMARY,
        hover_color=PRIMARY_HOVER,
        command=lambda: open_folder_in_explorer(summary["output_folder"]),
    ).grid(row=0, column=0, padx=(0, 8), sticky="ew")
    ctk.CTkButton(
        button_frame,
        text="Close",
        height=38,
        corner_radius=9,
        fg_color="#1e293b",
        hover_color="#334155",
        command=popup.destroy,
    ).grid(row=0, column=1, padx=(8, 0), sticky="ew")

    popup.after(100, popup.grab_set)


def request_cancel():
    if not compression_running:
        return
    cancel_requested.set()
    with current_ffmpeg_lock:
        process = current_ffmpeg_process
    if process and process.poll() is None:
        try:
            process.terminate()
        except Exception:
            pass
    update_status("Cancelling after the current safe step...", "muted")
    add_live_log("Cancellation requested", "muted")


def build_report(
    quality,
    image_count,
    video_count,
    failed_images,
    copied_original_videos,
    compressed_files,
    kept_original_files,
    failed_files,
    original_size,
    compressed_size,
    output_folder,
    failed_image_details,
    failed_video_details,
):
    saved_size = max(0, original_size - compressed_size)
    saved_percent = (saved_size / original_size) * 100 if original_size else 0

    report = ["Compression complete"]
    report.append(f"{compressed_files + kept_original_files} files completed; {failed_files} failed")
    report.append(f"Saved {format_size(saved_size)} ({saved_percent:.1f}%)")
    report.append(f"Output: {output_folder}")

    return "\n".join(report)


# -----------------------------
# UI Actions & Event Handlers
# -----------------------------
def handle_source_selection(paths):
    global selected_folder, selected_paths
    selected_paths = normalize_source_paths(paths)
    selected_folder = str(selected_paths[0]) if len(selected_paths) == 1 else describe_sources(selected_paths)
    update_status("Scanning selected media...")

    def scan_folder():
        try:
            files = collect_media_from_sources(selected_paths)
            image_count = sum(1 for item in files if item.suffix.lower() in PHOTO_EXTENSIONS)
            video_count = sum(1 for item in files if item.suffix.lower() in VIDEO_EXTENSIONS)
            if selected_paths:
                app.after(
                    0,
                    update_status,
                    f"Ready: {image_count} images and {video_count} videos found",
                    "success",
                )
        except Exception as error:
            app.after(0, update_status, f"Could not scan folder: {error}", "error")

    threading.Thread(target=scan_folder, daemon=True).start()


def handle_folder_selection(folder_path):
    handle_source_selection([folder_path])

def select_folder_dialog():
    folder_path = filedialog.askdirectory(title="Select Folder")

    if not folder_path:
        return

    handle_folder_selection(folder_path)

def choose_save_location():
    global save_location

    save_path = filedialog.askdirectory(title="Choose Save Location")

    if not save_path:
        return

    save_location = save_path
    update_status("Save location selected", "success")

def handle_drop(event):
    try:
        dropped_paths = app.tk.splitlist(event.data)
    except Exception:
        dropped_paths = [event.data.strip("{}")]

    valid_paths = normalize_source_paths(dropped_paths)
    if valid_paths:
        handle_source_selection(valid_paths)
    else:
        update_status("Drop supported media files or folders.", "error")


def open_output_folder():
    if not last_output_folder or not Path(last_output_folder).exists():
        update_status("No output folder available yet", "error")
        show_friendly_message("Folder not found", "Output folder not found.")
        return

    open_folder_in_explorer(last_output_folder)

def open_history():
    history_window = ctk.CTkToplevel(app)
    history_window.title("SmartCompress - Compression History")
    history_window.geometry("920x560")
    history_window.minsize(760, 460)
    history_window.transient(app)
    history_window.configure(fg_color="#0b1120")

    header = ctk.CTkFrame(history_window, height=70, corner_radius=14, fg_color="#111827")
    header.pack(fill="x", padx=16, pady=(16, 8))
    ctk.CTkLabel(header, text="Compression History", font=("Segoe UI", 24, "bold")).pack(
        side="left", padx=20, pady=16
    )

    history_frame = ctk.CTkScrollableFrame(
        history_window,
        corner_radius=14,
        fg_color="#111827",
        scrollbar_button_color="#2563eb",
    )
    history_frame.pack(fill="both", expand=True, padx=16, pady=(0, 16))
    history_data = load_history()

    if not history_data:
        ctk.CTkLabel(
            history_frame,
            text="No compression sessions yet.",
            font=("Segoe UI", 16),
            text_color="#94a3b8",
        ).pack(pady=50)
        return

    for session in reversed(history_data):
        card = ctk.CTkFrame(
            history_frame,
            corner_radius=12,
            fg_color="#172033",
            border_width=1,
            border_color="#263449",
        )
        card.pack(fill="x", padx=8, pady=6)
        card.grid_columnconfigure(1, weight=1)

        timestamp = str(session.get("date_time", "Unknown"))
        date_text = session.get("date") or timestamp.partition(" ")[0]
        time_text = session.get("time") or timestamp.partition(" ")[2]
        output_folder = session.get("output_folder", "")
        ctk.CTkLabel(card, text=date_text, font=("Segoe UI", 15, "bold"), text_color="#f8fafc").grid(
            row=0, column=0, padx=(16, 8), pady=(12, 2), sticky="w"
        )
        ctk.CTkLabel(card, text=time_text or "--:--:--", font=("Segoe UI", 13), text_color="#60a5fa").grid(
            row=1, column=0, padx=(16, 8), pady=(0, 12), sticky="w"
        )

        history_items = (
            ("Files", str(session.get("total_files", 0))),
            ("Original", format_size(session.get("original_size", 0))),
            ("Compressed", format_size(session.get("compressed_size", 0))),
            ("Saved", format_size(session.get("saved_size", 0))),
            ("Saved %", f"{session.get('saved_percent', 0):.1f}%"),
            ("Time", format_duration(session.get("compression_time", 0))),
        )
        metrics = ctk.CTkFrame(card, fg_color="transparent")
        metrics.grid(row=0, column=1, rowspan=2, padx=(8, 16), pady=10, sticky="ew")
        for column, (label, value) in enumerate(history_items):
            metrics.grid_columnconfigure(column, weight=1)
            ctk.CTkLabel(metrics, text=label, font=("Segoe UI", 11), text_color="#94a3b8").grid(
                row=0, column=column, padx=6
            )
            ctk.CTkLabel(metrics, text=value, font=("Segoe UI", 13, "bold"), text_color="#e2e8f0").grid(
                row=1, column=column, padx=6, pady=(2, 0)
            )

        action_frame = ctk.CTkFrame(card, fg_color="transparent")
        action_frame.grid(row=2, column=0, columnspan=2, padx=16, pady=(0, 12), sticky="ew")
        action_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            action_frame,
            text=f"Output: {output_folder or 'Not recorded'}",
            font=("Segoe UI", 11),
            text_color="#94a3b8",
            anchor="w",
            wraplength=580,
        ).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            action_frame,
            text="Open Output Folder",
            width=150,
            height=30,
            corner_radius=8,
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOVER,
            command=lambda folder=output_folder: open_folder_in_explorer(folder),
        ).grid(row=0, column=1, padx=(10, 0))

    history_window.after(100, history_window.grab_set)

# -----------------------------
# Compression engines
# -----------------------------
def compress_image_file(input_file, output_file, quality):
    quality = max(0, min(100, int(quality)))
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(input_file) as image:
        suffix = input_file.suffix.lower()
        save_kwargs = {"optimize": True, "quality": quality}

        if suffix in {".jpg", ".jpeg"} and image.mode in {"RGBA", "P", "LA", "PA"}:
            image = image.convert("RGB")

        if suffix in {".jpg", ".jpeg"}:
            save_kwargs["progressive"] = True
            if image.info.get("exif"):
                save_kwargs["exif"] = image.info["exif"]
            if image.info.get("icc_profile"):
                save_kwargs["icc_profile"] = image.info["icc_profile"]
        elif suffix == ".png":
            save_kwargs.pop("quality", None)
            save_kwargs["compress_level"] = 9
        elif suffix == ".gif":
            save_kwargs.pop("quality", None)

        image.save(output_file, **save_kwargs)


def _run_ffmpeg(command, progress_callback):
    """Run one FFmpeg encode while reporting progress from its normal output."""
    global current_ffmpeg_process
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    with current_ffmpeg_lock:
        current_ffmpeg_process = process

    duration_pattern = re.compile(r"Duration:\s*(\d{2}):(\d{2}):(\d{2}\.\d+)")
    time_pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2}\.\d+)")
    total_duration = 0
    error_tail = []

    try:
        for line in process.stderr:
            if cancel_requested.is_set():
                process.terminate()
                raise CompressionCancelled("Compression cancelled")
            error_tail.append(line.rstrip())
            error_tail = error_tail[-30:]
            if total_duration == 0:
                match = duration_pattern.search(line)
                if match:
                    h, m, s = match.groups()
                    total_duration = int(h) * 3600 + int(m) * 60 + float(s)
            if total_duration > 0 and progress_callback:
                match = time_pattern.search(line)
                if match:
                    h, m, s = match.groups()
                    current_time = int(h) * 3600 + int(m) * 60 + float(s)
                    progress_callback(min(100.0, current_time / total_duration * 100))

        try:
            process.wait(timeout=FFMPEG_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            raise RuntimeError("FFmpeg process timed out")

        if cancel_requested.is_set():
            raise CompressionCancelled("Compression cancelled")

        if process.returncode != 0:
            details = "\n".join(error_tail) or "FFmpeg exited with an error"
            raise RuntimeError(details)
    finally:
        with current_ffmpeg_lock:
            if current_ffmpeg_process is process:
                current_ffmpeg_process = None


def _h264_options(encoder, crf):
    if encoder == "h264_nvenc":
        return ["-c:v", encoder, "-preset", "p3", "-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
    if encoder == "h264_qsv":
        return ["-c:v", encoder, "-preset", "veryfast", "-global_quality", str(crf)]
    if encoder == "h264_amf":
        return ["-c:v", encoder, "-quality", "speed", "-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf)]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-threads", "0"]


def compress_video_file(input_file, output_file, quality, progress_callback=None):
    ffmpeg_path = get_ffmpeg_path()

    if not ffmpeg_path:
        raise RuntimeError("FFmpeg missing")

    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Lower CRF means higher quality. Keep the useful range conservative so
    # even low slider values remain watchable while 100 approaches lossless.
    crf = 40 - round((quality / 100) * 22)
    crf = max(0, min(51, int(crf)))

    output_suffix = output_file.suffix.lower()
    video_options = []
    audio_codec_args = ["-c:a", "aac", "-b:a", "128k"]
    hw_encoder = None

    if output_suffix in {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".flv", ".3gp"}:
        hw_encoder = get_hw_encoder(ffmpeg_path)
        video_options = _h264_options(hw_encoder or "libx264", crf)
        if output_suffix in {".mp4", ".mov", ".m4v"}:
            video_options.extend(["-movflags", "+faststart"])

    elif output_suffix == ".webm":
        video_options = [
            "-c:v", "libvpx-vp9", "-crf", str(crf), "-b:v", "0",
            "-deadline", "good", "-cpu-used", "5", "-row-mt", "1", "-threads", "0",
        ]
        audio_codec_args = ["-c:a", "libopus", "-b:a", "96k"]
    elif output_suffix == ".wmv":
        video_options = ["-c:v", "wmv2", "-q:v", "5", "-threads", "0"]
        audio_codec_args = ["-c:a", "wmav2", "-b:a", "128k"]

    command = [ffmpeg_path, "-hide_banner", "-y", "-i", str(input_file), "-map", "0:v:0", "-map", "0:a?", "-sn", "-dn"]
    command.extend(video_options)
    command.extend(audio_codec_args)
    command.append(str(output_file))
    start_time = time.time()
    try:
        _run_ffmpeg(command, progress_callback)
    except RuntimeError:
        # Drivers can still reject a particular resolution or pixel format even
        # after the startup test. Retry once with the dependable CPU encoder.
        if not hw_encoder:
            raise
        output_file.unlink(missing_ok=True)
        fallback = [ffmpeg_path, "-hide_banner", "-y", "-i", str(input_file), "-map", "0:v:0", "-map", "0:a?", "-sn", "-dn"]
        fallback.extend(_h264_options("libx264", crf))
        if output_suffix in {".mp4", ".mov", ".m4v"}:
            fallback.extend(["-movflags", "+faststart"])
        fallback.extend(audio_codec_args)
        fallback.append(str(output_file))
        _run_ffmpeg(fallback, progress_callback)
        
    return time.time() - start_time


def set_controls_enabled(enabled):
    state = "normal" if enabled else "disabled"

    select_button.configure(state=state)
    save_button.configure(state=state)
    history_button.configure(state=state)
    quality_slider.configure(state=state)

    if enabled:
        compress_button.configure(
            state="normal",
            text="Start Compression",
            command=compress_media,
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOVER,
        )
    else:
        compress_button.configure(
            state="normal",
            text="Cancel Compression",
            command=request_cancel,
            fg_color="#dc2626",
            hover_color="#b91c1c",
        )

    if enabled and last_output_folder:
        open_button.configure(state="normal")
    else:
        open_button.configure(state="disabled")


# -----------------------------
# Per-file processing helpers
# -----------------------------
_ProcessResult = namedtuple(
    "_ProcessResult",
    [
        "compressed_size_delta",
        "kept_original",
        "copied_original",
        "compressed",
        "failed",
        "failed_name",
        "error_message",
    ],
)


def _ok_result(output_size):
    return _ProcessResult(
        compressed_size_delta=output_size,
        kept_original=False,
        copied_original=False,
        compressed=True,
        failed=False,
        failed_name=None,
        error_message=None,
    )


def _failure_result(item_label, message):
    print(message)
    return _ProcessResult(
        compressed_size_delta=0,
        kept_original=False,
        copied_original=False,
        compressed=False,
        failed=True,
        failed_name=item_label,
        error_message=message,
    )


def _process_image(input_file, output_file, quality):
    try:
        if cancel_requested.is_set():
            raise CompressionCancelled("Compression cancelled")
        start_time = time.time()
        compress_image_file(input_file, output_file, quality)
        if cancel_requested.is_set():
            raise CompressionCancelled("Compression cancelled")
        time_taken = time.time() - start_time
        
        original_size = input_file.stat().st_size
        compressed_size = output_file.stat().st_size
        ratio = (compressed_size / original_size) * 100 if original_size else 0
        print(f"[Image] {input_file.name} | Time: {time_taken:.2f}s | Orig: {original_size} | Comp: {compressed_size} | Ratio: {ratio:.1f}%")
        
        return _ok_result(compressed_size)
    except CompressionCancelled:
        output_file.unlink(missing_ok=True)
        raise
    except Exception as error:
        output_file.unlink(missing_ok=True)
        log_error(input_file.name, str(error), "Image compression failed")
        return _failure_result(input_file.name, f"Could not compress image {input_file.name}: {error}")


def _process_video(input_file, output_file, quality, item_label, progress_callback):
    try:
        if cancel_requested.is_set():
            raise CompressionCancelled("Compression cancelled")
        time_taken = compress_video_file(input_file, output_file, quality, progress_callback)
        original_size = input_file.stat().st_size
        compressed_size = output_file.stat().st_size
        ratio = (compressed_size / original_size) * 100 if original_size else 0
        
        print(f"[Video] {item_label} | Time: {time_taken:.2f}s | Orig: {original_size} | Comp: {compressed_size} | Ratio: {ratio:.1f}%")
        return _ok_result(compressed_size)
    except CompressionCancelled:
        output_file.unlink(missing_ok=True)
        raise
    except Exception as error:
        output_file.unlink(missing_ok=True)
        error_str = str(error)
        if "FFmpeg missing" in error_str:
            failure_reason = "FFmpeg missing"
        elif "codec" in error_str.lower() or "encoder" in error_str.lower():
            failure_reason = "Video codec not supported"
        elif isinstance(error, PermissionError):
            failure_reason = "Permission denied"
        elif "in use" in error_str.lower() or "used by another process" in error_str.lower():
            failure_reason = "File in use"
        else:
            failure_reason = "Unknown error"
            
        error_msg = f"Could not compress video {item_label}:\n{failure_reason}"
        log_error(input_file.name, error_str, failure_reason)
        return _failure_result(item_label, error_msg)


def run_compression(source_paths, destination_folder, quality):
    global last_output_folder

    output_folder = os.path.join(destination_folder, "Compressed_Output")
    last_output_folder = output_folder
    os.makedirs(output_folder, exist_ok=True)

    source_paths = normalize_source_paths(source_paths)
    all_files = collect_media_from_sources(source_paths, output_folder)
    image_files = [item for item in all_files if item.suffix.lower() in PHOTO_EXTENSIONS]
    video_files = [item for item in all_files if item.suffix.lower() in VIDEO_EXTENSIONS]

    if not all_files:
        scanned_count = 0
        found_extensions = set()
        for source in source_paths:
            if source.is_file():
                scanned_count += 1
                found_extensions.add(source.suffix.lower() if source.suffix else "[no extension]")
            elif source.is_dir():
                count, extensions = get_scan_summary(source, output_folder)
                scanned_count += count
                found_extensions.update(extensions)
        extension_text = ", ".join(found_extensions) if found_extensions else "none"

        app.after(
            0,
            update_status,
            "No supported image or video files found\n\n"
            f"Total files scanned: {scanned_count}\n"
            f"Extensions found: {extension_text}\n\n"
            "Supported images: jpg, jpeg, png, webp, bmp, gif, tiff, heic\n"
            "Supported videos: mp4, avi, mov, mkv, wmv, flv, webm, 3gp, m4v",
            "error",
        )
        app.after(0, set_controls_enabled, True)
        return

    if video_files and get_missing_ffmpeg_tools():
        app.after(
            0,
            update_status,
            "FFmpeg files are missing. Please keep ffmpeg.exe, ffprobe.exe and ffplay.exe in the application folder.",
            "error"
        )
        app.after(0, set_controls_enabled, True)
        return

    original_size = 0
    compressed_size = 0
    compressed_images = 0
    compressed_videos = 0
    failed_images = 0
    copied_original_videos = 0
    compressed_files = 0
    kept_original_files = 0
    failed_files = 0    
    failed_image_details = []
    failed_video_details = []

    total_processing_time = 0
    total_files = len(all_files)
    session_start = time.time()
    cancelled = False

    app.after(0, clear_live_log)
    app.after(0, add_live_log, f"Started compression for {total_files} file(s)", "info")

    for index, item in enumerate(all_files, start=1):
        if cancel_requested.is_set():
            cancelled = True
            break

        file_start_time = time.time()
        output_file = get_output_path_for_source(item, source_paths, output_folder)
        last_output_folder = str(output_file.parent)
        try:
            relative_path = output_file.relative_to(Path(output_folder))
        except ValueError:
            relative_path = Path(item).name
        item_label = str(relative_path)

        def make_progress_callback(idx, total, label, imgs, vids, fails, orig_sz, comp_sz, time_elapsed):
            def cb(percentage):
                files_processed = idx - 1
                overall_progress = (idx - 1 + (percentage / 100.0)) / total
                metrics = build_runtime_metrics(session_start, files_processed, total, orig_sz)
                app.after(0, progress_bar.set, overall_progress)
                schedule_statistics(
                    current_file=label,
                    progress=percentage,
                    files_processed=files_processed,
                    total_files=total,
                    compressed_images=imgs,
                    compressed_videos=vids,
                    failed_files=fails,
                    original_size=orig_sz,
                    compressed_size=comp_sz,
                    **metrics,
                )
            return cb

        files_processed = index - 1
        try:
            item_size = item.stat().st_size
        except OSError as error:
            result = _failure_result(item_label, f"Could not read {item_label}: {error}")
            log_error(item_label, str(error), "Could not read input file")
            failed_files += 1
            app.after(0, update_status, result.error_message, "error")
            app.after(0, add_live_log, f"❌ Failed file: {item_label}", "error")
            continue

        metrics = build_runtime_metrics(session_start, files_processed, total_files, original_size)
        schedule_statistics(
            current_file=item_label,
            progress=0,
            files_processed=files_processed,
            total_files=total_files,
            compressed_images=compressed_images,
            compressed_videos=compressed_videos,
            failed_files=failed_files,
            original_size=original_size,
            compressed_size=compressed_size,
            **metrics,
        )

        is_image = item.suffix.lower() in PHOTO_EXTENSIONS
        try:
            if is_image:
                result = _process_image(item, output_file, quality)
            else:
                progress_cb = make_progress_callback(index, total_files, item_label, compressed_images, compressed_videos, failed_files, original_size, compressed_size, total_processing_time)
                result = _process_video(item, output_file, quality, item_label, progress_cb)
        except CompressionCancelled:
            cancelled = True
            app.after(0, add_live_log, f"Cancelled safely while processing: {item_label}", "muted")
            break

        if not result.failed:
            original_size += item_size
            compressed_size += result.compressed_size_delta

        if result.kept_original:
            kept_original_files += 1
            if result.copied_original:
                copied_original_videos += 1
        elif result.compressed:
            compressed_files += 1
            if is_image:
                compressed_images += 1
            else:
                compressed_videos += 1
            app.after(
                0,
                add_live_log,
                f"✔ {'Image' if is_image else 'Video'} compressed: {item_label}",
                "success",
            )

        if result.failed:
            failed_files += 1
            if is_image:
                failed_images += 1
                if result.failed_name:
                    failed_image_details.append((result.failed_name, result.error_message))
            else:
                if result.failed_name:
                    failed_video_details.append((result.failed_name, result.error_message))

        if result.failed:
            app.after(0, update_status, result.error_message, "error")
            app.after(0, add_live_log, f"❌ Failed file: {item_label}", "error")

        app.after(0, progress_bar.set, index / total_files)
        metrics = build_runtime_metrics(session_start, index, total_files, original_size)
        schedule_statistics(
            current_file=item_label,
            progress=100,
            files_processed=index,
            total_files=total_files,
            compressed_images=compressed_images,
            compressed_videos=compressed_videos,
            failed_files=failed_files,
            original_size=original_size,
            compressed_size=compressed_size,
            **metrics,
        )

        file_end_time = time.time()
        total_processing_time += (file_end_time - file_start_time)

    total_processing_time = time.time() - session_start
    saved_size = max(0, original_size - compressed_size)
    saved_percent = (saved_size / original_size) * 100 if original_size else 0

    report_type = "muted" if cancelled else ("success" if compressed_files or kept_original_files else "error")
    if compressed_files or kept_original_files:
        save_to_history(
            compressed_files + kept_original_files,
            original_size,
            compressed_size,
            saved_size,
            saved_percent,
            output_folder,
            total_processing_time,
        )

    if cancelled:
        app.after(0, update_status, "Compression cancelled. Completed files were kept safely.", report_type)
    else:
        app.after(
            0,
            update_status,
            build_report(
                quality,
                compressed_images,
                compressed_videos,
                failed_images,
                copied_original_videos,
                compressed_files,
                kept_original_files,
                failed_files,
                original_size,
                compressed_size,
                output_folder,
                failed_image_details,
                failed_video_details,
            ),
            report_type,
        )
        app.after(
            0,
            show_completion_popup,
            {
                "images": compressed_images,
                "videos": compressed_videos,
                "failed": failed_files,
                "original_size": original_size,
                "compressed_size": compressed_size,
                "saved_size": saved_size,
                "saved_percent": saved_percent,
                "total_time": total_processing_time,
                "output_folder": output_folder,
            },
        )

    final_processed = compressed_files + kept_original_files + failed_files
    metrics = build_runtime_metrics(session_start, final_processed, total_files, original_size)
    schedule_statistics(
        current_file="Cancelled" if cancelled else "Complete",
        progress=(final_processed / total_files * 100) if total_files else 0,
        files_processed=final_processed,
        total_files=total_files,
        compressed_images=compressed_images,
        compressed_videos=compressed_videos,
        failed_files=failed_files,
        original_size=original_size,
        compressed_size=compressed_size,
        eta="Cancelled" if cancelled else "Done",
        remaining_time="Cancelled" if cancelled else "Done",
        elapsed_time=format_duration(total_processing_time),
        average_time=metrics["average_time"],
        compression_speed=metrics["compression_speed"],
    )
    app.after(0, set_controls_enabled, True)


def run_compression_safely(source_folder, destination_folder, quality):
    global compression_running
    try:
        compression_running = True
        run_compression(source_folder, destination_folder, quality)
    except CompressionCancelled:
        app.after(0, update_status, "Compression cancelled safely.", "muted")
    except Exception as error:
        log_error("Compression session", str(error), "Unexpected session error")
        app.after(0, update_status, f"Compression stopped safely: {error}", "error")
    finally:
        compression_running = False
        cancel_requested.clear()
        app.after(0, set_controls_enabled, True)


def compress_media():
    global compression_running
    if not selected_paths:
        update_status("Please select a folder first", "error")
        return

    if not save_location:
        update_status("Please choose a save location", "error")
        return

    quality = int(quality_slider.get())
    save_settings(quality, app.geometry())

    progress_bar.set(0)
    update_status("Preparing compression...")
    update_statistics()
    clear_live_log()
    cancel_requested.clear()
    compression_running = True
    set_controls_enabled(False)

    worker = threading.Thread(
        target=run_compression_safely,
        args=(list(selected_paths), save_location, quality),
        daemon=True,
    )
    worker.start()


# -----------------------------
# CustomTkinter user interface
# -----------------------------
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")
ctk.set_widget_scaling(0.9)
ctk.set_window_scaling(0.9)

APP_BG = "#070b14"
SURFACE = "#0f1726"
CARD = "#131d2e"
CARD_ALT = "#182438"
BORDER = "#263449"
PRIMARY = "#2563eb"
PRIMARY_HOVER = "#1d4ed8"
TEXT = "#f8fafc"
MUTED = "#94a3b8"

initial_quality, initial_geometry = load_settings()

try:
    app = dnd.TkinterDnD.Tk()
    drag_drop_available = True
except Exception as error:
    app = tk.Tk()
    log_error("Startup", str(error), "Drag and drop disabled")

app.geometry(initial_geometry)
app.minsize(900, 600)
app.title(f"SmartCompress v{APP_VERSION}")
app.configure(bg=APP_BG)

try:
    icon_path = Path(__file__).parent / "icon.ico"
    app.iconbitmap(str(icon_path))
except Exception:
    pass

# Register the window for drag & drop when tkdnd is available.
if drag_drop_available:
    app.drop_target_register(dnd.DND_FILES)
    app.dnd_bind('<<Drop>>', handle_drop)

app.grid_columnconfigure(0, weight=1)
app.grid_rowconfigure(0, weight=1)

main_frame = ctk.CTkFrame(app, fg_color="transparent")
main_frame.grid(row=0, column=0, padx=12, pady=10, sticky="nsew")
main_frame.grid_columnconfigure(0, weight=9, uniform="panels")
main_frame.grid_columnconfigure(1, weight=11, uniform="panels")
main_frame.grid_rowconfigure(1, weight=1)

header_frame = ctk.CTkFrame(
    main_frame,
    height=116,
    corner_radius=18,
    fg_color=SURFACE,
    border_width=1,
    border_color=BORDER,
)
header_frame.grid(row=0, column=0, columnspan=2, padx=2, pady=(0, 8), sticky="ew")
header_frame.grid_columnconfigure(1, weight=1)
header_frame.grid_propagate(False)

try:
    logo_path = Path(__file__).parent / "smartcompress_logo.png"
    with Image.open(logo_path) as logo_source:
        logo_full = logo_source.copy()
    logo_image = ctk.CTkImage(light_image=logo_full, dark_image=logo_full, size=(104, 104))
    logo_label = ctk.CTkLabel(header_frame, image=logo_image, text="")
    logo_label.grid(row=0, column=0, rowspan=3, padx=(18, 16), pady=6, sticky="ns")
except Exception:
    pass

title = ctk.CTkLabel(
    header_frame,
    text="SmartCompress",
    font=("Segoe UI", 28, "bold"),
    text_color=TEXT,
    anchor="w",
)
title.grid(row=0, column=1, padx=0, pady=(14, 0), sticky="sw")

developer_label = ctk.CTkLabel(
    header_frame,
    text="Fast, dependable media compression",
    font=("Segoe UI", 13),
    text_color=MUTED,
    anchor="w",
)
developer_label.grid(row=1, column=1, padx=0, pady=0, sticky="w")

subtitle = ctk.CTkLabel(
    header_frame,
    text="Developed by Kamalesh S",
    font=("Segoe UI", 11, "bold"),
    text_color="#60a5fa",
    anchor="w",
)
subtitle.grid(row=2, column=1, padx=0, pady=(0, 14), sticky="nw")

version_badge = ctk.CTkLabel(
    header_frame,
    text=f"VERSION {APP_VERSION}",
    width=96,
    height=30,
    corner_radius=15,
    fg_color="#172554",
    text_color="#93c5fd",
    font=("Segoe UI", 11, "bold"),
)
version_badge.grid(row=0, column=2, rowspan=3, padx=(14, 16), pady=16)

controls_frame = ctk.CTkFrame(
    main_frame, corner_radius=16, fg_color=SURFACE, border_width=1, border_color=BORDER
)
controls_frame.grid(row=1, column=0, padx=(2, 5), pady=0, sticky="nsew")
controls_frame.grid_columnconfigure(0, weight=1)

controls_title = ctk.CTkLabel(
    controls_frame,
    text="Controls",
    font=("Segoe UI", 20, "bold"),
    text_color=TEXT,
)
controls_title.grid(row=0, column=0, padx=20, pady=(16, 10), sticky="w")

button_row = ctk.CTkFrame(controls_frame, fg_color="transparent")
button_row.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="ew")
button_row.grid_columnconfigure((0, 1), weight=1)

drop_zone = ctk.CTkFrame(
    controls_frame, corner_radius=12, border_width=1, border_color="#334155", fg_color=CARD
)
drop_zone.grid(row=2, column=0, padx=20, pady=(0, 10), sticky="ew")

drop_label = ctk.CTkLabel(
    drop_zone,
    text="Drag & Drop Files or Folders Here" if drag_drop_available else "Use Select Folder to choose media",
    font=("Segoe UI", 14),
    text_color=MUTED,
)
drop_label.pack(padx=20, pady=12, expand=True)

select_button = ctk.CTkButton(
    button_row,
    text="Select Folder",
    height=38,
    corner_radius=9,
    fg_color=PRIMARY,
    hover_color=PRIMARY_HOVER,
    font=("Segoe UI", 13, "bold"),
    command=select_folder_dialog,
)
select_button.grid(row=0, column=0, padx=(0, 8), sticky="ew")

save_button = ctk.CTkButton(
    button_row,
    text="Choose Save Location",
    height=38,
    corner_radius=9,
    fg_color="#1e293b",
    hover_color="#334155",
    border_width=1,
    border_color="#3b4b63",
    font=("Segoe UI", 13, "bold"),
    command=choose_save_location,
)
save_button.grid(row=0, column=1, padx=(8, 0), sticky="ew")

def update_quality_label(value):
    quality_label.configure(text=f"Quality: {int(value)} / 100")

quality_frame = ctk.CTkFrame(controls_frame, fg_color="transparent")
quality_frame.grid(row=3, column=0, padx=20, pady=(0, 10), sticky="ew")
quality_frame.grid_columnconfigure(0, weight=1)

quality_label = ctk.CTkLabel(quality_frame, text="", font=("Segoe UI", 14, "bold"), text_color=TEXT)
quality_label.grid(row=0, column=0, pady=(0, 4), sticky="w")

quality_slider = ctk.CTkSlider(
    quality_frame,
    from_=0,
    to=100,
    number_of_steps=100,
    command=update_quality_label,
    button_color="#60a5fa",
    button_hover_color="#93c5fd",
    progress_color=PRIMARY,
    fg_color="#243044",
)
quality_slider.set(initial_quality)
quality_slider.grid(row=1, column=0, sticky="ew")
update_quality_label(initial_quality)

compress_button = ctk.CTkButton(
    controls_frame,
    text="Start Compression",
    height=44,
    corner_radius=10,
    fg_color=PRIMARY,
    hover_color=PRIMARY_HOVER,
    font=("Segoe UI", 15, "bold"),
    command=compress_media,
)
compress_button.grid(row=4, column=0, padx=20, pady=(6, 10), sticky="ew")

open_button = ctk.CTkButton(
    controls_frame,
    text="Open Output Folder",
    height=38,
    corner_radius=9,
    fg_color="#1e293b",
    hover_color="#334155",
    border_width=1,
    border_color="#3b4b63",
    font=("Segoe UI", 13, "bold"),
    command=open_output_folder,
    state="disabled",
)
open_button.grid(row=5, column=0, padx=20, pady=(0, 10), sticky="ew")

history_button = ctk.CTkButton(
    controls_frame,
    text="View History",
    height=38,
    corner_radius=9,
    fg_color="#1e293b",
    hover_color="#334155",
    border_width=1,
    border_color="#3b4b63",
    font=("Segoe UI", 13, "bold"),
    command=open_history,
)
history_button.grid(row=6, column=0, padx=20, pady=(0, 10), sticky="ew")

progress_bar = ctk.CTkProgressBar(
    controls_frame, height=8, corner_radius=4, fg_color="#243044", progress_color="#3b82f6"
)
progress_bar.set(0)
progress_bar.grid(row=7, column=0, padx=20, pady=(0, 10), sticky="ew")

support_label = ctk.CTkLabel(
    controls_frame,
    text=(
        "Images: JPG, JPEG, PNG, WEBP, BMP, GIF, TIFF, HEIC\n"
        "Videos: MP4, AVI, MOV, MKV, WMV, FLV, WEBM, 3GP, M4V"
    ),
    font=("Segoe UI", 12),
    text_color=MUTED,
    justify="left",
)
support_label.grid(row=8, column=0, padx=20, pady=(0, 12), sticky="w")

status_frame = ctk.CTkFrame(
    main_frame, corner_radius=16, fg_color=SURFACE, border_width=1, border_color=BORDER
)
status_frame.grid(row=1, column=1, padx=(5, 2), pady=0, sticky="nsew")
status_frame.grid_columnconfigure(0, weight=1)

status_title = ctk.CTkLabel(
    status_frame,
    text="Status",
    font=("Segoe UI", 20, "bold"),
    text_color=TEXT,
)
status_title.grid(row=0, column=0, padx=20, pady=(16, 10), sticky="w")

paths_frame = ctk.CTkFrame(status_frame, corner_radius=12, fg_color=CARD)
paths_frame.grid(row=1, column=0, padx=18, pady=(0, 8), sticky="ew")
paths_frame.grid_columnconfigure((0, 1), weight=1)

selected_label = ctk.CTkLabel(
    paths_frame,
    text="Selected Folder",
    font=("Segoe UI", 11, "bold"),
    text_color=MUTED,
)
selected_label.grid(row=0, column=0, padx=(14, 8), pady=(10, 0), sticky="w")

selected_value = ctk.CTkLabel(
    paths_frame,
    text="Not Selected",
    font=("Segoe UI", 12),
    text_color="#e2e8f0",
    wraplength=230,
    justify="left",
)
selected_value.grid(row=1, column=0, padx=(14, 8), pady=(0, 10), sticky="w")

save_label = ctk.CTkLabel(
    paths_frame,
    text="Save Location",
    font=("Segoe UI", 11, "bold"),
    text_color=MUTED,
)
save_label.grid(row=0, column=1, padx=(8, 14), pady=(10, 0), sticky="w")

save_value = ctk.CTkLabel(
    paths_frame,
    text="Not Selected",
    font=("Segoe UI", 12),
    text_color="#e2e8f0",
    wraplength=230,
    justify="left",
)
save_value.grid(row=1, column=1, padx=(8, 14), pady=(0, 10), sticky="w")

stats_panel = ctk.CTkFrame(
    status_frame,
    corner_radius=12,
    fg_color=CARD,
)
stats_panel.grid(row=2, column=0, padx=18, pady=(0, 8), sticky="nsew")
stats_panel.grid_columnconfigure((0, 1), weight=1)

statistics_values = {}

current_file_label = ctk.CTkLabel(
    stats_panel, text="Current File", font=("Segoe UI", 11, "bold"), text_color=MUTED
)
current_file_label.grid(row=0, column=0, padx=14, pady=(10, 0), sticky="w")
statistics_values["current_file"] = ctk.CTkLabel(
    stats_panel,
    text="None",
    font=("Segoe UI", 12, "bold"),
    text_color="#bfdbfe",
    anchor="w",
    wraplength=470,
)
statistics_values["current_file"].grid(row=1, column=0, columnspan=2, padx=14, pady=(0, 6), sticky="ew")

metric_rows = (
    (("progress", "Progress"), ("files_processed", "Files Processed")),
    (("elapsed_time", "Elapsed Time"), ("average_time", "Average / File")),
    (("remaining_files", "Remaining Files"), ("remaining_time", "Estimated Remaining")),
    (("compression_speed", "Compression Speed"), ("failed_files", "Failed Files")),
    (("compressed_images", "Images"), ("compressed_videos", "Videos")),
    (("original_size", "Original Size"), ("compressed_size", "Compressed Size")),
    (("saved_size", "Saved Size"), ("saved_percentage", "Saved Percentage")),
)
for row_index, metric_row in enumerate(metric_rows, start=2):
    for column, (key, label_text) in enumerate(metric_row):
        cell = ctk.CTkFrame(stats_panel, fg_color="transparent")
        cell.grid(row=row_index, column=column, padx=14, pady=2, sticky="ew")
        ctk.CTkLabel(cell, text=label_text, font=("Segoe UI", 10), text_color=MUTED).pack(anchor="w")
        value_label = ctk.CTkLabel(cell, text="0", font=("Segoe UI", 12, "bold"), text_color=TEXT)
        value_label.pack(anchor="w")
        statistics_values[key] = value_label

log_title = ctk.CTkLabel(
    status_frame,
    text="Live Log",
    font=("Segoe UI", 13, "bold"),
    text_color=TEXT,
)
log_title.grid(row=3, column=0, padx=18, pady=(0, 4), sticky="w")

live_log_frame = ctk.CTkScrollableFrame(
    status_frame,
    height=92,
    corner_radius=12,
    fg_color=CARD,
    scrollbar_button_color=PRIMARY,
)
live_log_frame.grid(row=4, column=0, padx=18, pady=(0, 8), sticky="ew")

status_message_frame = ctk.CTkFrame(status_frame, corner_radius=12, fg_color=CARD_ALT)
status_message_frame.grid(row=5, column=0, padx=18, pady=(0, 14), sticky="ew")

status_value = ctk.CTkLabel(
    status_message_frame,
    text="Ready",
    font=("Segoe UI", 12),
    text_color="#dbeafe",
    justify="left",
    anchor="w",
    wraplength=500,
)
status_value.pack(fill="x", padx=14, pady=10)

update_statistics()

def on_closing():
    quality = int(quality_slider.get())
    save_settings(quality, app.geometry())
    app.destroy()

app.protocol("WM_DELETE_WINDOW", on_closing)
app.mainloop()
