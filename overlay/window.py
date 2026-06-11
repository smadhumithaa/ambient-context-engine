"""
Floating overlay window - always on top, excluded from Windows screen capture.
Uses WDA_EXCLUDEFROMCAPTURE flag (Windows 10 2004+) so it's invisible in Zoom/Meet/Teams.

Features:
- System tray icon: click to show/hide overlay from anywhere
- Global hotkeys: work even when overlay is hidden (Ctrl+Shift+A = show/hide)
- Pause / resume listening
- X button to close the app cleanly
"""
import sys
import threading
import tkinter as tk
import ctypes
from ctypes import wintypes
from datetime import datetime

# Load user32 with proper types
_user32   = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_user32.GetAncestor.restype  = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
_user32.SetWindowDisplayAffinity.restype  = wintypes.BOOL
_user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]

GA_ROOT = 2  # GetAncestor flag: walk up to the true root window

# Force UTF-8 output on Windows
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Windows API constants
WDA_EXCLUDEFROMCAPTURE = 0x00000011


def _make_tray_icon_image(size=64, color="#00FF9C", bg="#0D0F14"):
    """Generate a simple coloured circle icon for the system tray using Pillow."""
    try:
        from PIL import Image, ImageDraw
        img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Background
        r, g, b = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)
        draw.ellipse([0, 0, size - 1, size - 1], fill=(r, g, b, 230))
        # Green dot
        r2, g2, b2 = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        pad = size // 5
        draw.ellipse([pad, pad, size - pad - 1, size - pad - 1],
                     fill=(r2, g2, b2, 255))
        return img
    except Exception:
        return None


class CopilotOverlay:
    def __init__(
        self,
        ai_provider:  str = "anthropic",
        ai_model:     str = "",
        stt_provider: str = "faster-whisper",
        stt_model:    str = "base",
    ):
        self.root       = tk.Tk()
        self.messages   = []
        self.is_visible = True
        self.is_paused  = False
        self._on_close_cb  = None
        self._tray_icon    = None
        self._ai_provider  = ai_provider
        self._ai_model     = ai_model
        self._stt_provider = stt_provider
        self._stt_model    = stt_model

        self._setup_window()
        self._setup_ui()
        self._setup_global_hotkeys()
        self._setup_tray_icon()
        # Delay exclusion until window is fully rendered and has a real HWND
        self.root.after(300, self._apply_screen_capture_exclusion)

    # ── Window setup ──────────────────────────────────────────────

    def _setup_window(self):
        self.root.title("AI Copilot")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.93)

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width, height = 430, 540
        x = screen_w - width - 20
        y = screen_h - height - 60
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.configure(bg="#0D0F14")

        self.root.bind("<Button-1>", self._start_drag)
        self.root.bind("<B1-Motion>", self._do_drag)

    # ── UI ────────────────────────────────────────────────────────

    def _setup_ui(self):
        # ── Header ──────────────────────────────────────────────
        header = tk.Frame(self.root, bg="#0D0F14", pady=6)
        header.pack(fill="x", padx=10)

        dot_canvas = tk.Canvas(header, width=10, height=10, bg="#0D0F14",
                               highlightthickness=0)
        dot_canvas.create_oval(1, 1, 9, 9, fill="#00FF9C", outline="")
        dot_canvas.pack(side="left", padx=(0, 6), pady=2)

        title = tk.Label(header, text="AI Copilot", bg="#0D0F14",
                         fg="#E8EAF0", font=("Segoe UI Semibold", 11))
        title.pack(side="left")

        # Provider badge
        provider_letter = {"anthropic": "A", "gemini": "G", "groq": "Q"}.get(
            self._ai_provider.lower(), "AI"
        )
        model_short = self._ai_model.split("-")[0] if self._ai_model else self._ai_provider
        badge = tk.Label(
            header,
            text=f"[{provider_letter}] {self._ai_provider}:{model_short}",
            bg="#141720", fg="#6EE7B7",
            font=("Segoe UI", 7), padx=5, pady=2,
        )
        badge.pack(side="left", padx=(8, 0))

        # Right-side controls
        ctrl = tk.Frame(header, bg="#0D0F14")
        ctrl.pack(side="right")

        self.status_label = tk.Label(
            ctrl, text="Starting...",
            bg="#0D0F14", fg="#6B7280", font=("Segoe UI", 8)
        )
        self.status_label.pack(side="left", padx=(0, 6))

        self._make_btn(ctrl, "⊘", "#4B5563", self.clear_messages)
        self.btn_pause = self._make_btn(ctrl, "⏸", "#4B5563", self.toggle_pause)
        self._make_btn(ctrl, "◐", "#4B5563", self.toggle_visibility)
        self._make_btn(ctrl, "✕", "#6B2020", self._quit, hover_fg="#FF4444")

        # ── Divider ─────────────────────────────────────────────
        tk.Frame(self.root, bg="#1E2130", height=1).pack(fill="x", padx=10)

        # ── Message area ────────────────────────────────────────
        msg_container = tk.Frame(self.root, bg="#0D0F14")
        msg_container.pack(fill="both", expand=True, padx=10, pady=(6, 0))

        self.canvas = tk.Canvas(msg_container, bg="#0D0F14",
                                highlightthickness=0, bd=0)
        scrollbar = tk.Scrollbar(msg_container, orient="vertical",
                                 command=self.canvas.yview,
                                 bg="#1E2130", troughcolor="#0D0F14", width=4)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.msg_frame = tk.Frame(self.canvas, bg="#0D0F14")
        self.canvas_window = self.canvas.create_window(
            (0, 0), window=self.msg_frame, anchor="nw"
        )
        self.msg_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>",   self._on_canvas_configure)

        # ── Footer ──────────────────────────────────────────────
        footer = tk.Frame(self.root, bg="#0A0C10", pady=4)
        footer.pack(fill="x")

        self.mic_label = tk.Label(
            footer, text="Waiting for questions...",
            bg="#0A0C10", fg="#374151", font=("Segoe UI", 8)
        )
        self.mic_label.pack(side="left", padx=10)

        tk.Label(
            footer,
            text="Ctrl+Shift+A = show/hide  |  Alt+P = pause  |  Alt+Q = quit",
            bg="#0A0C10", fg="#1F2937", font=("Segoe UI", 7)
        ).pack(side="right", padx=8)

        self._show_placeholder()

    def _make_btn(self, parent, text, fg, command, hover_fg=None):
        btn = tk.Label(parent, text=text, bg="#0D0F14", fg=fg,
                       font=("Segoe UI", 12), cursor="hand2", padx=3)
        btn.pack(side="left", padx=1)
        btn.bind("<Button-1>", lambda e: command())
        _hfg = hover_fg or "#E8EAF0"
        btn.bind("<Enter>", lambda e: btn.config(fg=_hfg))
        btn.bind("<Leave>", lambda e: btn.config(fg=fg))
        return btn

    def _show_placeholder(self):
        self.placeholder = tk.Label(
            self.msg_frame,
            text="Listening to your call...\n"
                 "Answers appear here automatically.\n\n"
                 "Tip: type a question in the terminal to test.",
            bg="#0D0F14", fg="#2D3748",
            font=("Segoe UI", 9), justify="center"
        )
        self.placeholder.pack(expand=True, pady=60)

    # ── System tray ───────────────────────────────────────────────

    def _setup_tray_icon(self):
        """Create a system tray icon so the overlay can be shown after hiding."""
        try:
            import pystray

            img = _make_tray_icon_image()
            if img is None:
                print("[Overlay] Tray icon skipped (Pillow unavailable).")
                return

            menu = pystray.Menu(
                pystray.MenuItem("Show / Hide  (Ctrl+Shift+A)",
                                 lambda icon, item: self._tray_show()),
                pystray.MenuItem("Pause / Resume",
                                 lambda icon, item: self.root.after(0, self.toggle_pause)),
                pystray.MenuItem("Clear messages",
                                 lambda icon, item: self.root.after(0, self.clear_messages)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit",
                                 lambda icon, item: self.root.after(0, self._quit)),
            )

            self._tray_icon = pystray.Icon(
                "AI Copilot", img, "AI Copilot", menu
            )

            # Run tray in its own daemon thread
            t = threading.Thread(target=self._tray_icon.run, daemon=True)
            t.start()
            print("[Overlay] System tray icon active — right-click it to show/hide.")

        except Exception as exc:
            print(f"[Overlay] Tray icon failed: {exc}")

    def _tray_show(self):
        """Called from tray menu — show the overlay (thread-safe)."""
        self.root.after(0, self._ensure_visible)

    def _ensure_visible(self):
        """Make overlay visible and bring to front."""
        if not self.is_visible:
            self.is_visible = True
            self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        # Re-apply exclusion after window is shown (deiconify resets it)
        self.root.after(100, self._apply_screen_capture_exclusion)

    # ── Global hotkeys (work even when window is hidden) ──────────

    def _setup_global_hotkeys(self):
        """Register OS-level hotkeys using the keyboard library."""
        try:
            import keyboard as kb

            # Ctrl+Shift+A  →  show / hide
            kb.add_hotkey("ctrl+shift+a", lambda: self.root.after(0, self.toggle_visibility),
                          suppress=False)

            # Alt+P  →  pause / resume
            kb.add_hotkey("alt+p", lambda: self.root.after(0, self.toggle_pause),
                          suppress=False)

            # Alt+Q  →  quit
            kb.add_hotkey("alt+q", lambda: self.root.after(0, self._quit),
                          suppress=False)

            # Alt+C  →  clear
            kb.add_hotkey("alt+c", lambda: self.root.after(0, self.clear_messages),
                          suppress=False)

            print("[Overlay] Global hotkeys active:")
            print("          Ctrl+Shift+A = show/hide  |  Alt+P = pause  |  Alt+Q = quit  |  Alt+C = clear")

        except Exception as exc:
            # Fallback to Tkinter-only hotkeys (only work when window focused)
            print(f"[Overlay] Global hotkeys unavailable ({exc}), using Tkinter bindings instead.")
            self.root.bind_all("<Alt-h>", lambda e: self.toggle_visibility())
            self.root.bind_all("<Alt-H>", lambda e: self.toggle_visibility())
            self.root.bind_all("<Alt-p>", lambda e: self.toggle_pause())
            self.root.bind_all("<Alt-P>", lambda e: self.toggle_pause())
            self.root.bind_all("<Alt-c>", lambda e: self.clear_messages())
            self.root.bind_all("<Alt-C>", lambda e: self.clear_messages())
            self.root.bind_all("<Alt-q>", lambda e: self._quit())
            self.root.bind_all("<Alt-Q>", lambda e: self._quit())

    # ── Public API ────────────────────────────────────────────────

    def add_message(self, sender: str, text: str, role: str = "assistant"):
        def _add():
            if hasattr(self, 'placeholder') and self.placeholder.winfo_exists():
                self.placeholder.destroy()
            ts = datetime.now().strftime("%H:%M")
            outer = tk.Frame(self.msg_frame, bg="#0D0F14")
            outer.pack(fill="x", pady=(0, 8))
            if role == "user":
                tk.Label(outer, text=f"? {ts}", bg="#0D0F14", fg="#4B5563",
                         font=("Segoe UI", 7)).pack(anchor="w", padx=2)
                bubble = tk.Frame(outer, bg="#141720", padx=8, pady=6)
                bubble.pack(fill="x")
                tk.Label(bubble, text=text, bg="#141720", fg="#6B7280",
                         font=("Segoe UI", 8), wraplength=380, justify="left").pack(anchor="w")
            else:
                tk.Label(outer, text=f">> Copilot  {ts}", bg="#0D0F14", fg="#00FF9C",
                         font=("Segoe UI Semibold", 8)).pack(anchor="w", padx=2)
                bubble = tk.Frame(outer, bg="#0F1A14", padx=10, pady=8,
                                  highlightbackground="#00FF9C", highlightthickness=1)
                bubble.pack(fill="x")
                tk.Label(bubble, text=text, bg="#0F1A14", fg="#D1FAE5",
                         font=("Segoe UI", 9), wraplength=380, justify="left").pack(anchor="w")
            self.messages.append({"sender": sender, "text": text, "role": role})
            self.root.after(50, self._scroll_to_bottom)
        self.root.after(0, _add)

    def set_status(self, status: str):
        def _update():
            self.status_label.config(text=status)
            if "Listening" in status or "listening" in status:
                self.mic_label.config(text="Listening...", fg="#374151")
            elif "Thinking" in status or "thinking" in status:
                self.mic_label.config(text="Generating answer...", fg="#92400E")
            elif "Paused" in status or "paused" in status:
                self.mic_label.config(text="Paused", fg="#6B4C10")
        self.root.after(0, _update)

    def clear_messages(self):
        for widget in self.msg_frame.winfo_children():
            widget.destroy()
        self.messages.clear()
        self._show_placeholder()

    def toggle_visibility(self):
        """Show overlay if hidden, hide if visible."""
        self.is_visible = not self.is_visible
        if self.is_visible:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            print("[Overlay] Shown.")
        else:
            self.root.withdraw()
            print("[Overlay] Hidden. Press Ctrl+Shift+A or click the tray icon to bring it back.")

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.btn_pause.config(text="▶", fg="#F59E0B")
            self.set_status("Paused")
            print("[Copilot] Paused.")
        else:
            self.btn_pause.config(text="⏸", fg="#4B5563")
            self.set_status("[Listening]")
            print("[Copilot] Resumed.")

    def set_on_close(self, callback):
        self._on_close_cb = callback

    def _quit(self):
        print("[Copilot] Closing...")
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        if self._on_close_cb:
            try:
                self._on_close_cb()
            except Exception:
                pass
        self.root.after(100, self.root.destroy)

    # ── Screen capture exclusion ──────────────────────────────────

    def _apply_screen_capture_exclusion(self):
        """
        Apply WDA_EXCLUDEFROMCAPTURE to the TRUE top-level Win32 HWND.
        Must be called after the window is fully rendered (use root.after delay).
        With overrideredirect=True, winfo_id() returns a child window —
        we must walk up with GetAncestor(GA_ROOT) to find the real top-level handle.
        """
        try:
            self.root.update_idletasks()   # ensure window exists in Win32
            child_hwnd = self.root.winfo_id()

            # Walk to the true top-level window
            root_hwnd = _user32.GetAncestor(child_hwnd, GA_ROOT)
            hwnd = root_hwnd if root_hwnd else child_hwnd

            print(f"[Overlay] Applying capture exclusion to HWND {hwnd} (child={child_hwnd}, root={root_hwnd})")

            result = _user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
            if result:
                print("[Overlay] Screen capture exclusion applied — overlay is INVISIBLE to Zoom/Meet/Teams/screenshots.")
            else:
                err = _kernel32.GetLastError()
                print(f"[Overlay] SetWindowDisplayAffinity FAILED (Win32 error {err}).")
                if err == 5:
                    print("[Overlay] Error 5 = Access Denied. Try running as Administrator.")
        except Exception as exc:
            print(f"[Overlay] Screen exclusion error: {exc}")

    # ── Drag ──────────────────────────────────────────────────────

    def _start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _do_drag(self, event):
        x = self.root.winfo_x() + (event.x - self._drag_x)
        y = self.root.winfo_y() + (event.y - self._drag_y)
        self.root.geometry(f"+{x}+{y}")

    # ── Canvas helpers ────────────────────────────────────────────

    def _on_frame_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _scroll_to_bottom(self):
        self.canvas.yview_moveto(1.0)

    def run(self):
        self.root.mainloop()
