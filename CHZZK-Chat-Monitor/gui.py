import threading
import queue
import json
import os
import logging
from pathlib import Path
from tkinter import Tk, Text, StringVar, Frame, Toplevel, END, LEFT, BOTH, X, Y
from tkinter import messagebox
from tkinter import ttk
from tkinter import font as tkfont
from dotenv import load_dotenv

from run import ChzzkChat, get_logger
from config_store import load_config, save_config


class TextQueueHandler:
	def __init__(self, text_widget: Text):
		self.text_widget = text_widget
		self.queue = queue.Queue()
		self.text_widget.after(100, self._poll)

	def write(self, message: str):
		self.queue.put(message)

	def _poll(self):
		try:
			while True:
				msg = self.queue.get_nowait()
				self.text_widget.insert(END, msg)
				self.text_widget.see(END)
		except queue.Empty:
			pass
		finally:
			self.text_widget.after(100, self._poll)


def load_cookies() -> dict:
	p = Path('cookies.json')
	if not p.exists():
		return {"NID_AUT": "", "NID_SES": ""}
	return json.loads(p.read_text(encoding='utf-8'))


def save_cookies(data: dict) -> None:
	Path('cookies.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _parse_keywords(text: str) -> list[str]:
	if not text:
		return []
	normalized = text.replace(',', ' ').replace('/', ' ')
	return [seg.strip() for seg in normalized.split() if seg.strip()]


def _positive_int(text: str, default: int) -> int:
	if isinstance(text, (int, float)):
		value = int(text)
		return value if value >= 1 else default
	text = (str(text or '')).strip()
	if text.isdigit():
		value = int(text)
		return value if value >= 1 else default
	return default


def _sanitize_env_map(env_json: str | None) -> dict[str, int]:
	if not env_json:
		return {}
	try:
		raw = json.loads(env_json)
	except Exception:
		return {}
	if not isinstance(raw, dict):
		return {}
	result: dict[str, int] = {}
	for k, v in raw.items():
		key = str(k).strip()
		if not key:
			continue
		if isinstance(v, (int, float)) and int(v) >= 1:
			result[key] = int(v)
		elif isinstance(v, str) and v.isdigit():
			val = int(v)
			if val >= 1:
				result[key] = val
	return result


def _parse_combo(text: str, default_threshold: int, default_window: int) -> tuple[list[str], int, int]:
	segments = [seg.strip() for seg in text.split(',') if seg.strip()]
	if not segments:
		return [], default_threshold, default_window

	keywords: list[str] = []
	threshold = default_threshold
	window = default_window

	for segment in segments:
		parts = [seg.strip() for seg in segment.split(':')]
		if not parts:
			continue

		kws_part = parts[0]
		if kws_part:
			keywords.extend(_parse_keywords(kws_part))

		threshold_part = parts[1] if len(parts) > 1 else ""
		window_part = parts[2] if len(parts) > 2 else ""

		# 허용: "키워드:임계치,시간" 형태
		if threshold_part and ',' in threshold_part and not window_part:
			sub_parts = [seg.strip() for seg in threshold_part.split(',') if seg.strip()]
			if sub_parts:
				threshold_part = sub_parts[0]
				if len(sub_parts) > 1:
					window_part = sub_parts[1]
		# 허용: "키워드:임계치:시간,기타" → 앞 값만 사용
		if window_part and ',' in window_part:
			window_part = window_part.split(',')[0].strip()

		if threshold_part:
			threshold = _positive_int(threshold_part, threshold)
		if window_part:
			window = _positive_int(window_part, window)

	return keywords, threshold, window


def _parse_contains_entries(text: str, default_threshold: int, default_window: int) -> list[dict[str, int | str]]:
	entries: list[dict[str, int | str]] = []
	segments = [seg.strip() for seg in text.split(',') if seg.strip()]
	if not segments:
		return entries
	for segment in segments:
		parts = [seg.strip() for seg in segment.split(':')]
		if not parts:
			continue
		keyword = parts[0]
		if not keyword:
			continue
		threshold_part = parts[1] if len(parts) > 1 else ""
		window_part = parts[2] if len(parts) > 2 else ""
		if threshold_part and ',' in threshold_part and not window_part:
			sub_parts = [seg.strip() for seg in threshold_part.split(',') if seg.strip()]
			if sub_parts:
				threshold_part = sub_parts[0]
				if len(sub_parts) > 1:
					window_part = sub_parts[1]
		if window_part and ',' in window_part:
			window_part = window_part.split(',')[0].strip()
		entry = {
			'keyword': keyword,
			'threshold': _positive_int(threshold_part, default_threshold),
			'window': _positive_int(window_part, default_window),
		}
		entries.append(entry)
	return entries


def _format_contains_entries(entries: list[dict[str, int | str]]) -> str:
	if not entries:
		return ""
	segments: list[str] = []
	for entry in entries:
		keyword = str(entry.get('keyword', '')).strip()
		if not keyword:
			continue
		threshold = _positive_int(entry.get('threshold', ''), 1)
		window = _positive_int(entry.get('window', ''), 60)
		segments.append(f"{keyword}:{threshold}:{window}")
	return ", ".join(segments)


def _parse_per_combo(text: str) -> tuple[dict[str, int], dict[str, int]]:
	per_thresholds: dict[str, int] = {}
	per_windows: dict[str, int] = {}
	for segment in text.split(','):
		segment = segment.strip()
		if not segment:
			continue
		parts = [seg.strip() for seg in segment.split(':')]
		if len(parts) < 2:
			continue
		key = parts[0]
		if not key:
			continue
		thr_part = parts[1]
		win_part = parts[2] if len(parts) > 2 else ""
		if thr_part.isdigit():
			per_thresholds[key] = max(1, int(thr_part))
		if win_part.isdigit():
			per_windows[key] = max(1, int(win_part))
	return per_thresholds, per_windows


def _format_per_combo(per_thresholds: dict[str, int], per_windows: dict[str, int]) -> str:
	keys = set(per_thresholds.keys()) | set(per_windows.keys())
	if not keys:
		return ""
	segments = []
	for key in sorted(keys):
		thr = per_thresholds.get(key, 1)
		win = per_windows.get(key, 60)
		segments.append(f"{key}:{thr}:{win}")
	return ",".join(segments)


class UIHandler(logging.Handler):
	def __init__(self, writer: TextQueueHandler, on_connected=None):
		super().__init__()
		self.writer = writer
		self.on_connected = on_connected

	def emit(self, record: logging.LogRecord) -> None:
		try:
			msg = self.format(record)
			self.writer.write(msg + "\n")
			if self.on_connected and "[SYSTEM] 연결이 완료되었습니다." in msg:
				# 실행은 Tk 스레드에서 하도록 writer의 위젯 after 사용
				try:
					self.writer.text_widget.after(0, self.on_connected)
				except Exception:
					pass
		except Exception:
			pass


class App:
	def __init__(self, root: Tk):
		load_dotenv()
		config = load_config()

		self.root = root
		self.root.title('CHZZK Chat GUI')
		self.root.minsize(780, 640)

		available_families = set(tkfont.families())
		preferred = ['Malgun Gothic', 'Segoe UI', 'Arial', 'Helvetica']
		base_family = next((fam for fam in preferred if fam in available_families), tkfont.nametofont('TkDefaultFont').actual('family'))

		self.font_regular = tkfont.Font(family=base_family, size=10)
		self.font_small = tkfont.Font(family=base_family, size=9)
		self.font_subheader = tkfont.Font(family=base_family, size=11)
		self.font_header = tkfont.Font(family=base_family, size=18, weight='bold')
		self.font_button = tkfont.Font(family=base_family, size=10, weight='bold')

		self.root.option_add('*Font', self.font_regular)

		self.bg_color = '#f8fafc'
		self.card_color = '#ffffff'
		self.accent_color = '#2563eb'
		self.danger_color = '#ef4444'
		self.text_color = '#1f2937'
		self.muted_text_color = '#64748b'

		self.root.configure(bg=self.bg_color)

		self.style = ttk.Style()
		try:
			self.style.theme_use('clam')
		except Exception:
			pass

		self.style.configure('Main.TFrame', background=self.bg_color)
		self.style.configure('Card.TFrame', background=self.card_color)
		self.style.configure('TLabel', background=self.bg_color, foreground=self.text_color, font=self.font_regular)
		self.style.configure('Header.TLabel', background=self.bg_color, foreground=self.text_color, font=self.font_header)
		self.style.configure('Subheader.TLabel', background=self.bg_color, foreground=self.muted_text_color, font=self.font_subheader)
		self.style.configure('Card.TLabelframe', background=self.card_color, foreground=self.text_color, borderwidth=1, relief='solid', padding=12)
		self.style.configure('Card.TLabelframe.Label', background=self.card_color, foreground=self.text_color, font=('Malgun Gothic', 11, 'bold'))
		self.style.configure('Card.TLabel', background=self.card_color, foreground=self.text_color, font=self.font_regular)
		self.style.configure('Hint.TLabel', background=self.card_color, foreground=self.muted_text_color, font=self.font_small)
		self.style.configure('Accent.TButton', background=self.accent_color, foreground='#ffffff', font=self.font_button, padding=8)
		self.style.map('Accent.TButton', background=[('pressed', '#1d4ed8'), ('active', '#1d4ed8'), ('disabled', '#cbd5f5')], foreground=[('disabled', '#ffffff')])
		self.style.configure('Danger.TButton', background=self.danger_color, foreground='#ffffff', font=self.font_button, padding=8)
		self.style.map('Danger.TButton', background=[('pressed', '#b91c1c'), ('active', '#dc2626'), ('disabled', '#fecaca')], foreground=[('disabled', '#ffffff')])
		self.style.configure('Secondary.TButton', background='#e2e8f0', foreground=self.text_color, font=self.font_regular, padding=8)
		self.style.map('Secondary.TButton', background=[('pressed', '#cbd5f5'), ('active', '#dbeafe')])
		self.style.configure('Card.TEntry', fieldbackground='#ffffff', foreground=self.text_color, padding=6)

		self.streamer_var = StringVar(value=os.getenv('CHANNEL_ID', ''))
		cookies = load_cookies()
		self.aut_var = StringVar(value=cookies.get('NID_AUT', ''))
		self.ses_var = StringVar(value=cookies.get('NID_SES', ''))

		# 키워드, 임계치, 전역 윈도우 통합 입력
		initial_keywords = config.get('keywords') or []
		env_keywords = (os.getenv('KEYWORDS') or '').strip()
		if not initial_keywords and env_keywords:
			initial_keywords = _parse_keywords(env_keywords)
		keywords_display = " ".join(initial_keywords)

		config_threshold = config.get('global_threshold') or 1
		env_threshold = (os.getenv('KEYWORD_THRESHOLD') or '').strip()
		if env_threshold.isdigit() and int(env_threshold) >= 1:
			config_threshold = int(env_threshold)
		self.default_threshold = config_threshold
		threshold_display = str(config_threshold)

		config_window = config.get('global_window') or 60
		env_window = (os.getenv('KEYWORD_WINDOW') or '').strip()
		if env_window.isdigit() and int(env_window) >= 1:
			config_window = int(env_window)
		self.default_window = config_window
		window_display = str(config_window)

		segments = [":".join([keywords_display, threshold_display, window_display])]
		combo_value = ",".join(segments)
		self.keyword_combo_var = StringVar(value=combo_value)

		contains_entries = config.get('contains_keywords') or []
		contains_display = _format_contains_entries(contains_entries)
		self.contains_var = StringVar(value=contains_display)

		container = ttk.Frame(self.root, padding=(20, 20, 20, 16), style='Main.TFrame')
		container.pack(fill=BOTH, expand=True)

		header_frame = ttk.Frame(container, style='Main.TFrame')
		header_frame.pack(fill=X)
		ttk.Label(header_frame, text='CHZZK Chat Monitor', style='Header.TLabel').pack(anchor='w')
		ttk.Label(header_frame, text='MAID BY 백일몽(DAYDREAM)', style='Subheader.TLabel').pack(anchor='w', pady=(0, 16))

		ttk.Separator(container, orient='horizontal').pack(fill=X, pady=(0, 16))

		channel_frame = ttk.LabelFrame(container, text='채널 & 인증 정보', style='Card.TLabelframe')
		channel_frame.pack(fill=X)
		channel_inner = ttk.Frame(channel_frame, style='Card.TFrame')
		channel_inner.pack(fill=X)
		channel_inner.columnconfigure(1, weight=1)

		ttk.Label(channel_inner, text='채널 ID', style='Card.TLabel').grid(row=0, column=0, sticky='w', padx=(0, 12), pady=(0, 8))
		self.streamer_entry = ttk.Entry(channel_inner, textvariable=self.streamer_var, style='Card.TEntry')
		self.streamer_entry.grid(row=0, column=1, sticky='ew', pady=(0, 8))

		ttk.Label(channel_inner, text='NID_AUT', style='Card.TLabel').grid(row=1, column=0, sticky='w', padx=(0, 12), pady=(0, 8))
		self.aut_entry = ttk.Entry(channel_inner, textvariable=self.aut_var, show='*', style='Card.TEntry')
		self.aut_entry.grid(row=1, column=1, sticky='ew', pady=(0, 8))

		ttk.Label(channel_inner, text='NID_SES', style='Card.TLabel').grid(row=2, column=0, sticky='w', padx=(0, 12))
		self.ses_entry = ttk.Entry(channel_inner, textvariable=self.ses_var, show='*', style='Card.TEntry')
		self.ses_entry.grid(row=2, column=1, sticky='ew')

		keyword_frame = ttk.LabelFrame(container, text='키워드 설정', style='Card.TLabelframe')
		keyword_frame.pack(fill=X, pady=(16, 0))
		keyword_inner = ttk.Frame(keyword_frame, style='Card.TFrame')
		keyword_inner.pack(fill=X)
		keyword_inner.columnconfigure(1, weight=1)

		ttk.Label(keyword_inner, text='키워드 / 임계치 / 시간(초)', style='Card.TLabel').grid(row=0, column=0, sticky='w', padx=(0, 12), pady=(0, 6))
		self.keyword_combo_entry = ttk.Entry(keyword_inner, textvariable=self.keyword_combo_var, style='Card.TEntry')
		self.keyword_combo_entry.grid(row=0, column=1, sticky='ew', pady=(0, 6))
		ttk.Label(keyword_inner, text='예: 승리:3:60, 역전:2:120', style='Hint.TLabel').grid(row=1, column=1, sticky='w', pady=(0, 10))

		ttk.Label(keyword_inner, text='포함 키워드', style='Card.TLabel').grid(row=2, column=0, sticky='w', padx=(0, 12), pady=(0, 6))
		self.contains_entry = ttk.Entry(keyword_inner, textvariable=self.contains_var, style='Card.TEntry')
		self.contains_entry.grid(row=2, column=1, sticky='ew', pady=(0, 6))
		ttk.Label(keyword_inner, text='예: 치킨:2:120, 피자:1:180', style='Hint.TLabel').grid(row=3, column=1, sticky='w')

		control_frame = ttk.Frame(container, style='Main.TFrame')
		control_frame.pack(fill=X, pady=(20, 12))
		self.start_button = ttk.Button(control_frame, text='시작', style='Accent.TButton', command=self.start)
		self.start_button.pack(side=LEFT, padx=(0, 12))
		self.stop_button = ttk.Button(control_frame, text='중지', style='Danger.TButton', command=self.stop)
		self.stop_button.pack(side=LEFT, padx=(0, 12))
		self.stop_button.config(state='disabled')
		self.manual_button = ttk.Button(control_frame, text='설명서', style='Secondary.TButton', command=self.show_manual)
		self.manual_button.pack(side=LEFT)

		log_frame = ttk.LabelFrame(container, text='실시간 로그', style='Card.TLabelframe')
		log_frame.pack(fill=BOTH, expand=True)
		log_inner = ttk.Frame(log_frame, style='Card.TFrame')
		log_inner.pack(fill=BOTH, expand=True)
		log_inner.columnconfigure(0, weight=1)
		log_inner.rowconfigure(0, weight=1)

		self.text = Text(log_inner, wrap='word', height=18, font=self.font_regular)
		self.text.grid(row=0, column=0, sticky='nsew')
		self.text.configure(
			bg='#eef2ff',
			fg=self.text_color,
			relief='flat',
			padx=12,
			pady=12,
			insertbackground=self.accent_color,
		)
		sb = ttk.Scrollbar(log_inner, orient='vertical', command=self.text.yview)
		sb.grid(row=0, column=1, sticky='ns')
		self.text.configure(yscrollcommand=sb.set)

		self.handler = TextQueueHandler(self.text)
		self.chat_thread = None
		self.chat_obj: ChzzkChat | None = None

		self._orig_close = self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
		self.waiting_connect = False
		self.running = False
		self._manual_window: Toplevel | None = None

	def _block_close(self):
		messagebox.showinfo('알림', '창을 닫으려면 먼저 종료 버튼을 눌러주세요.')

	def on_connected(self):
		self.waiting_connect = False
		self.running = True
		# 연결 완료 시 중지 버튼 활성화 (창 닫기 제한은 종료 눌러 해제)
		self.stop_button.config(state='normal')

	def start(self):
		streamer_id = self.streamer_var.get().strip()
		aut = self.aut_var.get().strip()
		ses = self.ses_var.get().strip()
		if not streamer_id or not aut or not ses:
			self.handler.write('[ERROR] 채널 ID, NID_AUT, NID_SES를 모두 입력하세요.\n')
			return

		# persist settings
		save_cookies({"NID_AUT": aut, "NID_SES": ses})
		combo_keywords, combo_threshold, combo_window = _parse_combo(
			self.keyword_combo_var.get(),
			self.default_threshold,
			self.default_window,
		)
		contains_entries = _parse_contains_entries(self.contains_var.get(), combo_threshold, combo_window)
		config = {
			'keywords': combo_keywords,
			'global_threshold': combo_threshold,
			'per_keyword_thresholds': {},
			'global_window': combo_window,
			'per_keyword_windows': {},
			'contains_keywords': contains_entries,
		}
		save_config(config)
		self.default_threshold = combo_threshold
		self.default_window = combo_window

		logger = get_logger()
		# detach existing handlers and attach UI handler
		for h in list(logger.handlers):
			logger.removeHandler(h)
		ui_handler = UIHandler(self.handler, on_connected=self.on_connected)
		ui_handler.setFormatter(logging.Formatter('%(message)s'))
		logger.addHandler(ui_handler)
		logger.setLevel(logging.INFO)

		def run_backend():
			# forge cookies.json expected by backend
			Path('cookies.json').write_text(json.dumps({"NID_AUT": aut, "NID_SES": ses}), encoding='utf-8')
			try:
				self.chat_obj = ChzzkChat(streamer_id, {"NID_AUT": aut, "NID_SES": ses}, logger)
				self.chat_obj.run()
			except Exception as e:
				self.handler.write(f'[ERROR] {e}\n')
				try:
					self.root.after(0, self._auto_stop_after_error)
				except Exception:
					pass

		self.chat_thread = threading.Thread(target=run_backend, daemon=True)
		self.chat_thread.start()
		self.waiting_connect = True
		self.start_button.config(state='disabled')
		self.stop_button.config(state='disabled')
		self.keyword_combo_entry.config(state='disabled')
		self.contains_entry.config(state='disabled')
		# 창 닫기 잠금 (연결 중/실행 중 모두)
		self.root.protocol("WM_DELETE_WINDOW", self._block_close)
		self.handler.write('[SYSTEM] 시작했습니다.\n')

	def stop(self):
		try:
			if self.chat_obj:
				self.chat_obj.stop()
		except Exception:
			pass
		self.running = False
		self.start_button.config(state='normal')
		self.stop_button.config(state='disabled')
		self.keyword_combo_entry.config(state='normal')
		self.contains_entry.config(state='normal')
		# 창 닫기 복원 (종료 눌렀을 때만 허용)
		self.root.protocol("WM_DELETE_WINDOW", self._orig_close)
		self.handler.write('[SYSTEM] 중지했습니다.\n')

	def show_manual(self):
		if self._manual_window and self._manual_window.winfo_exists():
			try:
				self._manual_window.lift()
				self._manual_window.focus_force()
			except Exception:
				pass
			return

		self._manual_window = Toplevel(self.root)
		self._manual_window.title('사용 설명서')
		self._manual_window.geometry('580x540')
		self._manual_window.resizable(False, False)

		self._manual_window.configure(bg=self.bg_color)

		frame = Frame(self._manual_window, bg=self.card_color)
		frame.pack(fill=BOTH, expand=True, padx=10, pady=10)

		scrollbar = ttk.Scrollbar(frame, orient='vertical')
		scrollbar.pack(side='right', fill=Y)

		text_widget = Text(
			frame,
			wrap='word',
			yscrollcommand=scrollbar.set,
			bg=self.card_color,
			fg=self.text_color,
			relief='flat',
			padx=12,
			pady=12,
			font=self.font_regular,
			insertbackground=self.accent_color,
		)
		scrollbar.config(command=text_widget.yview)
		text_widget.pack(fill=BOTH, expand=True)

		manual_text = (
			"치지직 채팅 크롤러 GUI 사용 설명서\n"
			"\n"
			"1. 기본 준비\n"
			"   - 브라우저에서 치지직에 로그인한 뒤 개발자 도구(F12) → Application/Storage → Cookies에서\n"
			"     `NID_AUT`, `NID_SES` 값을 확인하여 입력합니다.\n"
			"   - 채널 ID는 방송 주소 `https://chzzk.naver.com/live/<채널ID>`의 마지막 부분입니다.\n"
			"\n"
			"2. 키워드 설정\n"
			"   - 형식: `키워드:임계치:시간(초)` (예: `승리:3:60`)\n"
			"   - 여러 항목은 콤마로 구분하며, 첫 번째 칸에는 공백으로 여러 키워드를 함께 입력할 수 있습니다.\n"
			"     예) `승리 역전:3:60, 펜타킬:2:120`\n"
			"\n"
			"3. 포함 키워드\n"
			"   - 단어 경계를 무시하고 포함 여부만 검사합니다. 형식은 위와 동일합니다.\n"
			"   - 예) `치킨:2:120, 피자:1:180`\n"
			"\n"
			"4. 실행과 로그\n"
			"   - `시작` 버튼으로 채팅 수집을 시작하고, 연결되면 `중지` 버튼이 활성화됩니다.\n"
			"   - 키워드 임계치를 충족하면 `keyword_times.log`에 다음 형식으로 기록됩니다.\n"
			"       `발생시각\\t채널명\\t키워드\\t닉네임\\t방송진행시간\\t채팅메시지`\n"
			"   - 방송 진행 시간은 `broadcast_logger`가 가져온 방송 시작 시각과 비교하여 계산됩니다.\n"
			"     시작 시각을 가져오지 못하면 로그에 경고가 출력되고 진행 시간은 `-`로 표기됩니다.\n"
			"\n"
			"5. 방송 재시작 대응\n"
			"   - 방송이 끊겼다가 다시 시작하면 자동으로 새 시작 시각을 불러옵니다.\n"
			"\n"
			"6. 주의 사항\n"
			"   - 방송이 오프라인일 경우 채팅 연결 및 시작 시각 조회가 실패할 수 있습니다.\n"
			"   - 로그 파일은 이어쓰기 모드로 열리므로, 새 로그가 필요하면 파일을 백업하거나 삭제하세요.\n"
			"   - 실행 중에는 창을 강제 종료하지 말고 반드시 `중지` 버튼을 이용해 종료하세요.\n"
			"\n"
			"7. 문제 해결\n"
			"   - 쿠키가 만료되거나 잘못되면 API 접근이 거부될 수 있으니 최신 값으로 갱신하세요.\n"
			"   - 기타 오류는 로그 메시지를 확인하거나 README를 참고하세요.\n"
		)

		text_widget.insert(END, manual_text)
		text_widget.config(state='disabled')

		def on_close():
			try:
				if self._manual_window:
					self._manual_window.destroy()
			finally:
				self._manual_window = None

		self._manual_window.protocol("WM_DELETE_WINDOW", on_close)


	def _auto_stop_after_error(self):
		try:
			if self.stop_button['state'] == 'disabled':
				self.stop_button.config(state='normal')
			self.stop_button.invoke()
		except Exception:
			try:
				self.stop()
			except Exception:
				pass


def main():
	root = Tk()
	App(root)
	root.mainloop()


if __name__ == '__main__':
	main()
