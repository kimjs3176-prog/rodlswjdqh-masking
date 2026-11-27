"""창(GUI) 프로그램. 파이썬 기본 tkinter 만 사용한다."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .masker import MaskOptions, default_output_path, is_supported, mask_file, scan_file
from . import __version__
from .patterns import KINDS


class App(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master, padding=12)
        self.master = master
        self.files: list[Path] = []
        self.msgs: queue.Queue = queue.Queue()
        self.busy = False
        self.last_out_dir: Path | None = None

        master.title(f"PDF·엑셀·한글 개인정보 마스킹 v{__version__}")
        master.minsize(680, 640)
        self.pack(fill="both", expand=True)
        self.dnd = self._enable_drop()
        self._build()
        self.after(100, self._drain)

    # ---------- 화면 ----------
    def _build(self) -> None:
        # 1. 파일
        title = "1. 파일 (PDF, 엑셀 .xlsx, 한글 .hwpx)"
        if self.dnd:
            title += " — 파일이나 폴더를 이 창에 끌어다 놓아도 됩니다"
        box = ttk.LabelFrame(self, text=title, padding=8)
        box.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(box, height=6, selectmode="extended")
        self.listbox.pack(side="left", fill="both", expand=True)
        btns = ttk.Frame(box)
        btns.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(btns, text="파일 추가", command=self.add_files).pack(fill="x")
        ttk.Button(btns, text="폴더 추가", command=self.add_folder).pack(fill="x", pady=4)
        ttk.Button(btns, text="선택 삭제", command=self.remove_selected).pack(fill="x")
        ttk.Button(btns, text="모두 삭제", command=self.clear_files).pack(fill="x", pady=4)

        # 2. 항목
        box = ttk.LabelFrame(self, text="2. 가릴 항목", padding=8)
        box.pack(fill="x", pady=8)
        self.kind_vars: dict[str, tk.BooleanVar] = {}
        for i, (kind, label) in enumerate(KINDS.items()):
            var = tk.BooleanVar(value=True)
            self.kind_vars[kind] = var
            ttk.Checkbutton(box, text=label, variable=var).grid(
                row=i // 3, column=i % 3, sticky="w", padx=(0, 16), pady=2
            )
        row = (len(KINDS) + 2) // 3
        ttk.Label(box, text="추가로 가릴 단어 (쉼표로 구분: 이름, 주소 등)").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(8, 2)
        )
        self.keywords = tk.StringVar()
        ttk.Entry(box, textvariable=self.keywords).grid(row=row + 1, column=0, columnspan=3, sticky="ew")
        box.columnconfigure(2, weight=1)

        # 3. 방식
        box = ttk.LabelFrame(self, text="3. 방식", padding=8)
        box.pack(fill="x")
        self.partial = tk.BooleanVar(value=False)
        ttk.Radiobutton(box, text="전체 가리기", variable=self.partial, value=False).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            box, text="일부만 가리기 (주민번호 뒷자리, 전화번호 가운데 등)", variable=self.partial, value=True
        ).grid(row=0, column=1, sticky="w", padx=16)
        self.clear_meta = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="문서 속성(작성자 등) 삭제", variable=self.clear_meta).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        self.ocr = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            box, text="스캔(이미지) 문서는 글자 인식(OCR)으로 처리 — 쪽당 몇 초 걸립니다", variable=self.ocr
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        out = ttk.Frame(box)
        out.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(out, text="저장 폴더").pack(side="left")
        self.out_dir = tk.StringVar()
        ttk.Entry(out, textvariable=self.out_dir).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(out, text="찾기", command=self.pick_out_dir).pack(side="left")
        ttk.Label(box, text="비워 두면 원본과 같은 폴더에 '파일명_마스킹' 으로 저장됩니다. 원본은 바뀌지 않습니다.").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        box.columnconfigure(1, weight=1)

        # 실행
        run = ttk.Frame(self)
        run.pack(fill="x", pady=8)
        self.btn_scan = ttk.Button(run, text="미리 검사 (파일 변경 없음)", command=lambda: self.start(dry=True))
        self.btn_scan.pack(side="left")
        self.btn_run = ttk.Button(run, text="마스킹 실행", command=lambda: self.start(dry=False))
        self.btn_run.pack(side="left", padx=8)
        self.btn_open = ttk.Button(run, text="저장 폴더 열기", command=self.open_out_dir, state="disabled")
        self.btn_open.pack(side="left")
        self.progress = ttk.Progressbar(run, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(8, 0))

        # 결과
        box = ttk.LabelFrame(self, text="결과", padding=8)
        box.pack(fill="both", expand=True)
        self.log = tk.Text(box, height=10, state="disabled", wrap="word")
        scroll = ttk.Scrollbar(box, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")

    # ---------- 파일 목록 ----------
    def _enable_drop(self) -> bool:
        """창 어디에든 파일을 끌어다 놓을 수 있게 한다. 지원되지 않는 환경이면 False."""
        try:
            from tkinterdnd2 import DND_FILES

            self.master.drop_target_register(DND_FILES)
            self.master.dnd_bind("<<Drop>>", self.on_drop)
            return True
        except Exception:
            return False

    def on_drop(self, event) -> None:
        if self.busy:
            return
        self.add_paths(self.tk.splitlist(event.data))

    def add_paths(self, paths) -> None:
        """파일·폴더가 섞인 목록을 추가한다. 폴더는 안의 지원 파일을 모두 넣는다."""
        before, skipped = len(self.files), 0
        for p in map(Path, paths):
            if p.is_dir():
                self._add(sorted(f for f in p.rglob("*") if is_supported(f) and "_마스킹" not in f.stem))
            elif is_supported(p):
                self._add([p])
            else:
                skipped += 1
        added = len(self.files) - before
        if skipped:
            self._write(f"{added}개 추가, 지원하지 않는 형식 {skipped}개는 제외했습니다 (PDF, .xlsx, .xlsm, .hwpx 만 가능. 옛 한글 .hwp 는 hwpx 로 다시 저장하세요).")

    def _add(self, paths) -> None:
        for p in map(Path, paths):
            if is_supported(p) and p not in self.files:
                self.files.append(p)
                self.listbox.insert("end", str(p))

    def add_files(self) -> None:
        self._add(filedialog.askopenfilenames(
                title="파일 선택",
                filetypes=[
                    ("PDF·엑셀·한글", "*.pdf *.xlsx *.xlsm *.hwpx"),
                    ("PDF", "*.pdf"),
                    ("엑셀", "*.xlsx *.xlsm"),
                    ("한글", "*.hwpx"),
                ],
            ))

    def add_folder(self) -> None:
        folder = filedialog.askdirectory(title="폴더 선택")
        if folder:
            self.add_paths([folder])

    def remove_selected(self) -> None:
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)
            del self.files[i]

    def clear_files(self) -> None:
        self.listbox.delete(0, "end")
        self.files.clear()

    def pick_out_dir(self) -> None:
        folder = filedialog.askdirectory(title="저장 폴더 선택")
        if folder:
            self.out_dir.set(folder)

    def open_out_dir(self) -> None:
        if not self.last_out_dir:
            return
        path = str(self.last_out_dir)
        if hasattr(os, "startfile"):
            os.startfile(path)  # Windows
        else:
            import subprocess, sys

            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])

    # ---------- 실행 ----------
    def options(self) -> MaskOptions:
        return MaskOptions(
            kinds=[k for k, v in self.kind_vars.items() if v.get()],
            keywords=[w.strip() for w in self.keywords.get().split(",") if w.strip()],
            partial=self.partial.get(),
            clear_metadata=self.clear_meta.get(),
            ocr=self.ocr.get(),
        )

    def start(self, dry: bool) -> None:
        if self.busy:
            return
        if not self.files:
            messagebox.showinfo("안내", "먼저 PDF, 엑셀 또는 한글(hwpx) 파일을 추가하세요.")
            return
        opts = self.options()
        if not list(opts.kinds) and not opts.keywords:
            messagebox.showinfo("안내", "가릴 항목을 하나 이상 선택하세요.")
            return
        self.busy = True
        for b in (self.btn_scan, self.btn_run):
            b.configure(state="disabled")
        self.progress.configure(maximum=len(self.files), value=0)
        self._write("─" * 40 + ("\n[미리 검사]" if dry else "\n[마스킹 실행]"))
        out_dir = self.out_dir.get().strip() or None
        threading.Thread(
            target=self._work, args=(list(self.files), opts, out_dir, dry), daemon=True
        ).start()

    def _work(self, files, opts, out_dir, dry) -> None:
        total = 0
        for src in files:
            try:
                if dry:
                    result = scan_file(src, opts)
                    self.msgs.put(("log", result.summary().replace("마스킹", "탐지", 1)))
                else:
                    result = mask_file(src, default_output_path(src, out_dir), opts)
                    self.msgs.put(("log", result.summary() + f"\n  → {result.output}"))
                    self.msgs.put(("out", result.output.parent))
                total += len(result.findings)
            except Exception as exc:
                self.msgs.put(("log", f"{Path(src).name}: 실패 — {exc}"))
            self.msgs.put(("step", None))
        self.msgs.put(("done", (total, dry)))

    def _drain(self) -> None:
        try:
            while True:
                kind, data = self.msgs.get_nowait()
                if kind == "log":
                    self._write(data)
                elif kind == "step":
                    self.progress.step(1) if self.progress["value"] + 1 < self.progress["maximum"] else self.progress.configure(value=self.progress["maximum"])
                elif kind == "out":
                    self.last_out_dir = data
                    self.btn_open.configure(state="normal")
                elif kind == "done":
                    total, dry = data
                    self.busy = False
                    for b in (self.btn_scan, self.btn_run):
                        b.configure(state="normal")
                    self._write(f"완료: 총 {total}건 {'탐지' if dry else '마스킹'}")
                    if not dry:
                        self._write("※ 자동 탐지는 완벽하지 않습니다. 배포 전 결과 파일을 직접 확인하세요.")
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _write(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def make_root() -> tk.Tk:
    """끌어다 놓기를 쓸 수 있으면 그 기능이 들어간 창을, 아니면 기본 창을 만든다."""
    try:
        from tkinterdnd2 import TkinterDnD

        return TkinterDnD.Tk()
    except Exception:
        return tk.Tk()


def run() -> None:
    root = make_root()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    run()
