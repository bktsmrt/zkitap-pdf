# -*- coding: utf-8 -*-
"""Z-Kitap Sayfa Yakalayıcı -> PDF (Windows)"""
import ctypes
import os
import queue
import shutil
import tempfile
import threading
import time
import tkinter as tk
import uuid
from tkinter import filedialog, messagebox

# Ekran koordinatlarının fiziksel piksel olması için (mss / pyautogui ile uyum)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import customtkinter as ctk
import img2pdf
import mss
import pyautogui
from PIL import Image
from pynput import keyboard

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def grab_region(region, path):
    """Bölgeyi RGB PNG olarak kaydeder (kayıpsız)."""
    left, top, w, h = region
    with mss.mss() as sct:
        shot = sct.grab({"left": left, "top": top, "width": w, "height": h})
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        img.save(path, "PNG")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Z-Kitap → PDF")
        self.geometry("460x640")
        self.resizable(False, False)

        self.region = None      # (left, top, w, h)
        self.click_pt = None    # (x, y)
        self.use_key = tk.BooleanVar(value=False)

        self.cancel_ev = threading.Event()
        self.pause_ev = threading.Event()   # set = duraklatıldı
        self.running = False
        self.q = queue.Queue()

        self._build_ui()
        self.listener = keyboard.Listener(on_press=self._on_key)
        self.listener.daemon = True
        self.listener.start()
        self.after(100, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- Arayüz ----------------
    def _build_ui(self):
        ctk.CTkLabel(self, text="Z-Kitap → PDF",
                     font=ctk.CTkFont(size=24, weight="bold")).pack(pady=(18, 4))
        ctk.CTkLabel(self, text="ESC: iptal   |   F9: duraklat / devam",
                     text_color="gray70").pack(pady=(0, 12))

        self.btn1 = ctk.CTkButton(self, text="1. Kitap Alanını Seç", height=40,
                                  command=lambda: self._open_overlay("rect"))
        self.btn1.pack(fill="x", padx=30, pady=4)
        self.lbl1 = ctk.CTkLabel(self, text="Alan seçilmedi", text_color="gray60")
        self.lbl1.pack()

        self.btn2 = ctk.CTkButton(self, text="2. Sonraki Sayfa Butonunu Seç", height=40,
                                  command=lambda: self._open_overlay("point"))
        self.btn2.pack(fill="x", padx=30, pady=(12, 4))
        self.chk = ctk.CTkCheckBox(
            self, text="Tıklamak yerine KLAVYE SAĞ OK tuşunu kullan",
            variable=self.use_key, command=self._on_chk)
        self.chk.pack(padx=30, pady=4, anchor="w")
        self.lbl2 = ctk.CTkLabel(self, text="Buton seçilmedi", text_color="gray60")
        self.lbl2.pack()

        self.btn3 = ctk.CTkButton(self, text="3. Deneme Çekimi Yap", height=40,
                                  command=self._test_shot)
        self.btn3.pack(fill="x", padx=30, pady=(12, 4))

        frm = ctk.CTkFrame(self)
        frm.pack(fill="x", padx=30, pady=(14, 4))
        ctk.CTkLabel(frm, text="Toplam Sayfa Sayısı").grid(row=0, column=0, padx=10, pady=6, sticky="w")
        self.ent_total = ctk.CTkEntry(frm, width=100)
        self.ent_total.insert(0, "100")
        self.ent_total.grid(row=0, column=1, padx=10, pady=6)
        ctk.CTkLabel(frm, text="Sayfa Bekleme Süresi (sn)").grid(row=1, column=0, padx=10, pady=6, sticky="w")
        self.ent_delay = ctk.CTkEntry(frm, width=100)
        self.ent_delay.insert(0, "1.5")
        self.ent_delay.grid(row=1, column=1, padx=10, pady=6)

        self.btn4 = ctk.CTkButton(self, text="4. BAŞLAT VE PDF OLUŞTUR", height=46,
                                  fg_color="#1f8a3b", hover_color="#176b2d",
                                  font=ctk.CTkFont(size=15, weight="bold"),
                                  command=self._start)
        self.btn4.pack(fill="x", padx=30, pady=(14, 6))

        self.btn_cancel = ctk.CTkButton(self, text="İptal (ESC)", height=30,
                                        fg_color="#a33", hover_color="#822",
                                        command=self._cancel, state="disabled")
        self.btn_cancel.pack(fill="x", padx=30, pady=(0, 10))

        self.bar = ctk.CTkProgressBar(self)
        self.bar.set(0)
        self.bar.pack(fill="x", padx=30, pady=(6, 4))
        self.lbl_prog = ctk.CTkLabel(self, text="Hazır")
        self.lbl_prog.pack()

    def _on_chk(self):
        if self.use_key.get():
            self.lbl2.configure(text="Sağ ok tuşu kullanılacak", text_color="#6c6")
        elif self.click_pt:
            self.lbl2.configure(text=f"Buton: {self.click_pt}", text_color="#6c6")
        else:
            self.lbl2.configure(text="Buton seçilmedi", text_color="gray60")

    # ---------------- Seçim katmanı ----------------
    def _open_overlay(self, mode):
        if self.running:
            return
        self.withdraw()
        self.after(300, lambda: self._make_overlay(mode))

    def _make_overlay(self, mode):
        ov = tk.Toplevel(self)
        ov.attributes("-fullscreen", True)
        ov.attributes("-topmost", True)
        ov.attributes("-alpha", 0.30)
        ov.configure(bg="black")
        cv = tk.Canvas(ov, bg="black", highlightthickness=0, cursor="crosshair")
        cv.pack(fill="both", expand=True)
        msg = ("Kitap sayfa alanını fareyle sürükleyerek seçin (ESC: vazgeç)"
               if mode == "rect" else
               "'Sonraki Sayfa' düğmesine tıklayın (ESC: vazgeç)")
        cv.create_text(ov.winfo_screenwidth() // 2, 60, text=msg,
                       fill="white", font=("Segoe UI", 20, "bold"))
        state = {"x0": 0, "y0": 0, "rx0": 0, "ry0": 0, "rect": None}

        def close():
            ov.destroy()
            self.deiconify()

        def on_press(e):
            if mode == "point":
                self.click_pt = (e.x_root, e.y_root)
                self.lbl2.configure(text=f"Buton: {self.click_pt}", text_color="#6c6")
                self.use_key.set(False)
                close()
                return
            state.update(x0=e.x, y0=e.y, rx0=e.x_root, ry0=e.y_root)
            state["rect"] = cv.create_rectangle(e.x, e.y, e.x, e.y,
                                                outline="red", width=3)

        def on_move(e):
            if mode == "rect" and state["rect"]:
                cv.coords(state["rect"], state["x0"], state["y0"], e.x, e.y)

        def on_release(e):
            if mode != "rect" or not state["rect"]:
                return
            l, r = sorted((state["rx0"], e.x_root))
            t, b = sorted((state["ry0"], e.y_root))
            w, h = r - l, b - t
            if w < 20 or h < 20:
                cv.delete(state["rect"])
                state["rect"] = None
                return
            self.region = (l, t, w, h)
            self.lbl1.configure(text=f"Alan: {w}×{h} px  (x={l}, y={t})", text_color="#6c6")
            close()

        cv.bind("<ButtonPress-1>", on_press)
        cv.bind("<B1-Motion>", on_move)
        cv.bind("<ButtonRelease-1>", on_release)
        ov.bind("<Escape>", lambda e: close())
        ov.focus_force()

    # ---------------- Sayfa çevirme ----------------
    @staticmethod
    def _turn(use_key, pt):
        if use_key:
            pyautogui.press("right")
        else:
            pyautogui.click(pt[0], pt[1])

    def _check_ready(self):
        if not self.region:
            messagebox.showwarning("Eksik", "Önce kitap alanını seçin (Adım 1).")
            return False
        if not self.use_key.get() and not self.click_pt:
            messagebox.showwarning("Eksik", "Önce 'Sonraki Sayfa' butonunu seçin veya klavye seçeneğini işaretleyin (Adım 2).")
            return False
        return True

    # ---------------- Deneme çekimi ----------------
    def _test_shot(self):
        if self.running or not self._check_ready():
            return
        self.withdraw()
        self.after(500, self._do_test)

    def _do_test(self):
        tmpdir = tempfile.mkdtemp(prefix="zkitap_test_")
        path = os.path.join(tmpdir, f"{uuid.uuid4().hex}.png")
        try:
            grab_region(self.region, path)
            self._turn(self.use_key.get(), self.click_pt)
            img = Image.open(path)
            img.load()
        except Exception as ex:
            self.deiconify()
            messagebox.showerror("Hata", str(ex))
            return
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        self.deiconify()
        self._show_preview(img)

    def _show_preview(self, img):
        top = ctk.CTkToplevel(self)
        top.title("Deneme Çekimi Önizleme")
        top.attributes("-topmost", True)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        maxw, maxh = int(sw * 0.6), int(sh * 0.7)
        ratio = min(maxw / img.width, maxh / img.height, 1.0)
        size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
        cimg = ctk.CTkImage(light_image=img, dark_image=img, size=size)
        lbl = ctk.CTkLabel(top, image=cimg, text="")
        lbl.image = cimg
        lbl.pack(padx=10, pady=10)
        ctk.CTkLabel(top, text="Sayfa çevirme tetiklendi. Görüntü doğruysa kapatıp başlatabilirsiniz.").pack()
        ctk.CTkButton(top, text="Kapat", command=top.destroy).pack(pady=10)

    # ---------------- Başlat ----------------
    def _start(self):
        if self.running or not self._check_ready():
            return
        try:
            total = int(self.ent_total.get().strip())
            delay = float(self.ent_delay.get().strip().replace(",", "."))
            if total < 1 or delay < 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Geçersiz değer", "Sayfa sayısı pozitif tam sayı, bekleme süresi 0 veya daha büyük sayı olmalıdır.")
            return

        # Uygulama penceresi yakalama alanını / butonu kapatıyor mu?
        self.update_idletasks()
        wl, wt = self.winfo_rootx(), self.winfo_rooty()
        wr, wb = wl + self.winfo_width(), wt + self.winfo_height()
        l, t, w, h = self.region
        areas = [(l, t, l + w, t + h)]
        if not self.use_key.get():
            areas.append((self.click_pt[0], self.click_pt[1], self.click_pt[0] + 1, self.click_pt[1] + 1))
        for (a, b, c, d) in areas:
            if a < wr and c > wl and b < wb and d > wt:
                messagebox.showwarning(
                    "Pencere engelliyor",
                    "Bu uygulamanın penceresi seçtiğiniz alanın veya butonun üzerinde.\n"
                    "Lütfen pencereyi kitabın dışına taşıyıp tekrar deneyin.")
                return

        pdf_path = filedialog.asksaveasfilename(
            title="PDF'i kaydet", defaultextension=".pdf",
            filetypes=[("PDF belgesi", "*.pdf")], initialfile="kitap.pdf")
        if not pdf_path:
            return

        self.cancel_ev.clear()
        self.pause_ev.clear()
        self.running = True
        self._set_buttons(False)
        self.bar.set(0)
        threading.Thread(
            target=self._worker,
            args=(self.region, self.click_pt, self.use_key.get(), total, delay, pdf_path),
            daemon=True).start()

    def _set_buttons(self, enabled):
        st = "normal" if enabled else "disabled"
        for b in (self.btn1, self.btn2, self.btn3, self.btn4):
            b.configure(state=st)
        self.btn_cancel.configure(state="disabled" if enabled else "normal")

    def _cancel(self):
        if self.running:
            self.cancel_ev.set()

    # ---------------- Arka plan işi ----------------
    def _sleep(self, secs):
        """İptal ve duraklatmayı gözeten bekleme. İptalse False döner."""
        end = time.time() + secs
        while True:
            if self.cancel_ev.is_set():
                return False
            if self.pause_ev.is_set():
                self.q.put(("status", "Duraklatıldı (devam için F9)"))
                time.sleep(0.1)
                end += 0.1
                continue
            if time.time() >= end:
                return True
            time.sleep(0.05)

    def _worker(self, region, pt, use_key, total, delay, pdf_path):
        tmpdir = tempfile.mkdtemp(prefix="zkitap_")
        run_id = uuid.uuid4().hex[:8]
        files = []
        try:
            for s in range(5, 0, -1):
                self.q.put(("status", f"{s} sn sonra başlıyor… Kitap penceresine tıklayın!"))
                if not self._sleep(1):
                    self.q.put(("done", "İşlem iptal edildi."))
                    return
            for i in range(1, total + 1):
                if not self._sleep(0):
                    self.q.put(("done", "İşlem iptal edildi."))
                    return
                path = os.path.join(tmpdir, f"{run_id}_{i:06d}.png")
                grab_region(region, path)
                files.append(path)
                self.q.put(("progress", i, total))
                if i < total:
                    self._turn(use_key, pt)
                    if not self._sleep(delay):
                        self.q.put(("done", "İşlem iptal edildi."))
                        return
            self.q.put(("status", "PDF oluşturuluyor…"))
            files.sort()  # sıfır dolgulu numaralar -> sayısal sıra
            with open(pdf_path, "wb") as f:
                f.write(img2pdf.convert(files))  # PNG'ler yeniden sıkıştırılmadan gömülür
            self.q.put(("done", f"Tamamlandı!\n{pdf_path}"))
        except Exception as ex:
            self.q.put(("error", str(ex)))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ---------------- Kuyruk ve klavye ----------------
    def _poll(self):
        try:
            while True:
                m = self.q.get_nowait()
                if m[0] == "progress":
                    _, i, total = m
                    self.bar.set(i / total)
                    self.lbl_prog.configure(text=f"Çekiliyor: {i} / {total}")
                elif m[0] == "status":
                    self.lbl_prog.configure(text=m[1])
                elif m[0] in ("done", "error"):
                    self.running = False
                    self.pause_ev.clear()
                    self._set_buttons(True)
                    self.lbl_prog.configure(text=m[1].split("\n")[0])
                    self.deiconify()
                    (messagebox.showinfo if m[0] == "done" else messagebox.showerror)(
                        "Bilgi" if m[0] == "done" else "Hata", m[1])
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _on_key(self, key):
        if not self.running:
            return
        if key == keyboard.Key.esc:
            self.cancel_ev.set()
        elif key == keyboard.Key.f9:
            if self.pause_ev.is_set():
                self.pause_ev.clear()
            else:
                self.pause_ev.set()

    def _on_close(self):
        self.cancel_ev.set()
        try:
            self.listener.stop()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
