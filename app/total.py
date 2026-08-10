# -*- coding: utf-8 -*-
"""
성형외과 상담 통합 대시보드 (셀레니움 수집 + 구글시트 DB + Tkinter GUI · 단일 파일)
================================================================================
바비톡 · 강남언니 · 네이버지도 · 여신티켓 · 온라인상담 · 온라인예약 · 카카오톡
→ 셀레니움이 채널을 순회하며 새 상담을 수집 → 저장(구글시트 또는 로컬)
→ '오늘 날짜 중 아직 확인 안 한' 상담을 한 화면에 모아 보여주는 토탈 대시보드.

실행 (추가 설치 불필요):
    python total.py

동작 모드:
    DEMO_MODE = True   → 셀레니움 없이 가짜 유입 생성(테스트용)
    DEMO_MODE = False  → 각 채널을 셀레니움으로 실제 수집(_scrape 구현 필요)

저장 백엔드:
    SHEET_URL 비어있음 → 로컬 SQLite (data/consultations.db)
    SHEET_URL 입력됨   → 구글시트 (service_account.json 으로 연결)
                         ※ 시트를 service account 이메일에 '편집자'로 공유해야 함

구조:
    [설정] · [모델] · [셀레니움] · [채널×7] · [데모] · [저장소] · [수집] · [화면]

새 채널 추가:  '채널' 영역에 클래스 하나(@register) 추가 + ENABLED 한 줄.
실제 연동:     각 채널의 LOGIN_URL/LIST_URL/_scrape() 채우기 → DEMO_MODE=False.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Dict, List, Optional, Type

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent   # exe 옆 (DB·크롬프로필 등 쓰기용)
    BUNDLE_DIR = Path(sys._MEIPASS)                    # exe 안에 넣은 읽기전용 파일
else:
    BASE_DIR = BUNDLE_DIR = Path(__file__).resolve().parent

# ══════════════════════════════════════════════════════════════
# [설정]  운영 중 자주 바꾸는 값
# ══════════════════════════════════════════════════════════════
DEMO_MODE = False                      # True=가짜유입 / False=셀레니움 실제수집


# ── 비밀값 로딩 (secrets.json) ─────────────────────────────────
#   이 코드는 깃허브 공개 저장소로 자동 업데이트되므로 계정·비밀번호·토큰을
#   코드 안에 두지 않는다. 값은 secrets.json 에서만 읽는다.
#   찾는 순서: ① exe(또는 total.py) 옆  ② exe 안에 넣어둔 것
#   → 비밀번호가 바뀌면 exe 옆 secrets.json 만 고치면 되고 재빌드가 필요 없다.
def _load_secrets() -> dict:
    for p in (BASE_DIR / "secrets.json", BUNDLE_DIR / "secrets.json"):
        try:
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[설정] {p} 읽기 실패: {e}")
    print("[설정] secrets.json 을 찾지 못했습니다 — 로그인·알림이 동작하지 않습니다.")
    return {}


SECRETS: Dict = _load_secrets()


def app_version() -> str:
    """실행 중인 코드의 버전.

    찾는 순서:
      ① app/version.json   — exe(런처)가 깃허브에서 받아둔 코드      → "1.0.1"
      ② version.json       — app/ 안에서 바로 실행된 경우
      ③ repo/version.json  — 개발 폴더에서 소스를 직접 실행한 경우   → "1.0.1(개발)"
    아무것도 없으면 "개발".
    """
    def read(p: Path) -> str:
        try:
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8")).get("version", "")
        except Exception:
            pass
        return ""

    for p in (BASE_DIR / "app" / "version.json", BASE_DIR / "version.json"):
        v = read(p)
        if v:
            return v
    # 개발 PC 에서 total.py 를 직접 돌릴 때: 마지막으로 배포한 버전을 보여준다
    v = read(BASE_DIR / "repo" / "version.json")
    return f"{v}(개발)" if v else "개발"


def account(key: str) -> tuple:
    """secrets.json 의 accounts[key] → (아이디, 비밀번호). 없으면 빈 문자열."""
    a = SECRETS.get("accounts", {}).get(key) or {}
    return a.get("id", ""), a.get("pw", "")


# 구글시트 (비워두면 로컬 SQLite 사용). 예: https://docs.google.com/spreadsheets/d/XXXX/edit
SHEET_URL = ""
SHEET_TAB = "상담목록"
# service_account.json: exe 옆에 있으면 그걸 먼저 쓴다(키 교체 시 재빌드 불필요)
SERVICE_ACCOUNT_FILE = (BASE_DIR / "service_account.json"
                        if (BASE_DIR / "service_account.json").exists()
                        else BUNDLE_DIR / "service_account.json")

# 채널별 대시보드용 구글시트 (탭 이름 = 채널별 SHEET_TAB)
GSHEET_URL = SECRETS.get("gsheet_url", "")

# 웹 대시보드(Apps Script 웹앱) — GUI '웹 대시보드' 버튼이 브라우저로 연다
WEBAPP_URL = SECRETS.get("webapp_url", "")

DB_PATH = BASE_DIR / "data" / "consultations.db"           # 로컬 백엔드용
CHROME_PROFILE_DIR = BASE_DIR / "chrome_profiles"          # 채널별 로그인 유지
HEADLESS = True                        # 수집 시 브라우저 숨김(로그인 때는 자동으로 보임)

COLLECT_INTERVAL_SEC = 180             # 수집기 순회 주기(초) · run_dashboard.py 와 별개
GUI_REFRESH_SEC = 30                    # GUI가 시트를 다시 읽어 화면 갱신하는 주기(초)

# ── 텔레그램 알림 (새 상담 / 수집 실패) ──────────────────────────
#   @BotFather 로 봇 생성 → 토큰, 봇과 대화 후 @userinfobot 으로 Chat ID 확인.
#   여러 명이 받으려면 그룹에 봇 초대 후 그룹 chat_id 사용.
#   토큰이 비어있으면 알림 기능 전체가 조용히 꺼진다.
TELEGRAM_TOKEN = SECRETS.get("telegram_token", "")
TELEGRAM_CHAT_ID = SECRETS.get("telegram_chat_id", "")   # 그룹 'CS알림'
TELEGRAM_FAIL_RENOTIFY_MIN = 60        # 같은 채널 수집실패 재알림 최소 간격(분)

FONT = ("맑은 고딕", 10)
FONT_BOLD = ("맑은 고딕", 10, "bold")
FONT_TITLE = ("맑은 고딕", 16, "bold")

ENABLED: Dict[str, bool] = {
    "babitalk": True,
    "gangnamunni": True,
    "naver_map": True,
    "yeosin_ticket": True,
    "online_consult": True,
    "online_booking": True,
    "kakaotalk": True,
}


# ══════════════════════════════════════════════════════════════
# [모델]  공통 상담 데이터 (채널마다 형식이 달라도 이걸로 통일)
# ══════════════════════════════════════════════════════════════
@dataclass
class Consultation:
    id: str                       # 전역 고유 ID  (예: "babitalk:12345")
    channel_key: str
    external_id: str
    customer_name: str
    contact: str = ""
    treatment: str = ""
    message: str = ""
    received_at: datetime = field(default_factory=datetime.now)
    status: str = "신규"
    confirmed: bool = False
    raw: dict = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════
# [셀레니움]  채널별 프로필을 유지하는 드라이버
# ══════════════════════════════════════════════════════════════
# 상주 브라우저 원격 디버깅 포트: 다른 Chrome(다른 계정)과 안 겹치게 '전용 비표준 포트' 사용.
# 흔한 9222 를 쓰면 CS PC의 개인 Chrome(다른 네이버 계정)에 잘못 붙을 수 있어 피함.
CHROME_DEBUG_PORT = 9764
COLLECTOR_LOCK_PORT = 9765      # 수집기 단일 실행 보장용(실제 통신은 안 함)


def acquire_collector_lock():
    """수집기 중복 실행 차단.
    성공하면 점유 소켓을 반환(프로세스가 살아있는 동안 들고 있어야 함),
    이미 다른 수집기가 돌고 있으면 None.
    ※ 락파일과 달리 강제 종료돼도 OS 가 포트를 회수하므로 찌꺼기가 남지 않는다."""
    s = socket.socket()
    try:                        # 윈도우: 남이 같은 포트를 가로채지 못하게
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    except (AttributeError, OSError):
        pass
    try:
        s.bind(("127.0.0.1", COLLECTOR_LOCK_PORT))
        s.listen(1)
        return s
    except OSError:             # 이미 점유됨 = 다른 수집기 실행 중
        s.close()
        return None


def collector_lock_held() -> bool:
    """다른 수집기가 이미 실행 중인지(UI 사전 확인용)."""
    s = acquire_collector_lock()
    if s is None:
        return True
    s.close()
    return False


# ── 잠금을 쥔 프로세스 찾기 ────────────────────────────────────
# '이미 실행 중'만 알려주면 작업 관리자에서 뭘 찾아야 할지 알 수 없다.
# 윈도우 기본 명령(netstat·tasklist·taskkill)만 써서 추가 의존성 없이
# '누가' 잡고 있는지 이름·PID 로 짚어주고, 원하면 그 자리에서 종료한다.
def _run_hidden(cmd: list, timeout: int = 10) -> str:
    """콘솔창 안 띄우고 외부 명령 실행 → stdout. 실패하면 빈 문자열."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           errors="replace", timeout=timeout,
                           creationflags=0x08000000)     # CREATE_NO_WINDOW
        return r.stdout or ""
    except Exception:
        return ""


def port_holder_pid(port: int) -> Optional[int]:
    """해당 TCP 포트를 듣고 있는 프로세스의 PID. 못 찾으면 None.
    netstat 한 줄 예시(상태 표기는 한글 윈도우에서도 영문):
        TCP    127.0.0.1:9765     0.0.0.0:0     LISTENING     12784
    """
    listen = other = None
    for line in _run_hidden(["netstat", "-ano", "-p", "TCP"]).splitlines():
        parts = line.split()
        # parts[1]=로컬주소 로만 비교한다(상대주소가 같은 포트인 연결은 무관)
        if len(parts) < 5 or not parts[1].endswith(f":{port}"):
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if parts[-2].upper() == "LISTENING":
            return pid
        other = other or pid            # 상태 표기가 다른 환경 대비 폴백
    return listen or other


def process_name(pid: int) -> str:
    """PID → 실행 파일 이름('CSdashboard.exe'). 못 찾으면 빈 문자열."""
    out = _run_hidden(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"])
    m = re.match(r'"([^"]+)"', out.strip())
    return m.group(1) if m else ""


def collector_lock_holder() -> Optional[tuple]:
    """수집기 잠금 포트를 쥔 프로세스 (PID, 이름). 못 찾으면 None."""
    pid = port_holder_pid(COLLECTOR_LOCK_PORT)
    if pid is None:
        return None
    return pid, (process_name(pid) or "이름 확인 불가")


def kill_pid(pid: int) -> bool:
    """PID 강제 종료 후 잠금이 실제로 풀렸는지까지 확인.
    ※ /T(자식 트리)는 쓰지 않는다 — 수집기가 띄운 '로그인된 상주 크롬'까지
      같이 죽어서 다음 실행 때 전 채널 재로그인(캡챠 위험)이 걸린다."""
    _run_hidden(["taskkill", "/PID", str(pid), "/F"], timeout=15)
    for _ in range(20):                 # 포트가 OS 로 회수될 때까지 최대 5초
        if not collector_lock_held():
            return True
        time.sleep(0.25)
    return not collector_lock_held()


def app_exe_name() -> str:
    """지금 실행 중인 파일 이름(exe 면 'CSdashboard.exe', 소스 실행이면 'python.exe').
    안내 문구에 옛 exe 이름을 박아두면 작업 관리자에서 못 찾는다."""
    try:
        return Path(sys.executable).name
    except Exception:
        return "CSdashboard.exe"


def _build_chrome(opts):
    """크롬 드라이버 인스턴스 생성.

    드라이버 확보 순서:
      1) Selenium Manager(셀레니움 4.6+ 내장) — service 없이 Chrome() 호출하면
         설치된 크롬 버전에 맞는 드라이버를 ~/.cache/selenium 에 '버전별'로 캐시.
         이미 있으면 재다운로드/덮어쓰기를 안 하므로 '실행 중 exe 덮어쓰기 →
         WinError 5(액세스 거부)' 문제가 원천적으로 안 생긴다.
      2) 실패 시 webdriver_manager 폴백. 폴백 중 PermissionError(캐시 파일 잠김)면
         잠긴 .wdm 캐시를 지우고 1회 재시도한다.

    ⚠️ unhandledPromptBehavior='ignore' 를 반드시 준다(실측으로 확인한 문제).
       기본값은 'dismiss and notify' — alert 이 떠 있는 동안 들어온 첫 명령을
       크롬드라이버가 '알림창을 취소로 닫고' 예외로 되돌린다. 그래서
         · 우리가 '확인'을 누른 게 아니라 브라우저가 멋대로 '취소'를 누른 셈이 되고
         · 그 알림창 문구도 사라져(_pop_alert 로 확인해도 흔적이 없다)
           '로그인 폼을 찾지 못했습니다' 같은 엉뚱한 사유만 남는다.
       'ignore' 면 알림창이 그대로 살아있어 우리가 문구를 읽고 '확인'을 누른다.
    """
    from selenium import webdriver

    opts.set_capability("unhandledPromptBehavior", "ignore")

    try:                                   # 1) Selenium Manager (권장)
        return webdriver.Chrome(options=opts)
    except Exception as e:
        print(f"[driver] Selenium Manager 실패({type(e).__name__}) → webdriver_manager 폴백")

    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    try:                                   # 2) webdriver_manager
        return webdriver.Chrome(
            service=Service(ChromeDriverManager().install()), options=opts)
    except PermissionError:
        # 잠긴/손상된 .wdm 캐시 제거 후 1회 재시도(재다운로드).
        wdm = Path.home() / ".wdm"
        print(f"[driver] 캐시 잠김 → {wdm} 삭제 후 재시도")
        shutil.rmtree(wdm, ignore_errors=True)
        return webdriver.Chrome(
            service=Service(ChromeDriverManager().install()), options=opts)


def make_driver(channel_key: str, headless: bool = True,
                debugger_address: Optional[str] = None):
    """크롬 드라이버 생성. debugger_address 가 있으면 '이미 떠 있는 브라우저'에 연결."""
    from selenium import webdriver

    opts = webdriver.ChromeOptions()
    if debugger_address:            # 상주 브라우저에 연결(새 창 안 띄움)
        opts.add_experimental_option("debuggerAddress", debugger_address)
        return _build_chrome(opts)

    profile = CHROME_PROFILE_DIR / channel_key
    profile.mkdir(parents=True, exist_ok=True)
    opts.add_argument(f"--user-data-dir={profile}")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    if headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1920,1080")
    else:
        opts.add_argument("--start-maximized")
    return _build_chrome(opts)


def _port_open(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _chrome_exe() -> Optional[str]:
    cands = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]
    local = os.environ.get("LOCALAPPDATA")      # 사용자 단위 설치(다른 PC에서 흔함)
    if local:
        cands.append(str(Path(local) / r"Google\Chrome\Application\chrome.exe"))
    for p in cands:
        if Path(p).exists():
            return p
    return shutil.which("chrome") or shutil.which("chrome.exe")


def ensure_persistent_chrome(port: int = CHROME_DEBUG_PORT,
                             headless: bool = False) -> bool:
    """
    원격 디버깅 크롬이 안 떠 있으면 main 프로필로 '한 번' 띄운다(detached).
    이미 떠 있으면 그대로 재사용. 성공 시 True.
    → 이후 모든 실행(run_dashboard once/연속)이 이 브라우저에 연결됨.
    """
    if _port_open("127.0.0.1", port):
        return True
    exe = _chrome_exe()
    if not exe:
        print("[hub] Chrome 실행파일을 못 찾음 → 일반 방식으로 실행")
        return False
    profile = CHROME_PROFILE_DIR / "main"
    profile.mkdir(parents=True, exist_ok=True)
    # ※ --disable-blink-features=AutomationControlled 는 상단 '지원되지 않는 플래그'
    #   경고바를 띄우므로 넣지 않음. navigator.webdriver 숨김은 start()의 CDP로 처리.
    args = [exe, f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-session-crashed-bubble", "--hide-crash-restore-bubble"]
    if headless:
        args += ["--headless=new", "--window-size=1920,1080"]
    else:
        args.append("--start-maximized")
    # DETACHED_PROCESS(0x8): 파이썬이 꺼져도 브라우저는 살아있게
    subprocess.Popen(args, creationflags=0x00000008,
                     close_fds=True)
    for _ in range(40):             # 포트 열릴 때까지 대기(최대 ~20초)
        if _port_open("127.0.0.1", port):
            print(f"[hub] 상주 브라우저 최초 실행(포트 {port})")
            return True
        time.sleep(0.5)
    print("[hub] 상주 브라우저 기동 대기 시간초과 → 일반 방식으로 실행")
    return False


def _cell_lines(el) -> List[str]:
    """셀 텍스트를 공백 제거된 줄 리스트로."""
    return [ln.strip() for ln in el.text.split("\n") if ln.strip()]


def _to_sheet_date(s: str):
    """다양한 날짜 포맷 → 시트가 날짜값으로 인식할 문자열.
    - 날짜+시각: 'YYYY-MM-DD HH:MM' 로 통일
    - ISO8601(+타임존): '2026-05-28T19:23:53+09:00' 도 처리
    - 날짜만: '2026-06-20' 은 그대로(날짜값 인식)
    못 맞추면 원문 유지."""
    s = (s or "").strip()
    for fmt in ("%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            pass
    try:                                    # ISO8601(+09:00 등)
        return datetime.fromisoformat(s).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        pass
    m = re.match(                           # 한글 오전/오후: '2026. 7. 13 오후 6:38:17'
        r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\s*(오전|오후)\s*(\d{1,2}):(\d{2})", s)
    if m:
        y, mo, d, ap, h, mi = (int(m.group(1)), int(m.group(2)), int(m.group(3)),
                               m.group(4), int(m.group(5)), int(m.group(6)))
        if ap == "오후" and h != 12:
            h += 12
        elif ap == "오전" and h == 12:
            h = 0
        try:
            return datetime(y, mo, d, h, mi).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    try:                                    # 날짜만
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return s


# ══════════════════════════════════════════════════════════════
# [브라우저 허브]  단일 브라우저 + 채널별 탭 상주 (세션 유지)
# ══════════════════════════════════════════════════════════════
class BrowserHub:
    """
    브라우저 1개를 계속 띄워두고 채널마다 탭 1개를 상주시킨다.
    수집 때마다 해당 탭을 완전 재로드(get) → 로그인 튕기면 재로그인 → 스크랩.

    안전장치:
      1) 프로필 1개(main) 공유 — 도메인이 달라 쿠키 안 섞임
      2) self.lock 으로 드라이버 접근 직렬화(브라우저 1개)
      3) 탭이 닫혀도(NoSuchWindowException) 자동 재생성
      4) set_page_load_timeout 으로 한 탭 hang 방지
      5) maybe_recycle 로 장시간 구동 시 브라우저 재시작(메모리 정리)
      6) collect 실패는 예외로 올려 호출부가 시트를 건드리지 않게 함
      7) 로그인 실패(캡챠 등) 시 그 채널을 LOGIN_BACKOFF_SEC 동안 '재로그인 시도'만 건너뜀
         → 반복 자동 로그인(계정 플래그 위험) 방지. 단, 매 사이클 로그인 상태는 확인해
           그 사이 수동 로그인이 됐으면 백오프를 즉시 풀고 수집(화면 바로 반영)한다.
      8) persistent=True(기본): 원격 디버깅 브라우저를 '최초 1회'만 띄우고
         이후 실행은 그 브라우저에 연결(재사용). 앱을 껐다 켜도 로그인 유지.
    """
    PAGE_LOAD_TIMEOUT = 45
    RECYCLE_AFTER_SEC = 6 * 3600
    LOGIN_BACKOFF_SEC = 10 * 60         # 로그인 실패 후 재시도 안 하는 시간(10분)
                                        # ※ 채널이 같은 이름의 값을 들고 있으면 그쪽 우선
                                        #   (바비톡처럼 '몇 분 뒤 재시도'가 정답인 사이트)
    # 한 사이클 안에서 '새로고침 → 로그인 → 정말 됐는지 확인' 을 몇 번까지 돌지.
    # 2 = 로그인해서 목록을 열었는데 여전히 미로그인이면 한 번 더 해본다.
    # ※ '제출했다가 거부당한' 실패는 이 횟수와 무관하게 즉시 중단한다(계정 잠김 방지).
    LOGIN_TRY_PER_CYCLE = 2

    def _backoff_sec(self, ch: "BaseChannel") -> float:
        """이 채널의 로그인 실패 대기시간. 채널이 정해두지 않았으면 공통값(10분).

        단, '로그인 버튼을 누르지도 못한' 실패(폼이 아직 안 그려짐, 화면 못 읽음 등)는
        사이트에 실패 기록이 남지 않는다 → 계정 잠김과 무관하므로 길게 쉴 이유가 없다.
        이때는 짧게(기본 30초) 쉬고 바로 다시 시도한다.
        (실측: 폼 못 찾음 하나로 5분 30초씩 세 번을 쉬어 복구에 18분이 걸렸다.)"""
        long_sec = float(getattr(ch, "LOGIN_BACKOFF_SEC", 0) or self.LOGIN_BACKOFF_SEC)
        if getattr(ch, "login_attempted", True):
            return long_sec
        short = float(getattr(ch, "LOGIN_BACKOFF_SHORT_SEC", 0) or 30)
        return min(short, long_sec)

    def __init__(self, headless: bool = False, persistent: bool = True):
        self.headless = headless
        self.persistent = persistent    # True=상주 브라우저에 연결(최초 1회만 실행)
        self.attached = False           # 상주 브라우저에 붙었는지(=quit 시 닫지 않음)
        self.driver = None
        self.tabs: Dict[str, str] = {}      # channel_key -> window handle
        self.lock = threading.Lock()
        self._started = 0.0
        self._backoff: Dict[str, float] = {}   # channel_key -> 이 시각까지 건너뜀(monotonic)

    def start(self) -> "BrowserHub":
        self.attached = False
        if self.persistent and ensure_persistent_chrome(CHROME_DEBUG_PORT, self.headless):
            self.driver = make_driver(
                "main", debugger_address=f"127.0.0.1:{CHROME_DEBUG_PORT}")
            self.attached = True
            self._prune_tabs()          # 이전 실행 잔여 탭 정리(중복 누적 방지)
        else:
            self.driver = make_driver("main", headless=self.headless)
        try:
            self.driver.set_page_load_timeout(self.PAGE_LOAD_TIMEOUT)
        except Exception:
            pass
        # 봇 탐지 완화(네이버 등): navigator.webdriver 숨김
        try:
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator,'webdriver',"
                           "{get:()=>undefined})"})
        except Exception:
            pass
        self.tabs.clear()
        self._started = time.monotonic()
        return self

    def _prune_tabs(self) -> None:
        """상주 브라우저의 이전 실행 잔여 탭을 정리(1개만 남김) → 탭 중복 누적 방지."""
        try:
            handles = self.driver.window_handles
            if len(handles) <= 1:
                return
            for h in handles[1:]:
                try:
                    self.driver.switch_to.window(h)
                    self.driver.close()
                except Exception:
                    pass
            self.driver.switch_to.window(self.driver.window_handles[0])
        except Exception:
            pass

    def _accept_alert(self, wait: float = 0.0) -> None:
        """열린 JS alert(로그인 후 이용/세션 만료 등)을 닫음.
        wait>0 이면 그 시간만큼 '늦게 뜨는 alert'도 폴링해서 닫는다.
        (닫지 않으면 이후 모든 명령이 차단돼 다음 채널까지 실패함)"""
        from selenium.common.exceptions import NoAlertPresentException
        end = time.monotonic() + wait
        while True:
            try:
                self.driver.switch_to.alert.accept()   # 있으면 닫고 계속(연속 alert 대비)
                time.sleep(0.2)
            except NoAlertPresentException:
                if time.monotonic() >= end:
                    return
                time.sleep(0.2)
            except Exception:
                return

    def _sweep_alerts(self) -> None:
        """모든 탭을 돌며 열린 alert 을 '확인'으로 닫고 원래 탭으로 돌아온다.
        드라이버가 unhandledPromptBehavior='ignore' 라 알림창은 우리가 닫아야만
        사라진다 → 다른 탭에 알림창이 남아 그 탭 전환/조작이 계속 막히는 것을 막는다."""
        try:
            cur, handles = self.driver.current_window_handle, self.driver.window_handles
        except Exception:
            return
        for h in handles:
            try:
                self.driver.switch_to.window(h)
                self._accept_alert()
            except Exception:
                continue
        try:
            self.driver.switch_to.window(cur)
        except Exception:
            pass

    def _retry_on_alert(self, fn):
        """alert 때문에 막힌 호출은 alert 을 닫고 한 번 더 시도한다.
        세션 만료 안내는 페이지 로드가 끝난 '뒤에' 뜨기도 해서, 아무리 잘 닫아도
        is_logged_in()/login() 도중에 새로 뜰 수 있다. 그때 채널을 통째로
        실패시키지 않고 알림만 치우고 그대로 진행한다."""
        from selenium.common.exceptions import UnexpectedAlertPresentException
        try:
            return fn()
        except UnexpectedAlertPresentException:
            self._accept_alert(1.0)
            try:
                return fn()
            except UnexpectedAlertPresentException:
                self._sweep_alerts()        # 다른 탭에 남은 알림창까지 치우고 마지막 시도
                return fn()

    def _ensure_tab(self, ch: "BaseChannel") -> None:
        """채널 탭으로 전환. 없거나 닫혔으면 빈 창 재활용 또는 새 탭 생성."""
        h = self.tabs.get(ch.key)
        if h and h in self.driver.window_handles:
            self.driver.switch_to.window(h)
            return
        used = set(self.tabs.values())
        free = [w for w in self.driver.window_handles if w not in used]
        if free:                                   # 최초 빈 창 등 재활용
            self.driver.switch_to.window(free[0])
        else:
            self.driver.switch_to.new_window("tab")
        self.tabs[ch.key] = self.driver.current_window_handle

    def _open_list(self, ch: "BaseChannel") -> None:
        """목록 페이지를 완전 재로드하고, 늦게 뜨는 알림창/팝업까지 치운다."""
        self.driver.get(ch.LIST_URL)               # 새로고침 대신 완전 재로드
        # 미로그인/세션만료 등 늦게 뜨는 alert까지 대기해서 닫음.
        # 채널이 더 긴 창을 요구하면(ALERT_SETTLE_SEC) 그만큼 기다린다.
        self._accept_alert(max(2.0, getattr(ch, "ALERT_SETTLE_SEC", 0.0)))
        ch.dismiss_popups(self.driver)

    def _login_fail(self, ch: "BaseChannel", why: str = "") -> RuntimeError:
        """로그인 실패 확정 — 백오프를 걸고 화면에 띄울 예외를 만든다."""
        wait_sec = self._backoff_sec(ch)
        self._backoff[ch.key] = time.monotonic() + wait_sec
        hint = f" | 수동 로그인: {ch.LOGIN_HELP}" if ch.LOGIN_HELP else ""
        why = why or getattr(ch, "login_error", "") or ""
        m, s = divmod(int(wait_sec), 60)
        when = (f"{m}분" + (f" {s}초" if s else "")) if m else f"{s}초"
        return RuntimeError(f"자동 로그인 실패 — {when} 뒤 다시 시도"
                            + (f" ({why})" if why else "") + hint)

    def collect(self, ch: "BaseChannel") -> list:
        """탭 재로드 + 로그인 보장 + 스크랩. 실패 시 예외를 올린다(시트 보호).

        '로그아웃 상태면 → 새로고침 → 로그인 → 정말 됐는지 다시 확인' 을
        LOGIN_TRY_PER_CYCLE 번까지 한 사이클 안에서 돈다. 로그인했다고 넘어갔다가
        정작 목록이 안 열려 엉뚱한 스크랩 오류로 끝나는 일을 막는다.

        ⚠️ 단, '아이디/비번을 실제로 제출했는데 거부당한' 실패는 그 자리에서
           다시 시도하지 않는다(사이트의 연속 실패 카운터 → 계정 잠김).
           화면이 안 그려져 제출도 못 해본 실패만 곧장 새로고침해 다시 한다.

        백오프 중이어도 페이지를 열어 로그인 상태 '확인'은 한다:
          · 그 사이 수동 로그인이 됐으면 → 백오프 즉시 해제하고 수집(화면 바로 반영)
          · 아직 미로그인이면 → 재로그인은 '시도하지 않고' 남은 백오프만큼 건너뜀
            (반복 자동 로그인으로 계정이 플래그되는 것 방지)"""
        from selenium.common.exceptions import NoSuchWindowException

        with self.lock:
            self._accept_alert()                   # 이전 채널의 잔여 alert 정리(연쇄 차단 방지)
            try:
                # 탭 전환 자체가 알림창에 막힐 수 있다 → 치우고 재시도
                self._retry_on_alert(lambda: self._ensure_tab(ch))
            except NoSuchWindowException:
                self.tabs.pop(ch.key, None)
                self._ensure_tab(ch)

            self._open_list(ch)
            tries = 0                              # 실제로 login() 을 부른 횟수
            while not self._retry_on_alert(lambda: ch.is_logged_in(self.driver)):
                # 백오프 중이면 재로그인은 시도하지 않고 남은 시간만큼 건너뜀
                # (수동 로그인은 위 is_logged_in 통과로 이미 걸러졌으니 여기 안 옴)
                until = self._backoff.get(ch.key, 0.0)
                if until and time.monotonic() < until:
                    left = int(until - time.monotonic()) + 1
                    when = (f"약 {left // 60 + 1}분" if left >= 60 else f"{left}초")
                    raise RuntimeError(f"로그인 실패 백오프 중 — {when} 후 재시도")
                if tries >= self.LOGIN_TRY_PER_CYCLE:
                    # 로그인은 됐다는데 목록이 계속 미로그인 → 다음 사이클에 다시
                    raise self._login_fail(
                        ch, "로그인 후에도 목록이 미로그인 상태로 나옵니다")

                tries += 1
                print(f"[{ch.name}] 세션 만료 → 재로그인 "
                      f"({tries}/{self.LOGIN_TRY_PER_CYCLE})")
                try:
                    ok = self._retry_on_alert(lambda: ch.login(self.driver))
                except Exception as e:
                    ok = False                     # 로그인 중 예외도 실패로 처리
                    ch.login_error = ch.login_error or classify_error(e).detail
                if ok:
                    self._open_list(ch)            # 목록을 다시 열고 위에서 재확인
                    continue
                # 제출까지 갔다가 거부당했으면(비번 오류·캡챠 등) 여기서 멈춘다.
                # 제출도 못 해본 실패(폼이 안 그려짐 등)만 새로고침하고 곧장 다시.
                if (tries >= self.LOGIN_TRY_PER_CYCLE
                        or getattr(ch, "login_attempted", True)):
                    raise self._login_fail(ch)
                print(f"[{ch.name}] 로그인 화면이 준비되지 않음 "
                      f"({ch.login_error}) — 새로고침하고 곧장 다시 시도")
                self._open_list(ch)
            self._backoff.pop(ch.key, None)        # 로그인 정상(수동 포함) → 백오프 해제
            return ch.scrape(self.driver)

    def maybe_recycle(self) -> None:
        # 상주 브라우저(attached)는 우리가 소유하지 않으니 재시작하지 않음.
        if self.attached:
            return
        if self.driver and (time.monotonic() - self._started) > self.RECYCLE_AFTER_SEC:
            print("[hub] 장시간 구동 → 브라우저 재시작(메모리 정리)")
            self.quit()
            self.start()

    def quit(self) -> None:
        # 상주 브라우저에 '연결'만 한 경우엔 브라우저를 닫지 않음(재사용 위해 유지).
        if self.attached:
            self.driver = None
            self.tabs.clear()
            return
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        self.driver = None
        self.tabs.clear()


# ══════════════════════════════════════════════════════════════
# [채널]  베이스 + 자동 등록 레지스트리
# ══════════════════════════════════════════════════════════════
_REGISTRY: Dict[str, Type["BaseChannel"]] = {}


def register(cls: Type["BaseChannel"]) -> Type["BaseChannel"]:
    if not getattr(cls, "key", ""):
        raise ValueError(f"{cls.__name__}: 'key' 가 필요합니다.")
    _REGISTRY[cls.key] = cls
    return cls


class BaseChannel(ABC):
    key: str = ""
    name: str = ""
    color: str = "#888888"
    LOGIN_URL: str = ""     # 로그인 페이지
    LIST_URL: str = ""      # 상담 목록 페이지
    LOGIN_HELP: str = ""    # 로그인 실패 시 안내 문구(수동 로그인 방법 등)
    login_error: str = ""   # 마지막 자동 로그인 실패 사유(화면·시트 '비고'에 표시)
    _lookup_note: str = ""  # 폼 조회가 막힌 이유(알림창 문구/예외명) — 사유 보고용
    # 이번 로그인에서 '제출(로그인 클릭)'까지 갔는가.
    # False = 사이트엔 아무 흔적도 안 남은 실패(폼이 아직 안 그려짐 등)
    #   → 오래 쉴 이유가 없다(짧은 백오프). 기본값은 안전하게 True(모르면 길게 쉼).
    login_attempted: bool = True

    # ── 팝업 닫기 ─────────────────────────────────────────────
    # ⚠️ 페이지 본문은 절대 클릭하지 않는다. '진짜 오버레이(모달/알림/드로어)'가
    #    떠 있을 때만, 그 오버레이 컨테이너 '내부'의 닫기(X) 버튼만 클릭한다.
    #    (예전엔 '확인'/'닫기' 텍스트를 페이지 전역에서 클릭해 표의 '확인' 칩까지
    #     눌러버리는 사고가 있었음 → 텍스트 전역 클릭 제거)
    POPUP_ROOT_SELECTORS = [
        ".ant-modal-wrap", ".ant-modal-root",
        ".ant-notification", ".ant-drawer",
        ".MuiDialog-root", ".MuiModal-root",
        "[role='dialog']", "[role='alertdialog']",
    ]
    # 오버레이 '내부'에서만 찾는 닫기 버튼
    POPUP_CLOSE_SELECTORS = [
        ".ant-modal-close", ".ant-modal-close-x",
        ".ant-notification-notice-close", ".ant-drawer-close",
        "button[aria-label='Close']", "[aria-label='close']", "[aria-label='닫기']",
    ]
    # 오버레이 '내부'에서만 찾는 텍스트 닫기(공지/광고 전용, 본문엔 없는 문구)
    POPUP_CLOSE_TEXTS = [
        "오늘 하루 보지 않기", "오늘 하루 그만보기", "다시 보지 않기", "그만 보기",
    ]
    EXTRA_POPUP_SELECTORS: List[str] = []

    def __init__(self, credentials: Optional[dict] = None):
        self.credentials = credentials or {}
        # 시트 1행에 추가로 쓸 셀(예: 잔액) {"E1": "...", "F1": "..."}. scrape 중 채움.
        self.header_cells: Dict[str, str] = {}
        self.login_error = ""       # 자동 로그인 실패 사유(어디서 막혔는지 남긴다)
        self._lookup_note = ""      # 폼 조회가 막힌 이유(알림창/예외)
        self.login_attempted = True  # '제출까지 갔는지' — 실패 시 백오프 길이를 가른다

    # 외부 진입점: 데모면 가짜, 아니면 셀레니움 수집
    def collect(self) -> List[Consultation]:
        if DEMO_MODE:
            return _generate_demo(self)
        driver = make_driver(self.key, headless=HEADLESS)
        try:
            self.dismiss_popups(driver)
            if not self.is_logged_in(driver):
                print(f"[{self.name}] 세션 없음 — 자동 로그인 시도")
                if not self.login(driver):
                    print(f"[{self.name}] 자동 로그인 미지원/실패 — 건너뜀")
                    return []
                print(f"[{self.name}] 자동 로그인 성공")
            self.dismiss_popups(driver)
            return self._scrape(driver)
        finally:
            driver.quit()

    def is_logged_in(self, driver) -> bool:
        """로그인 상태 확인. 채널마다 override (기본 True)."""
        return True

    def login(self, driver) -> bool:
        """저장된 계정으로 자동 로그인. 구현한 채널만 True 반환(미구현=수동 필요)."""
        return False

    # ══════════════════════════════════════════════════════════
    # [자동 로그인 공통부]  '로그인 버튼을 확실히 누르는' 단계
    # ══════════════════════════════════════════════════════════
    # 세션이 만료되면 Hub 가 login() 을 부른다. 그런데 셀레니움은
    #   · 버튼을 못 찾으면 → 예외로 login() 이 그대로 끝나고
    #   · disabled(폼 검증 미반영) 버튼을 클릭하면 → '조용히' 아무 일도 안 한다
    # 둘 다 '아이디·비번만 채워진 화면'을 남기고 10분 백오프('로그인 대기')로 갔다.
    # → 아래 공통부로 [입력 반영 확인 → 활성화 대기 → 클릭 폴백 → 재클릭] 을 보장한다.
    # 채널별로 다른 건 셀렉터/URL표식 클래스 변수와 hook override 로 맞춘다.
    ID_SELECTORS: tuple = ("input[name='id']", "input[name='userId']",
                           "input[name='username']", "form input[type='text']")
    PW_SELECTORS: tuple = ("input[type='password']",)
    EXTRA_LOGIN_BUTTONS: tuple = ()      # 채널 고유 버튼(가장 먼저 시도)
    LOGIN_BUTTON_SELECTORS: tuple = (    # 공통 후보(위에서부터)
        ("xpath", "//button[@type='submit'][contains(.,'로그인')]"),
        ("xpath", "//button[@type='submit']"),
        ("css", "input[type='submit']"),
        ("xpath", "//button[contains(normalize-space(.),'로그인')]"),
        ("xpath", "//*[@role='button'][contains(normalize-space(.),'로그인')]"),
    )
    LOGIN_URL_MARK = "/login"   # URL 에 이 문구가 있으면 '아직 로그인 화면'
    LOGIN_SUBMIT_RETRY = 3      # 클릭이 씹혀 폼이 남을 때 재클릭 횟수
    LOGIN_WAIT_SEC = 15         # 클릭 후 로그인 완료까지 대기(초)
    POST_SUBMIT_ALERT_SEC = 0.0  # 클릭 직후 '결과 alert' 을 먼저 읽는 시간(초)
                                 # >0 이면 로그인 결과를 alert 으로만 알려주는 사이트용
    # 이 문구가 alert 에 들어 있으면 '계정 거부' — 재클릭해도 소용없고,
    # 오히려 사이트의 '연속 실패 횟수'만 채워 계정이 잠긴다 → 즉시 중단한다.
    # ※ 너무 흔한 말('않습니다','잠')은 넣지 않는다 — 멀쩡한 안내까지 거부로 오인한다.
    REJECT_ALERT_WORDS = ("잘못된", "일치하지", "제한", "차단", "잠겼", "잠금",
                          "비밀번호를", "다시 입력", "실패")
    FILL_WITH_KEYS = False      # True=실제 키 입력을 먼저(값 주입이 막히는 폼)
    FILL_PAUSE_SEC = 0.4        # 한 칸 입력 후 쉬는 시간(봇 탐지 완화용으로 늘림)
    # 폼이 아직 안 그려졌을 때: '새로고침 → 다시 보기'를 이만큼 반복한다.
    # 클릭(제출)은 하지 않으므로 사이트의 '연속 로그인 실패' 카운터와 무관하다
    # → 몇 번 더 새로고침해도 계정이 잠기지 않는다.
    LOGIN_PAGE_RETRY = 3
    LOGIN_RELOAD_WAIT_SEC = 1.5     # 새로고침 전에 잠깐 쉼(렌더 여유)
    # 제출을 '한 번도 못 해본' 실패(폼/버튼 못 찾음 등)는 사이트에 아무 흔적이 없다
    # → 길게 쉴 이유가 없다. 이만큼만 쉬고 바로 다시 시도한다.
    LOGIN_BACKOFF_SHORT_SEC = 30

    def _first_visible(self, driver, selectors):
        """CSS selectors 중 화면에 보이는 첫 요소. 없으면 None.

        ⚠️ alert 이 떠 있으면 find_elements 가 예외를 낸다(바비톡: 401 마다 만료
           alert 이 연달아 쌓임). 드라이버를 unhandledPromptBehavior='ignore' 로
           띄우므로 알림창은 우리가 닫지 않는 한 그대로 살아있다 →
           같은 셀렉터를 다시 조회해도 계속 막힌다.
           → 막히면 '확인'을 눌러(=_pop_alert) 치운 뒤 다시 조회한다.
             (사람이 하는 것과 같은 순서: 만료 팝업 확인 → 그다음 폼 조작)
           왜 '문구를 기록'하나: 예전엔 예외를 통째로 삼켜서, 폼이 화면에 멀쩡히
           있는데도 '폼을 찾지 못했습니다'라는 거짓 사유만 남았다."""
        from selenium.webdriver.common.by import By
        self._lookup_note = ""
        for sel in selectors:
            for _ in range(6):          # 연달아 쌓인 alert 개수만큼만 재시도
                try:
                    for e in driver.find_elements(By.CSS_SELECTOR, sel):
                        if e.is_displayed():
                            return e
                    break               # 정상 조회 — 이 셀렉터엔 없음
                except Exception as ex:
                    # 알림창에 막힌 것이면 '확인'을 눌러 치우고 재조회.
                    msg = self._pop_alert(driver, 0.3)
                    self._lookup_note = (f"알림창 '{' '.join(msg.split())[:60]}'" if msg
                                         else f"{type(ex).__name__}")
                    time.sleep(0.2)
        # 셀레니움이 '없다'고 해도 브라우저에 직접 물어본다(마지막 그물).
        el = self._visible_by_js(driver, tuple(("css", s) for s in selectors))
        if el is not None:
            self._lookup_note = "is_displayed()=False (브라우저 판정으로 찾음)"
            print(f"[{self.name}] is_displayed() 로는 못 찾은 칸을 "
                  f"브라우저 판정(JS)으로 찾았습니다 — 그대로 진행")
        return el

    @staticmethod
    def _visible_by_js(driver, pairs):
        """브라우저에게 직접 물어 '보이는 첫 요소'를 받아온다. 없으면 None.
        pairs = (("css"|"xpath", 셀렉터), ...) — 위에서부터 순서대로 본다.

        왜 필요한가(실측): 바비톡 /login 에서 셀레니움 is_displayed() 만 False 로
        나와 '로그인 폼을 찾지 못했습니다'로 죽는 일이 있었다. 같은 순간에 찍힌
        진단 문구는 '입력칸 2개(보임 2) · 보이는칸[text/ID/form-control ,
        password/비밀번호/form-control]' — 화면엔 멀쩡히 있었다는 뜻이다.
        (렌더 타이밍·오버레이·애니메이션 등으로 is_displayed() 가 틀릴 수 있다.)
        → 사람 눈과 같은 기준(레이아웃 상자가 있고 숨김 스타일이 아님)으로 다시 본다.
          position:fixed 는 offsetParent 가 null 이라 예외 처리한다."""
        try:
            return driver.execute_script("""
              var pairs = arguments[0];
              function vis(e){
                if(!e) return false;
                var st = window.getComputedStyle(e);
                if(st.visibility === 'hidden' || st.display === 'none') return false;
                if(e.offsetParent === null && st.position !== 'fixed') return false;
                var r = e.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
              }
              for (var i=0;i<pairs.length;i++){
                var how = pairs[i][0], sel = pairs[i][1], els = [];
                try {
                  if (how === 'xpath'){
                    var it = document.evaluate(sel, document, null, 7, null);
                    for (var k=0;k<it.snapshotLength;k++) els.push(it.snapshotItem(k));
                  } else {
                    els = Array.prototype.slice.call(document.querySelectorAll(sel));
                  }
                } catch(err) { continue; }
                for (var j=0;j<els.length;j++) if (vis(els[j])) return els[j];
              }
              return null;
            """, [list(p) for p in pairs])
        except Exception:
            return None

    @staticmethod
    def _page_hint(driver) -> str:
        """실패 사유에 붙일 현재 화면 요약 — 다른 PC 에서 난 실패를 로그만 보고
        따라갈 수 있게 한다(어떤 화면이었는지, 입력칸이 정말 없었는지).

        ⚠️ '입력칸 2개(보임 2)' 처럼 개수만 남기면 아무 것도 진단할 수 없다.
           (실제로 '폼을 못 찾았다'면서 '보임 2' 라고 적힌 알림이 왔는데,
            그 2개가 우리 셀렉터에 안 걸리는 칸인지 알림창에 막혔던 것인지
            구분이 안 됐다.) → 보이는 입력칸의 type/placeholder/class 를 함께 남긴다.

        ⚠️ '보임' 을 offsetParent 하나로 세면 안 된다 — visibility:hidden / 크기 0 인
           칸도 '보임' 으로 세어져(실측 확인) '화면엔 멀쩡한데 왜 못 찾지?' 하는
           엉뚱한 진단으로 이어진다. → 숨김 스타일·상자 크기까지 본 '진짜보임' 을
           따로 센다. 둘이 다르면 그게 곧 원인(그려지다 만 화면)이다."""
        try:
            n = driver.execute_script(
                "var a=document.querySelectorAll('input');"
                "function laid(e){return e.offsetParent!==null"
                "  || getComputedStyle(e).position==='fixed';}"
                "function real(e){var s=getComputedStyle(e);"
                "  if(s.visibility==='hidden'||s.display==='none') return false;"
                "  var r=e.getBoundingClientRect();"
                "  return laid(e) && r.width>0 && r.height>0;}"
                "var vis=Array.prototype.filter.call(a,laid);"
                "var hard=Array.prototype.filter.call(a,real);"
                "return [a.length, vis.length, document.readyState,"
                " vis.slice(0,4).map(function(e){"
                "   return (e.getAttribute('type')||'(type없음)')"
                "     +'/'+(e.placeholder||e.name||e.id||'-')"
                "     +'/'+String(e.className||'-').slice(0,20)"
                "     +(real(e)?'':'/숨김');}).join(' , '), hard.length];")
            return (f"url={(driver.current_url or '')[:60]} · "
                    f"입력칸 {n[0]}개(보임 {n[1]}/진짜보임 {n[4]}) · {n[2]}"
                    + (f" · 보이는칸[{n[3]}]" if n[3] else ""))
        except Exception as e:
            return f"화면 상태도 읽지 못함({type(e).__name__})"

    @staticmethod
    def _react_fill(driver, el, value) -> None:
        """React 컨트롤드 인풋에 값 주입(send_keys 가 값 등록 안 되는 폼 대응).
        네이티브 value setter로 값 설정 + input/change 이벤트 발생 → React 상태 반영."""
        try:
            el.click()
        except Exception:
            pass
        driver.execute_script(
            "var d=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');"
            "d.set.call(arguments[0], arguments[1]);"
            "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
            el, value)

    @staticmethod
    def _key_fill(driver, el, value) -> None:
        """실제 키 입력으로 채운다(키 이벤트로만 폼 검증을 하는 화면 대응)."""
        try:
            el.click()
        except Exception:
            pass
        el.clear()
        el.send_keys(value)

    # 세션이 만료되면 alert 이 '페이지 로드가 끝난 뒤' 늦게 뜨는 사이트가 있다.
    # (바비톡: /ask 로드 0.5초 → 0.8초에 '로그인 기한이 만료되었습니다' alert)
    # 그 창이 열려 있는 동안은 셀레니움 명령이 전부 막히므로 채널별로 더 기다린다.
    ALERT_SETTLE_SEC = 0.0

    @staticmethod
    def _pop_alert(driver, wait: float = 0.0) -> str:
        """열려 있는 JS alert 을 '전부' 닫고 첫 문구를 반환. 없으면 ''.
        wait>0 이면 그동안 늦게 뜨는 alert 도 폴링해서 닫는다.

        ⚠️ 닫지 않으면 이후 모든 셀레니움 명령이 UnexpectedAlertPresentException 으로
           막힌다. 게다가 chromedriver 기본값이 'dismiss and notify' 라 그 다음 명령이
           alert 을 '취소'로 닫아버린다 — 즉 우리가 확인을 누른 게 아니라 브라우저가
           멋대로 취소한 셈이 되고, 예외까지 올라가 그 채널이 통째로 실패한다.
        만료 안내는 XHR 이 401 날 때마다 뜨므로 연속으로 여러 개가 쌓일 수 있다."""
        first = ""
        end = time.monotonic() + max(0.0, wait)
        while True:
            try:
                al = driver.switch_to.alert
                msg = (al.text or "").strip()
                al.accept()
                first = first or (msg or "알림창")
                time.sleep(0.25)
                continue                    # 뒤에 쌓인 alert 이 또 있는지 계속 확인
            except Exception:
                pass                        # 지금은 없음 → 남은 시간만큼 더 지켜본다
            if time.monotonic() >= end:
                return first
            time.sleep(0.2)

    def _at_login_page(self, driver) -> bool:
        """아직 로그인 화면인지(URL 표식 기준). 판별이 다른 채널은 override."""
        try:
            return self.LOGIN_URL_MARK.lower() in (driver.current_url or "").lower()
        except Exception:
            return True                 # URL 조차 못 읽으면 넘어가지 못한 것으로 본다

    def _goto_login_page(self, driver) -> None:
        """로그인 화면으로 이동. 홈에서 '로그인'을 눌러야 하는 채널은 override."""
        driver.get(self.LOGIN_URL)

    def _prepare_submit(self, driver) -> None:
        """제출 직전 채널별 추가 처리(자동로그인/로그인상태유지 체크 등)."""
        return

    def _id_input_near_pw(self, driver):
        """비번칸을 기준으로 '그 앞의 보이는 입력칸'을 아이디 칸으로 본다. 없으면 None.

        왜 필요한가: ID_SELECTORS 는 placeholder('ID')·class('form-control') 같은
        '사이트가 언제든 바꿀 수 있는 표식'에 의존한다. 하나만 바뀌어도 아이디 칸을
        못 찾아 '폼을 찾지 못했습니다'로 죽는다(비번칸은 type=password 라 안 죽는다
        → 매번 아이디 칸만 문제가 된다). 로그인 폼의 '비번칸 바로 위가 아이디 칸'
        이라는 구조는 사이트가 개편돼도 잘 안 바뀌므로 이걸 마지막 그물로 쓴다.
        체크박스(로그인 유지)·숨은 칸·검색창은 제외한다."""
        try:
            return driver.execute_script("""
              var all=Array.prototype.slice.call(document.querySelectorAll('input'));
              var pw=null;
              for (var i=0;i<all.length;i++){
                if(all[i].type==='password' && all[i].offsetParent!==null){pw=all[i];break;}}
              if(!pw) return null;
              var scope = pw.form || document;
              var cands = Array.prototype.filter.call(scope.querySelectorAll('input'),
                function(e){ return e!==pw && e.offsetParent!==null
                  && !/^(password|checkbox|radio|hidden|submit|button|file|image)$/
                       .test(e.type); });
              var before = cands.filter(function(e){
                return pw.compareDocumentPosition(e)
                       & Node.DOCUMENT_POSITION_PRECEDING; });
              return (before.length ? before[before.length-1] : cands[0]) || null;
            """)
        except Exception:
            return None

    def _find_login_inputs(self, driver):
        """(아이디칸, 비번칸) 을 찾는다. 못 찾으면 해당 자리에 None.

        순서: 알림창 치우기('확인') → 셀렉터 조회 → 아이디 칸은 구조 기반 폴백.
        알림창은 401 마다 늦게 또 뜨므로 라운드마다 다시 치운다."""
        blocked = self._pop_alert(driver, 1.0)
        id_in = self._first_visible(driver, self.ID_SELECTORS)
        note = self._lookup_note
        pw = self._first_visible(driver, self.PW_SELECTORS)
        note = note or self._lookup_note
        if id_in is None and pw is not None:
            id_in = self._id_input_near_pw(driver)      # 표식이 바뀐 경우의 마지막 그물
            if id_in is not None:
                print(f"[{self.name}] 아이디 칸을 셀렉터로 못 찾아 "
                      f"'비번칸 기준 폴백'으로 찾았습니다 — 셀렉터 갱신 필요")
        return id_in, pw, (blocked or note)

    def _fill_login_form(self, driver) -> bool:
        """아이디/비번을 '값이 실제로 들어간 상태'로 만든다. 실패 시 False.
        값 주입 후 value 를 다시 읽어 확인하고, 반영이 안 됐으면 반대 방식으로 폴백한다."""
        # ⚠️ '못 찾음'을 곧이곧대로 믿지 말 것. 만료 alert 이 떠 있어 명령이 막힌
        #    경우도 None 이 된다(폼은 화면에 멀쩡히 있는데도). 알림창을 확인으로
        #    치우고 다시 보는 것을 3라운드 반복한다 — 만료 안내는 XHR 401 마다
        #    새로 뜨기 때문에 '한 번 치우고 한 번 보기'로는 계속 어긋날 수 있다.
        why = ""
        for _ in range(3):
            id_in, pw, note = self._find_login_inputs(driver)
            why = note or why
            if id_in is not None and pw is not None:
                break
            time.sleep(0.8)
        if id_in is None or pw is None:
            missing = ("아이디·비번 칸" if id_in is None and pw is None
                       else "아이디 칸" if id_in is None else "비번 칸")
            self.login_error = (f"로그인 폼({missing})을 찾지 못했습니다 "
                                f"({self._page_hint(driver)}"
                                + (f" · 막힌이유={' '.join(why.split())[:60]}" if why else "")
                                + ")")
            return False
        first, second = ((self._key_fill, self._react_fill) if self.FILL_WITH_KEYS
                         else (self._react_fill, self._key_fill))
        for el, val in ((id_in, self.USER_ID), (pw, self.USER_PW)):
            for fill in (first, second):
                try:
                    fill(driver, el, val)
                except Exception:
                    pass
                time.sleep(self.FILL_PAUSE_SEC)
                if (el.get_attribute("value") or "") == val:
                    break               # 값이 들어갔으면 폴백은 하지 않는다
        if not ((id_in.get_attribute("value") or "")
                and (pw.get_attribute("value") or "")):
            self.login_error = "아이디/비번 입력이 폼에 반영되지 않았습니다"
            return False
        return True

    def _retype_with_keys(self, driver) -> None:
        """아이디/비번을 실제 키 입력으로 다시 친다.
        keyup 등 '키 이벤트'로만 폼 검증을 하는 화면에서 제출 버튼이 계속
        비활성으로 남는 것을 푼다(값 주입만으론 안 풀린다)."""
        for sels, val in ((self.ID_SELECTORS, self.USER_ID),
                          (self.PW_SELECTORS, self.USER_PW)):
            el = self._first_visible(driver, sels)
            if el is None:
                continue
            try:
                self._key_fill(driver, el, val)
                time.sleep(0.3)
            except Exception:
                pass

    def _login_button(self, driver):
        """화면에 보이는 '로그인' 제출 버튼. 없으면 None.
        채널 고유 버튼(EXTRA_LOGIN_BUTTONS)을 먼저, 그다음 공통 후보를 본다."""
        from selenium.webdriver.common.by import By
        by_map = {"css": By.CSS_SELECTOR, "xpath": By.XPATH}
        for how, sel in tuple(self.EXTRA_LOGIN_BUTTONS) + tuple(self.LOGIN_BUTTON_SELECTORS):
            for _ in range(3):
                try:
                    for e in driver.find_elements(by_map[how], sel):
                        if e.is_displayed():
                            return e
                    break
                except Exception:
                    # 알림창에 막힌 것이면 '확인'을 눌러 치우고 재조회
                    # (치우지 않으면 버튼이 멀쩡히 있는데도 '버튼을 찾지 못했습니다'가 된다)
                    self._pop_alert(driver, 0.3)
        # 입력칸과 같은 이유로, is_displayed() 가 틀린 경우를 대비한 마지막 그물
        return self._visible_by_js(
            driver, tuple(self.EXTRA_LOGIN_BUTTONS) + tuple(self.LOGIN_BUTTON_SELECTORS))

    def _submit_login(self, driver) -> bool:
        """'로그인' 버튼을 실제로 누른다(눌렀으면 True).
        비활성 버튼은 활성화 대기 → 실제 키 재입력으로 유도하고,
        클릭은 일반 → JS → 비번칸 Enter → form 직접 제출 순으로 폴백한다."""
        from selenium.webdriver.common.keys import Keys

        btn = self._login_button(driver)
        for _ in range(10):             # 비활성 버튼은 활성화될 때까지 최대 5초 대기
            if btn is None or btn.is_enabled():
                break
            time.sleep(0.5)
            btn = self._login_button(driver)

        # 여전히 비활성 → 값 주입만으론 폼 검증이 안 걸린 것(키 이벤트로 검증하는 폼)
        if btn is not None and not btn.is_enabled():
            self._retype_with_keys(driver)
            for _ in range(6):
                btn = self._login_button(driver)
                if btn is None or btn.is_enabled():
                    break
                time.sleep(0.5)

        if btn is not None and btn.is_enabled():
            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", btn)
            except Exception:
                pass
            for click in (lambda: btn.click(),
                          lambda: driver.execute_script("arguments[0].click();", btn)):
                try:
                    click()
                    return True
                except Exception:
                    continue

        # 버튼이 없거나 비활성/클릭 불가 → 비번칸 Enter → 폼 직접 제출
        pw = self._first_visible(driver, self.PW_SELECTORS)
        if pw is not None:
            try:
                pw.send_keys(Keys.ENTER)
                return True
            except Exception:
                pass
            try:
                driver.execute_script(
                    "var f=arguments[0].form;"
                    "if(f){f.requestSubmit?f.requestSubmit():f.submit();}", pw)
                return True
            except Exception:
                pass
        self.login_error = ("로그인 버튼이 계속 비활성 상태입니다" if btn is not None
                            else "로그인 버튼을 찾지 못했습니다")
        return False

    def _note_alert(self, msg: str) -> bool:
        """클릭 후 뜬 alert 을 실패 사유로 기록. '계정 거부'면 True(=중단하라)."""
        if not msg:
            return False
        self.login_error = " ".join(msg.split())[:120]      # 여러 줄 → 한 줄
        print(f"[{self.name}] 로그인 알림창: {self.login_error}")
        if self._is_reject_alert(self.login_error):
            print(f"[{self.name}] 로그인 거부 — 재클릭하지 않습니다(계정 잠김 방지)")
            return True
        return False

    def _is_reject_alert(self, msg: str) -> bool:
        """alert 문구가 '계정 거부'인가(= 다시 눌러도 안 되는 실패인가).

        세션 만료 안내('로그인 기한이 만료되었습니다')처럼 '다시 로그인하면 되는'
        알림과 구분해야 한다. 만료 안내는 재클릭이 정답이지만, 아이디/비번 거부는
        재클릭이 사이트의 연속 실패 카운터만 올려 계정을 잠근다."""
        t = " ".join((msg or "").split())
        if not t or "만료" in t:            # 만료 안내는 거부가 아니다
            return False
        return any(w in t for w in self.REJECT_ALERT_WORDS)

    def _login_error_text(self, driver) -> str:
        """폼에 떠 있는 오류 문구(비번 불일치 등). 없으면 ''.
        오류가 있는데 계속 누르면 계정이 잠기므로 재시도를 멈추는 근거로 쓴다."""
        from selenium.webdriver.common.by import By
        for sel in (".MuiFormHelperText-root.Mui-error", ".Mui-error",
                    ".ant-form-item-explain-error", ".invalid-feedback",
                    ".error-message", "[class*='Toastify__toast-body']"):
            try:
                for e in driver.find_elements(By.CSS_SELECTOR, sel):
                    if e.is_displayed() and (e.text or "").strip():
                        return e.text.strip()[:80]
            except Exception:
                continue
        return ""

    def _open_login_page(self, driver) -> bool:
        """로그인 화면을 열고(=새로고침 포함) 폼이 그려질 때까지 기다린다.
        반환 True = '이미 로그인돼 있다'(로그인 화면이 우리를 튕겨냈다)."""
        from selenium.webdriver.support.ui import WebDriverWait

        # ⚠️ 순서 주의 — alert 을 '이동보다 먼저' 치운다.
        #    예전엔 _goto_login_page() 가 먼저였는데, 세션 만료 alert 이 떠 있으면
        #    그 안의 driver.get() 이 UnexpectedAlertPresentException 으로 터져
        #    login() 이 통째로 실패(=10분 백오프)했다. 정작 로그인 화면은 멀쩡한데
        #    '자동 로그인 실패'로 넘어가던 원인.
        self._pop_alert(driver)
        try:
            self._goto_login_page(driver)
        except Exception:               # 이동 도중 alert 이 튀어나온 경우 — 치우고 재시도
            self._pop_alert(driver, 1.0)
            self._goto_login_page(driver)
        self._pop_alert(driver, self.ALERT_SETTLE_SEC)   # 이동 뒤 늦게 뜨는 것까지
        try:                                # 로그인 폼이 그려질 때까지 대기
            WebDriverWait(driver, 20).until(
                lambda d: self._first_visible(d, self.PW_SELECTORS) is not None
                or not self._at_login_page(d))
        except Exception:
            pass
        return (not self._at_login_page(driver)
                and self._first_visible(driver, self.PW_SELECTORS) is None)

    def _fill_or_reload(self, driver) -> str:
        """폼을 채운다. 아직 안 그려졌으면 '새로고침 후 다시' 를 몇 번 더 해본다.
        반환: "ok"(채움) / "already"(그 사이 로그인돼 있었음) / "fail"(끝내 못 채움).

        왜 이렇게: 폼을 못 찾았다고 곧장 실패로 끝내면, 화면이 조금 늦게 그려졌을
        뿐인데도 백오프(수 분)를 타고 그만큼 수집이 멈춘다. 사람이라면 그냥
        새로고침 한 번 더 하고 진행한다 — 클릭(제출)은 안 하므로 계정도 안전하다."""
        for attempt in range(1, self.LOGIN_PAGE_RETRY + 1):
            if self._fill_login_form(driver):
                self.login_error = ""       # 앞선 시도의 사유는 지운다(해결됨)
                return "ok"
            if attempt >= self.LOGIN_PAGE_RETRY:
                return "fail"
            print(f"[{self.name}] 로그인 폼 준비 안 됨 — 새로고침 후 재시도 "
                  f"({attempt}/{self.LOGIN_PAGE_RETRY - 1}) · {self.login_error}")
            time.sleep(self.LOGIN_RELOAD_WAIT_SEC)
            try:
                if self._open_login_page(driver):
                    return "already"        # 새로고침해 보니 이미 로그인 상태
            except Exception as e:
                self.login_error = f"로그인 화면 새로고침 실패({type(e).__name__})"
        return "fail"

    def _do_login_flow(self, driver) -> bool:
        """공통 자동 로그인: 이동 → 입력(안 되면 새로고침 재시도) → 클릭(폴백)
        → 결과 확인 → 재클릭."""
        from selenium.webdriver.support.ui import WebDriverWait

        self.login_error = ""
        self.login_attempted = False        # 아직 '제출'은 안 했다(짧은 백오프 판단용)
        if self._open_login_page(driver):
            return True                     # 이미 로그인됨(로그인 페이지가 튕겨냄)

        for i in range(1, self.LOGIN_SUBMIT_RETRY + 1):
            filled = self._fill_or_reload(driver)
            if filled == "already":
                print(f"[{self.name}] 새로고침해 보니 이미 로그인 상태 — 그대로 진행")
                self.login_error = ""
                return True
            if filled != "ok":
                print(f"[{self.name}] 로그인 입력 실패 — {self.login_error}")
                return False
            self._prepare_submit(driver)
            if not self._submit_login(driver):
                print(f"[{self.name}] 로그인 클릭 실패 — {self.login_error}")
                return False
            self.login_attempted = True     # 여기서부터는 사이트에 흔적이 남는다
            print(f"[{self.name}] '로그인' 클릭 {i}/{self.LOGIN_SUBMIT_RETRY} — 결과 대기")
            # ⚠️ 순서 주의 — 결과 alert 을 '기다리기보다 먼저' 읽는다.
            #    chromedriver 기본값(dismiss and notify)은 alert 이 떠 있는 동안
            #    들어온 첫 명령으로 그 alert 을 '닫아버리고' 예외를 낸다.
            #    예전엔 아래 WebDriverWait 안의 _at_login_page() 가 그 첫 명령이었고,
            #    거기서 예외를 삼켜(True 반환) 버려서 — 바비톡처럼 로그인 결과를
            #    alert 으로만 알려주는 사이트는 실패 사유가 통째로 사라졌다.
            #    그 결과 '클릭이 씹혔나 보다'로 오판해 재클릭 → 사이트의 연속 실패
            #    카운터를 우리 손으로 채워 계정이 잠기는 악순환이 돌았다.
            if self._note_alert(self._pop_alert(driver, self.POST_SUBMIT_ALERT_SEC)):
                return False                # 거부 alert → 재클릭 금지
            try:                            # 로그인 완료(=로그인 화면 이탈) 대기
                WebDriverWait(driver, self.LOGIN_WAIT_SEC).until(
                    lambda d: not self._at_login_page(d))
            except Exception:
                pass
            if self._note_alert(self._pop_alert(driver)):   # 뒤늦게 뜨는 알림창
                return False
            if not self._at_login_page(driver):
                # 통과했으면 도중에 기록해둔 알림(만료 안내 등)은 실패 사유가 아니다
                self.login_error = ""
                print(f"[{self.name}] 자동 로그인 성공")
                return True
            err = self._login_error_text(driver)
            if err:                         # 비번 오류 등 — 더 눌러도 잠기기만 한다
                self.login_error = err
                print(f"[{self.name}] 로그인 거부: {err}")
                return False
            time.sleep(2)                   # 클릭이 씹힌 경우 → 재클릭

        self.login_error = (self.login_error
                            or "로그인 화면을 넘어가지 못했습니다(클릭 후에도 잔류)")
        return False

    def dismiss_popups(self, driver) -> None:
        """
        떠 있는 '오버레이(모달/알림/드로어)' 가 있을 때만, 그 컨테이너 내부의
        닫기(X) 버튼만 클릭한다. 오버레이가 없으면 아무것도 하지 않는다.
        → 페이지 본문/표 요소는 절대 클릭하지 않는다(읽기 전용 스크랩 보장).
        """
        from selenium.webdriver.common.by import By

        close_sels = self.POPUP_CLOSE_SELECTORS + self.EXTRA_POPUP_SELECTORS
        for _ in range(2):                       # 겹친 오버레이 대비 2회
            # 1) 실제로 떠 있는 오버레이 컨테이너만 수집
            roots = []
            for rsel in self.POPUP_ROOT_SELECTORS:
                try:
                    roots += [r for r in driver.find_elements(By.CSS_SELECTOR, rsel)
                              if r.is_displayed()]
                except Exception:
                    pass
            if not roots:
                return                           # 오버레이 없음 → 클릭 안 함

            clicked = False
            for root in roots:
                for sel in close_sels:           # 오버레이 '내부' 닫기 버튼만
                    try:
                        for el in root.find_elements(By.CSS_SELECTOR, sel):
                            if el.is_displayed():
                                try:
                                    el.click()
                                except Exception:
                                    driver.execute_script("arguments[0].click();", el)
                                clicked = True
                    except Exception:
                        pass
                for txt in self.POPUP_CLOSE_TEXTS:   # 오버레이 '내부' 텍스트만
                    try:
                        for el in root.find_elements(
                                By.XPATH, f".//*[normalize-space()='{txt}']"):
                            if el.is_displayed():
                                el.click()
                                clicked = True
                    except Exception:
                        pass
            if not clicked:
                return

    @abstractmethod
    def _scrape(self, driver) -> List[Consultation]:
        """LIST_URL 을 열어 상담 목록을 긁어 Consultation 리스트로 반환."""
        raise NotImplementedError

    # BrowserHub 진입점: 현재 탭에서 스크랩. 기본은 레거시 _scrape 위임.
    # 대시보드 채널은 dict 리스트를 반환하도록 override.
    def scrape(self, driver) -> list:
        return self._scrape(driver)

    # 시트 기록용(override): 스크랩 dict → 시트 행(list), 기록 후 서식 처리
    SHEET_TAB: str = ""
    SHEET_START: str = "A1"
    SHEET_CLEAR: str = ""
    # 같은 탭 안에 '본 목록과 다른 모양의 블록'을 하나 더 쓸 때 비울 범위들.
    # (예: 강남언니 탭의 Q&A 블록 M3:Q1000 — 본 목록 B3:K1000 과 안 겹친다)
    SHEET_CLEAR_EXTRA: tuple = ()

    def to_sheet_rows(self, items: list) -> list:
        return []

    def extra_sheet_data(self, items: list) -> list:
        """같은 batch_update 에 함께 실을 추가 블록.
        [{"range": "M3", "values": [[...], ...]}, ...] 형태. 기본은 없음.
        ※ 쓰기 호출 수를 늘리지 않으려고 별도 update 가 아니라 batch 에 합친다."""
        return []

    def after_write(self, ws) -> None:
        pass

    # 대시보드 통합용: 각 미확인 건 → [이름, 내용, 시각, 연락처] (override)
    def dashboard_rows(self, items: list) -> list:
        return []

    def make_id(self, external_id: str) -> str:
        return f"{self.key}:{external_id}"


# ── 채널 정의 (URL/선택자는 실제 사이트에 맞게 채우기) ────────
@register
class BabitalkChannel(BaseChannel):
    key, name, color = "babitalk", "바비톡", "#FF6B9D"
    LOGIN_URL = "https://client.babitalk.com/login"
    LIST_URL = "https://client.babitalk.com/ask"
    USER_ID, USER_PW = account("babitalk")

    # 시트 매핑: /ask 테이블 td#0~#15 → '바비톡' 탭 B~Q (헤더 순서 동일)
    #   B CS현황 C 고객정보 D 연락처 E 이벤트명/의사명 F 유입경로 G 플랫폼종류
    #   H EID I 상담요청시각 J 부위/시술 K 고객코멘트 L 문자발송 M 상담신청단가
    #   N 소진 O CS메모 P 신청일시(날짜서식) Q 비고
    SHEET_TAB = "바비톡"
    SHEET_START = "B3"
    SHEET_CLEAR = "B3:Q1000"
    N_COLS = 16                      # B~Q
    MIN_COLS = 14                    # 이보다 적으면 데이터행 아님(그룹행/빈행) → 스킵
    NAME_COL = 1                     # cols 내 '고객정보' 인덱스. 셀 첫 줄=국적(국기 라벨)
    DATE_COL = 14                    # cols 내 '신청일시' 인덱스(→ 시트 P열)
    CSMEMO_COL = 13                  # cols 내 'CS메모' 인덱스(→ 시트 O열)
    NOISE = {"arrow_right"}          # material-symbols 아이콘 글자 제거

    def is_logged_in(self, driver) -> bool:
        """현재 탭 기준 판별(네비게이션은 Hub가 수행). SPA 정착까지 대기."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        try:
            WebDriverWait(driver, 12).until(lambda d:
                "/login" in d.current_url
                or d.find_elements(By.CSS_SELECTOR, "input[placeholder='ID']")
                or d.find_elements(By.CSS_SELECTOR, "tbody.MuiTableBody-root tr"))
        except Exception:
            pass
        return ("/login" not in driver.current_url
                and not driver.find_elements(By.CSS_SELECTOR, "input[placeholder='ID']"))

    # 로그인 폼/버튼 셀렉터 — 공통부(BaseChannel)가 이 값으로 폼을 찾는다
    ID_SELECTORS = ("input.form-control[placeholder='ID']",
                    "input[placeholder='ID']",
                    "input[name='id']", "input[name='userId']",
                    "input[name='username']",
                    "form input[type='text']")
    PW_SELECTORS = ("input.form-control[type='password']",
                    "input[type='password']")
    # 세션이 만료된 채 /ask 를 열면 로드가 끝난 '뒤'(실측 약 0.3초 후)
    # '로그인 기한이 만료되었습니다. 다시 로그인해주세요.' alert 이 뜬다.
    # XHR 이 401 날 때마다 뜨므로 여러 개가 연달아 쌓이기도 한다 → 넉넉히 지켜본다.
    ALERT_SETTLE_SEC = 4.0

    # ── 바비톡은 '사람처럼' 한 번만 누른다 ────────────────────────────
    # 바비톡은 로그인 결과를 오직 JS alert 으로만 알려주고(폼에 오류 문구가 없다),
    # 실패가 5회 쌓이면 5분간 로그인을 막는다. 실측한 alert 문구:
    #   '잘못된 아이디 또는 비밀번호입니다.
    #    5회 이상 불일치 할 경우, 5분간 로그인이 제한됩니다.'
    # 예전엔 이 alert 을 읽지 못해 '클릭이 씹혔다'고 오판하고 한 번에 3연타 →
    # 두 사이클이면 5회를 넘겨 스스로 차단을 만들고, 차단 때문에 또 실패하는
    # 악순환에 빠졌다(비밀번호는 멀쩡한데도). 사람이 손으로 할 땐 한 번 누르고
    # 안 되면 몇 분 뒤 다시 누르므로 걸리지 않는다 → 그 방식을 그대로 따른다.
    LOGIN_SUBMIT_RETRY = 1       # 연타 금지: 한 사이클에 클릭 1회만
    POST_SUBMIT_ALERT_SEC = 3.0  # 클릭 직후 결과 alert 을 먼저 읽는다
    LOGIN_BACKOFF_SEC = 5.5 * 60  # 실패 시 5분 30초 쉬었다 재시도(차단 5분 + 여유)

    def login(self, driver) -> bool:
        """저장된 계정으로 자동 로그인(세션 만료 시 Hub 가 호출).
        채우고 → 한 번 누르고 → alert 으로 결과 확인. 거부면 그대로 멈추고,
        Hub 가 LOGIN_BACKOFF_SEC(5분 30초) 뒤에 다시 부른다."""
        return self._do_login_flow(driver)


    @staticmethod
    def _memo_text(driver, td) -> str:
        """CS메모 셀에서 아이콘('+' add 등)만 뺀 '실제 메모 텍스트'를 반환.
        미작성(신규) 셀은 add 아이콘만 있어 ''(빈값)이 된다 → 이게 신규 판정 기준.

        ⚠️ 실제 DOM: 메모 텍스트도 '<button>' 안(<div>)에 들어있다.
           · 빈 셀:  <button><span class=material-symbols-rounded>add</span>…</button>
           · 메모有: <button><div>실제 메모…</div><span class=MuiTouchRipple/></button>
           예전엔 button 을 통째로 제거해서 메모 텍스트까지 날아가 '전부 신규'로
           오분류됐다. → button 은 남기고 '아이콘 span·ripple'만 제거한다."""
        try:
            txt = driver.execute_script(
                "var c=arguments[0].cloneNode(true);"
                "c.querySelectorAll('svg,[class*=material-symbols],"
                "[class*=material-icons],[class*=MuiTouchRipple],i')"
                ".forEach(function(e){e.remove();});"
                "return (c.textContent||'').trim();", td)
        except Exception:
            txt = td.get_attribute("textContent") or ""
        # 아이콘 잔여 리거처 토큰('add','+' 등) 제거 후 남는 순수 텍스트만
        return " ".join(t for t in (txt or "").split()
                        if t not in ("add", "+", "arrow_right", "note_add", "edit"))

    def scrape(self, driver) -> List[dict]:
        """/ask 테이블에서 CS메모(O열) 비어있는 신규(=CS 미확인)만 B~Q로 추출. 실패 시 예외."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        WebDriverWait(driver, 20).until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "tbody.MuiTableBody-root tr")))
        time.sleep(1.2)

        rows = driver.find_elements(By.CSS_SELECTOR, "tbody.MuiTableBody-root tr")
        out = []
        for row in rows:
            tds = row.find_elements(By.TAG_NAME, "td")
            if len(tds) < self.MIN_COLS:        # 데이터 행이 아니면(빈행/그룹행) 스킵
                continue
            cols = []
            for i in range(self.N_COLS):        # 셀이 모자라도 빈칸으로 안전 처리
                if i < len(tds):
                    lines = _cell_lines(tds[i])         # .text(보이는 텍스트)
                    if not lines:                       # 숨겨져 비면 textContent 폴백
                        tc = (tds[i].get_attribute("textContent") or "").strip()
                        if tc:
                            lines = [tc]
                    lines = [l for l in lines if l not in self.NOISE]
                    # 고객정보 셀은 [국적, 이름] 두 줄(국기 라벨 '대한민국' 등이 첫 줄).
                    # 첫 줄(국적)을 떼고 이름만 남긴다 — 국적이 무엇이든(외국인 포함) 동작.
                    if i == self.NAME_COL and len(lines) >= 2:
                        lines = lines[1:]
                    cols.append(" ".join(lines))
                else:
                    cols.append("")
            # 실데이터 아닌 빈 행(고객정보·이벤트명 둘 다 없음) → 스킵
            if not cols[1].strip() and not cols[3].strip():
                continue
            # CS메모칸: 미작성(신규)이면 '+' 버튼만 있으므로, 버튼/아이콘을 뺀
            # '실제 메모 텍스트'로 다시 채워 판정·기록한다(시트 O열도 깨끗해짐).
            if self.CSMEMO_COL < len(tds):
                cols[self.CSMEMO_COL] = self._memo_text(driver, tds[self.CSMEMO_COL])
            # 신규 = CS메모 비어있음(=CS 미확인). 실제 메모 있으면 제외.
            if cols[self.CSMEMO_COL].strip():
                continue
            out.append({"cols": cols})

        # 충전잔액(E1) — money-box-container(고정 클래스)의 '원' 금액.
        # ※ 사이드바가 접혀 화면에 안 보이면 .text 가 빈 값이라, textContent(숨김 여부
        #   무관)로 읽고 정규식으로 금액 추출. 못 잡으면 이전 값 유지(빈 값으로 안 덮음).
        try:
            from selenium.webdriver.support.ui import WebDriverWait
            def _bal(d):
                for b in d.find_elements(By.CSS_SELECTOR, "[class*='money-box-container']"):
                    t = b.get_attribute("textContent") or ""
                    m = re.search(r"[\d][\d,]*\s*원", t)
                    if m:
                        return m.group(0).strip()
                return None
            v = WebDriverWait(driver, 8).until(_bal)
            if v:
                self.header_cells["E1"] = v
        except Exception:
            pass
        return out

    def to_sheet_rows(self, items: list) -> list:
        rows = []
        for it in items:
            c = list(it["cols"])
            c[self.DATE_COL] = _to_sheet_date(c[self.DATE_COL])   # 신청일시 → 날짜값
            rows.append(c)
        return rows

    def dashboard_rows(self, items: list) -> list:
        # [이름=고객정보, 내용=이벤트명, 시각=신청일시, 연락처]
        return [[it["cols"][1], it["cols"][3],
                 _to_sheet_date(it["cols"][14]), it["cols"][2]] for it in items]

    def after_write(self, ws) -> None:
        # 헤더 마지막에 신청일시/비고 추가 + P열(신청일시) 날짜 표시서식
        ws.update(values=[["신청일시", "비고"]], range_name="P2")
        ws.format("P3:P1000", {"numberFormat": {"type": "DATE_TIME",
                                                "pattern": "yyyy-mm-dd hh:mm"}})

    def _scrape(self, driver):
        return []


@register
class GangnamUnniChannel(BaseChannel):
    key, name, color = "gangnamunni", "강남언니", "#00B5A0"
    LOGIN_URL = "https://partner.gangnamunni.com/login"
    LIST_URL = "https://partner.gangnamunni.com/consultation"
    USER_ID, USER_PW = account("gangnamunni")

    # 시트 매핑 (강남언니 탭: B신청일시 C고객정보 D연락처 E상담경로
    #            F결제상담상태 G시술일정 H집도의 I메모 J문자)
    SHEET_TAB = "강남언니"
    SHEET_START = "B3"
    SHEET_CLEAR = "B3:K1000"
    TARGET_STATUS = "신규상담"
    NOISE = {"채팅창으로 이동", "내원일 입력", "시술일 입력",
             "내원예약취소", "시술예정", "내원예약"}

    # ── Q&A(이벤트 문의) ──────────────────────────────────────
    # 상담목록과 같은 파트너 사이트의 별도 페이지. '답변 상태 = 미답변' 만 미확인으로 본다.
    # 시트는 같은 '강남언니' 탭을 쓰되, 상담 블록(B3:K1000)과 절대 겹치지 않는
    # M열 이후에 별도 블록으로 적는다. → 기존 시트/수식/Code.gs 와 충돌 없음.
    #   (M열이 이미 쓰이고 있다면 아래 3개 상수만 다른 열로 바꾸면 된다)
    QNA_URL = "https://partner.gangnamunni.com/service-offer/qna"
    QNA_UNANSWERED = "미답변"
    QNA_START = "M3"
    QNA_CLEAR = "M3:Q1000"
    QNA_HEADER_CELL = "M2"
    QNA_HEADERS = ["작성일", "이벤트id", "이벤트명", "내용", "답변상태"]
    SHEET_CLEAR_EXTRA = (QNA_CLEAR,)
    # 내용 칸에 같이 들어오는 버튼 라벨(질문 본문이 아님)
    QNA_NOISE = {"답변달기", "답변하기", "답변수정", "답변 달기"}
    # 대시보드 '내용' 앞에 붙는 표식. Index.html 이 이걸로 Q&A 행을 구분한다.
    QNA_MARK = "[Q&A] "

    def is_logged_in(self, driver) -> bool:
        """현재 탭 기준 판별(네비게이션은 Hub가 이미 함). SPA 정착까지 대기."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        try:
            WebDriverWait(driver, 12).until(lambda d:
                "/login" in d.current_url
                or d.find_elements(By.ID, "loginId")
                or d.find_elements(
                    By.CSS_SELECTOR, "tbody.ant-table-tbody tr.ant-table-row"))
        except Exception:
            pass
        return ("/login" not in driver.current_url
                and not driver.find_elements(By.ID, "loginId"))

    # 로그인 폼(login_gangnamunni.py 에서 확인) — 공통부가 이 값으로 폼을 찾는다.
    # emotion 해시 클래스가 전역이라 폼 행 div가 아닌 '로그인' span 을 가진
    # submit 버튼을 먼저 노린다(EXTRA_LOGIN_BUTTONS = 최우선 후보).
    ID_SELECTORS = ("#loginId", "input[name='loginId']")
    PW_SELECTORS = ("#loginPw", "input[type='password']")
    EXTRA_LOGIN_BUTTONS = (
        ("xpath", "//button[@type='submit'][.//span[text()='로그인']]"),
    )
    FILL_WITH_KEYS = True       # 기존 동작 유지(실제 키 입력이 검증된 방식)
    FILL_PAUSE_SEC = 1.5        # 기존 흐름의 느린 입력 페이스 유지(봇 탐지 완화)

    def login(self, driver) -> bool:
        """저장된 계정으로 자동 로그인 — 클릭 단계는 BaseChannel 공통부가 처리
        (버튼 활성화 대기 · 클릭 폴백 · 재클릭)."""
        return self._do_login_flow(driver)

    def _set_page_size(self, driver, size: str = "100") -> None:
        """페이지당 표시 개수를 size로 변경(기본 10 → 100). ant-dropdown-trigger 사용.
        실패해도 기본 개수로 진행(예외 안 냄)."""
        from selenium.webdriver.common.by import By
        try:
            trigger = None
            for b in driver.find_elements(By.CSS_SELECTOR, "button.ant-dropdown-trigger"):
                if b.is_displayed() and b.text.strip() in ("10", "25", "50", "100"):
                    trigger = b
                    break
            if not trigger or trigger.text.strip() == size:
                return
            driver.execute_script("arguments[0].scrollIntoView({block:'center'})", trigger)
            trigger.click()
            time.sleep(1)
            for i in driver.find_elements(By.CSS_SELECTOR, ".ant-dropdown-menu-item"):
                if i.is_displayed() and i.text.strip() == size:
                    driver.execute_script("arguments[0].click();", i)
                    break
            time.sleep(2)      # 재조회 대기
        except Exception:
            pass

    def scrape(self, driver) -> List[dict]:
        """/consultation 테이블에서 '메모'(td#8)가 비어있는 신규(=CS 미확인)만. 실패 시 예외."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        # 목록 로딩 대기 — 못 뜨면 예외가 올라가 Hub→러너가 시트를 건드리지 않음
        WebDriverWait(driver, 20).until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "tbody.ant-table-tbody tr.ant-table-row")))
        # 기본 10건만 봄(100건은 느려서 되돌림). 새 상담은 최신순 맨 위에 떠서 잡힘.
        # 필요시 self._set_page_size(driver, "100") 로 확대 가능.
        time.sleep(1.2)

        rows = driver.find_elements(
            By.CSS_SELECTOR,
            "tbody.ant-table-tbody tr.ant-table-row.ant-table-row-level-0")
        out = []
        for row in rows:
            tds = row.find_elements(By.TAG_NAME, "td")
            if len(tds) < 9:                     # 메모(td#8)까지 필요
                continue
            # 신규 = 메모칸에 '메모하기' 버튼만 있는(=아직 메모 안 쓴) 상태.
            #   메모가 있으면 그 텍스트가 뜨고, 없으면 '메모하기' 버튼만 뜸.
            memo = " ".join(l for l in _cell_lines(tds[8]) if l != "메모하기")
            if memo.strip():                     # 실제 메모 텍스트가 있으면 → 제외
                continue
            status = (_cell_lines(tds[4]) or [""])[0]     # 상태 = td#4 첫 줄

            applied = " ".join(_cell_lines(tds[0]))
            try:
                name = row.find_element(
                    By.CSS_SELECTOR,
                    "[data-testid='consultation-txt-name']").text.strip()
            except Exception:
                name = ""
            info = _cell_lines(tds[1])
            birth = info[-1] if info else ""              # 예: '여 / 2007'
            customer = f"{name} ({birth})" if name else birth
            contact = (_cell_lines(tds[2]) or [""])[0]
            route = " / ".join(l for l in _cell_lines(tds[3]) if l not in self.NOISE)
            sisul = " ".join(l for l in _cell_lines(tds[6]) if l not in self.NOISE) \
                if len(tds) > 6 else ""
            doctor = " ".join(_cell_lines(tds[7])) if len(tds) > 7 else ""
            sms = " ".join(_cell_lines(tds[9])) if len(tds) > 9 else ""

            out.append({"kind": "consult",
                        "applied": applied, "customer": customer,
                        "contact": contact, "route": route, "status": status,
                        "sisul": sisul, "doctor": doctor, "memo": memo, "sms": sms,
                        "row_key": row.get_attribute("data-row-key")})

        # 충전잔액(E1) + 노출 잔여기간(F1) — css 해시 대신 안정 클래스로.
        # .text(보이는 텍스트)가 비면 textContent(숨김 무관)로 폴백. 못 잡으면 이전 값 유지.
        def _txt(el):
            return (el.text or el.get_attribute("textContent") or "").strip()
        try:
            v = _txt(driver.find_element(
                By.CSS_SELECTOR,
                "span.ant-typography-ellipsis-single-line.flex-1.text-left"))
            if v:
                self.header_cells["E1"] = v
        except Exception:
            pass
        try:
            v = _txt(driver.find_element(
                By.XPATH, "//span[contains(@class,'ant-typography') "
                          "and contains(@class,'!text-white')]"))
            if v:
                self.header_cells["F1"] = v
        except Exception:
            pass

        # ── Q&A(미답변)도 같은 탭에서 이어서 수집 ────────────────
        # 잔액(E1/F1)은 위에서 이미 읽었으므로 이제 페이지를 옮겨도 안전하다.
        # Q&A 쪽이 깨져도 상담목록 결과는 살린다(전체를 실패로 만들지 않음).
        # 다음 사이클에 BrowserHub 가 LIST_URL 로 다시 이동시키므로 되돌릴 필요 없음.
        try:
            out.extend(self.scrape_qna(driver))
        except Exception as e:
            print(f"[{self.name}] Q&A 수집 건너뜀: {classify_error(e).detail}")
        return out

    # ── Q&A 페이지 ────────────────────────────────────────────
    @staticmethod
    def _qna_date(s: str):
        """Q&A 목록의 작성일 '26.07.29' → '2026-07-29'. 다른 형식은 공통 변환에 맡김."""
        s = (s or "").strip()
        m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{2})", s)
        if m:
            return f"20{m.group(1)}-{m.group(2)}-{m.group(3)}"
        return _to_sheet_date(s)

    def _qna_col_index(self, driver) -> dict:
        """표 머리글(th) 텍스트 → 열 번호. 열 순서가 바뀌어도 따라가도록.
        공백·마침표를 지운 키('No', '이벤트id', '이벤트명', '작성일', '내용', '답변상태')."""
        from selenium.webdriver.common.by import By
        idx = {}
        for i, th in enumerate(driver.find_elements(
                By.CSS_SELECTOR, "thead.ant-table-thead th")):
            k = re.sub(r"[\s.]+", "", th.get_attribute("textContent") or "")
            if k:
                idx.setdefault(k, i)
        return idx

    def _set_qna_filter(self, driver, label: str) -> bool:
        """'답변 상태' 셀렉트(antd Select)를 label 로 바꾼다. 성공 시 True.
        실패해도 예외를 내지 않는다 — 아래에서 행 텍스트로 한 번 더 거르기 때문."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        sel = None
        try:
            for s in driver.find_elements(By.CSS_SELECTOR, ".ant-select-selector"):
                if s.is_displayed():
                    sel = s
                    break
            if sel is None:
                return False
            if (sel.text or "").strip() == label:
                return True
            driver.execute_script("arguments[0].scrollIntoView({block:'center'})", sel)
            sel.click()
            time.sleep(0.8)
            for opt in driver.find_elements(
                    By.CSS_SELECTOR, ".ant-select-dropdown .ant-select-item-option"):
                if opt.is_displayed() and (opt.text or "").strip() == label:
                    driver.execute_script("arguments[0].click();", opt)
                    time.sleep(1.5)                 # 재조회 대기
                    return True
            sel.send_keys(Keys.ESCAPE)              # 옵션 못 찾음 → 드롭다운만 닫기
        except Exception:
            pass
        return False

    def scrape_qna(self, driver) -> List[dict]:
        """/service-offer/qna 에서 답변 상태가 '미답변'인 문의만 반환."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        driver.get(self.QNA_URL)
        self.dismiss_popups(driver)
        # 빈 목록이면 tr.ant-table-placeholder 가 뜬다 → tr 로 기다려야 20초를 안 버린다
        WebDriverWait(driver, 20).until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "tbody.ant-table-tbody tr")))
        time.sleep(1.0)

        # 상태 필터를 '미답변'으로 좁힌다. 성공했을 때만 100건 보기로 넓힌다
        # (전체 목록을 100건으로 펴면 느리다 — 상담목록에서 되돌린 이유와 같음).
        if self._set_qna_filter(driver, self.QNA_UNANSWERED):
            self._set_page_size(driver, "100")

        idx = self._qna_col_index(driver)
        FALLBACK = {"No": 0, "이벤트id": 1, "이벤트명": 2,
                    "작성일": 3, "내용": 4, "답변상태": 5}

        def cell(tds, key):
            i = idx.get(key, FALLBACK.get(key))
            return tds[i] if (i is not None and i < len(tds)) else None

        def lines(tds, key):
            el = cell(tds, key)
            return _cell_lines(el) if el is not None else []

        out = []
        for row in driver.find_elements(
                By.CSS_SELECTOR, "tbody.ant-table-tbody tr.ant-table-row"):
            tds = row.find_elements(By.TAG_NAME, "td")
            if len(tds) < 4:
                continue
            st_el = cell(tds, "답변상태") or tds[-1]
            # '답변완료' 에는 '미답변' 이 안 들어가므로 부분일치로 판별해도 안전
            if self.QNA_UNANSWERED not in " ".join(_cell_lines(st_el)):
                continue
            # 내용 칸에는 질문 + '답변달기' 버튼(미답변) 이 같이 들어온다
            question = " ".join(l for l in lines(tds, "내용")
                                if l not in self.QNA_NOISE)
            out.append({
                "kind": "qna",
                "no": " ".join(lines(tds, "No")),
                "event_id": " ".join(lines(tds, "이벤트id")),
                # 이벤트명 칸엔 썸네일도 있어 여러 줄로 잡힐 수 있다 → 한 줄로 합침
                "event": " ".join(lines(tds, "이벤트명")),
                "date": self._qna_date(" ".join(lines(tds, "작성일"))),
                "question": question,
            })
        return out

    def to_sheet_rows(self, items: list) -> list:
        # 상담 건만 B3:K1000 에. Q&A 는 모양이 달라 extra_sheet_data 로 따로 나간다.
        return [[_to_sheet_date(it["applied"]), it["customer"], it["contact"],
                 it["route"], it["status"], it["sisul"], it["doctor"],
                 it["memo"], it["sms"]]
                for it in items if it.get("kind") != "qna"]

    def extra_sheet_data(self, items: list) -> list:
        qs = [it for it in items if it.get("kind") == "qna"]
        if not qs:
            return []
        return [{"range": self.QNA_START,
                 "values": [[it["date"], it["event_id"], it["event"],
                             it["question"], self.QNA_UNANSWERED] for it in qs]}]

    def dashboard_rows(self, items: list) -> list:
        # 상담: [이름=고객정보, 내용=상담경로, 시각=신청일시, 연락처]
        # Q&A : [이름=이벤트명(줄임), 내용='[Q&A] 질문', 시각=작성일, 연락처=없음]
        #   → 열 구조(5칸)는 그대로 두고 '내용' 앞의 표식으로만 구분한다.
        #     (시트/Code.gs/GUI 어디도 안 바꿔도 되게)
        rows = []
        for it in items:
            if it.get("kind") == "qna":
                ev = it["event"]
                rows.append([(ev[:18] + "…") if len(ev) > 19 else (ev or "Q&A"),
                             self.QNA_MARK + it["question"], it["date"], ""])
            else:
                rows.append([it["customer"], it["route"],
                             _to_sheet_date(it["applied"]), it["contact"]])
        return rows

    def after_write(self, ws) -> None:
        # B열(신청일시) 날짜 표시서식
        ws.format(f"{self.SHEET_START}:B1000",
                  {"numberFormat": {"type": "DATE_TIME",
                                    "pattern": "yyyy.mm.dd hh:mm"}})
        # Q&A 블록 머리글(M2~) — 상담 블록과 떨어져 있어 기존 열을 안 건드린다
        ws.update(values=[self.QNA_HEADERS], range_name=self.QNA_HEADER_CELL)

    def _scrape(self, driver):
        return []


@register
class NaverMapChannel(BaseChannel):
    key, name, color = "naver_map", "네이버지도", "#03C75A"
    LOGIN_URL = "https://new.smartplace.naver.com/"
    BOOKING_BIZ_ID = "762603"
    # 예약 API를 같은 오리진에서 fetch 하려고 LIST_URL 을 예약목록 페이지로 둠
    LIST_URL = (f"https://partner.booking.naver.com/bizes/{BOOKING_BIZ_ID}"
                "/booking-list-view")
    USER_ID, USER_PW = account("naver_map")
    CAPTCHA_WAIT = 150      # 캡챠/추가인증이 뜨면 사람이 풀 수 있게 대기(초)
    LOGIN_HELP = "python naver_login.py 실행 후 캡챠 직접 풀기"

    # 시트 매핑 (네이버지도 탭: B신청일시 C고객명 D연락처 E예약일 F시술/상품
    #            G유입경로 H예약번호). 필터 = CS메모(ownerCommentBody) 비어있는 신규만.
    SHEET_TAB = "네이버지도"
    SHEET_START = "B3"
    SHEET_CLEAR = "B3:K1000"
    SHEET_HEADERS = ["No", "신청일시", "고객명", "연락처", "예약일",
                     "시술/상품", "유입경로", "예약번호"]   # A~H
    WINDOW_DAYS = 90        # 신청일(REGDATE) 조회 범위: 오늘 기준 과거 N일

    def is_logged_in(self, driver) -> bool:
        """
        미로그인 판별: nid로 튕기거나 #id 폼이 보이거나 헤더에 '로그인' 버튼이 보이면 False.
        (place 페이지는 공개라 URL만으론 판별 불가 → 헤더 로그인버튼으로 판별)
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        if "nid.naver.com" in driver.current_url:
            return False
        try:                                   # 헤더 렌더까지 대기
            WebDriverWait(driver, 8).until(lambda d:
                d.find_elements(By.CSS_SELECTOR, "[class*='Header_account']")
                or d.find_elements(By.ID, "id"))
        except Exception:
            pass
        if driver.find_elements(By.ID, "id"):
            return False
        login_btns = driver.find_elements(
            By.XPATH, "//a[normalize-space()='로그인']|//button[normalize-space()='로그인']")
        return not any(b.is_displayed() for b in login_btns)

    def _paste(self, driver, el, text):
        """네이버 봇 탐지 완화: 클립보드 붙여넣기(빠른 send_keys보다 덜 걸림).
        붙여넣기가 막히면(클립보드 접근 불가 등) 값이 조용히 안 들어가므로,
        value 를 확인해 비어 있으면 타이핑으로 폴백한다."""
        from selenium.webdriver.common.keys import Keys
        el.click(); el.clear()
        try:
            import pyperclip
            pyperclip.copy(text)
            el.send_keys(Keys.CONTROL, "v")
            time.sleep(0.3)
        except Exception:
            pass
        if (el.get_attribute("value") or "") != text:
            el.clear(); el.send_keys(text)

    # 로그인 화면 판별은 URL 이 nid(네이버 로그인 도메인)인지로 본다.
    # 재시도는 1회만 — 캡챠가 뜬 상태에서 반복 제출하면 계정이 잠긴다.
    ID_SELECTORS = ("#id", "input[name='id']")
    PW_SELECTORS = ("#pw", "input[name='pw']")
    LOGIN_URL_MARK = "nid.naver.com"
    LOGIN_SUBMIT_RETRY = 1
    LOGIN_WAIT_SEC = CAPTCHA_WAIT       # 캡챠/추가인증을 사람이 풀 시간까지 대기

    def _goto_login_page(self, driver) -> None:
        """홈 오른쪽 상단 '로그인' 클릭 → nid 로그인 페이지(막히면 직접 이동)."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        driver.get(self.LOGIN_URL)
        try:
            WebDriverWait(driver, 20).until(EC.element_to_be_clickable(
                (By.XPATH, "//a[normalize-space()='로그인']"))).click()
        except Exception:
            driver.get("https://nid.naver.com/nidlogin.login")

    def _fill_login_form(self, driver) -> bool:
        """네이버는 값 주입·빠른 타이핑이 봇으로 걸리므로 클립보드 붙여넣기를 쓴다."""
        id_in, pw_in, why = self._find_login_inputs(driver)
        if id_in is None or pw_in is None:
            self.login_error = (f"로그인 폼(아이디/비번 칸)을 찾지 못했습니다 "
                                f"({self._page_hint(driver)}"
                                + (f" · 막힌이유={' '.join(why.split())[:60]}" if why else "")
                                + ")")
            return False
        self._paste(driver, id_in, self.USER_ID)
        time.sleep(2)
        self._paste(driver, pw_in, self.USER_PW)
        time.sleep(1)
        if not ((id_in.get_attribute("value") or "")
                and (pw_in.get_attribute("value") or "")):
            self.login_error = "아이디/비번 입력이 폼에 반영되지 않았습니다"
            return False
        return True

    def _prepare_submit(self, driver) -> None:
        """로그인 상태 유지 ON (실제 체크박스 #nvlong, 클릭 프록시 div#keep).
        세션을 오래 유지해 재로그인(=캡챠 위험) 자체를 줄인다."""
        from selenium.webdriver.common.by import By
        try:
            nvlong = driver.find_element(By.ID, "nvlong")
            if not nvlong.is_selected():
                driver.find_element(By.ID, "keep").click()
        except Exception:
            pass

    def login(self, driver) -> bool:
        """홈 '로그인' 클릭 → nid 폼에 계정 붙여넣기 → 로그인상태유지 ON → 제출.
        제출 버튼 클릭은 공통부가 폴백까지 처리한다. 캡챠/추가인증이 뜨면
        headed 창에서 사람이 풀 시간을 CAPTCHA_WAIT 만큼 준다."""
        ok = self._do_login_flow(driver)
        if not ok:
            print(f"[{self.name}] 로그인 미완료(캡챠/추가인증 가능) — 수동 로그인 필요")
        return ok

    @staticmethod
    def _fmt_phone(p: str) -> str:
        d = "".join(ch for ch in (p or "") if ch.isdigit())
        if len(d) == 11:
            return f"{d[:3]}-{d[3:7]}-{d[7:]}"
        if len(d) == 10:
            return f"{d[:3]}-{d[3:6]}-{d[6:]}"
        return p or ""

    def scrape(self, driver) -> List[dict]:
        """
        네이버 예약 목록 API를 로그인 세션으로 호출 → CS메모(ownerCommentBody)가
        비어있는(=CS 미확인 신규) 예약만 반환. HTML 스크랩이 아니라 JSON API 사용.
        (BrowserHub 가 LIST_URL=예약목록 페이지를 이미 열어둬서 same-origin fetch 가능)
        """
        import json
        # 신청일(REGDATE) 기준 최근 N일 조회 → '신규 유입' 정확도↑.
        # 신청일은 미래가 없으므로 끝날짜=내일(오늘 신청분 tz 안전 포함).
        start = (datetime.now() - timedelta(days=self.WINDOW_DAYS)
                 ).strftime("%Y-%m-%dT00:00:00.000Z")
        end = (datetime.now() + timedelta(days=1)
               ).strftime("%Y-%m-%dT00:00:00.000Z")
        api = (f"https://partner.booking.naver.com/api/businesses/"
               f"{self.BOOKING_BIZ_ID}/bookings?bizItemTypes=STANDARD"
               f"&bookingStatusCodes=&dateFilter=REGDATE"
               f"&startDateTime={start}&endDateTime={end}&page=0&size=300")

        driver.set_script_timeout(30)
        txt = driver.execute_async_script("""
            const cb = arguments[arguments.length - 1];
            fetch(arguments[0], {credentials: 'include'})
              .then(r => r.text()).then(t => cb(t)).catch(e => cb('ERR:' + e));
        """, api)
        data = json.loads(txt)                 # 형식 오류/미로그인 응답이면 예외 → 시트 보호
        if not isinstance(data, list):
            raise RuntimeError(f"예약 API 응답이 목록이 아님: {str(data)[:120]}")

        out = []
        for r in data:
            if (r.get("ownerCommentBody") or "").strip():   # CS메모 있으면 = 확인함 → 제외
                continue
            if r.get("cancelledDateTime"):                  # 취소된 예약 → 제외
                continue
            out.append({
                "reg": r.get("regDateTime", ""),
                "name": r.get("name", ""),
                "phone": self._fmt_phone(r.get("phone", "")),
                "useDate": r.get("startDate", ""),
                "item": r.get("bizItemName", "") or r.get("serviceName", ""),
                "area": r.get("areaName", ""),
                "bookingId": r.get("bookingId", ""),
            })
        out.sort(key=lambda x: x["reg"], reverse=True)      # 신청 최신순
        return out

    def to_sheet_rows(self, items: list) -> list:
        return [[_to_sheet_date(it["reg"]), it["name"], it["phone"],
                 _to_sheet_date(it["useDate"]), it["item"], it["area"],
                 str(it["bookingId"])] for it in items]

    def dashboard_rows(self, items: list) -> list:
        # [이름, 내용=시술/상품, 시각=신청일시, 연락처]
        return [[it["name"], it["item"], _to_sheet_date(it["reg"]), it["phone"]]
                for it in items]

    def after_write(self, ws) -> None:
        # A열은 사용자 수식이므로 건드리지 않음 → 헤더도 B2부터만.
        # + 날짜서식(B 신청일시=날짜+시각, E 예약일=날짜)
        ws.update(values=[self.SHEET_HEADERS[1:]], range_name="B2")
        ws.format("B3:B1000", {"numberFormat": {"type": "DATE_TIME",
                                                "pattern": "yyyy-mm-dd hh:mm"}})
        ws.format("E3:E1000", {"numberFormat": {"type": "DATE",
                                                "pattern": "yyyy-mm-dd"}})

    def _scrape(self, driver):
        return []


@register
class YeosinTicketChannel(BaseChannel):
    key, name, color = "yeosin_ticket", "여신티켓", "#FF4757"
    LOGIN_URL = ""   # TODO: 여신티켓 제휴점 관리자 URL
    LIST_URL = ""    # TODO

    def _scrape(self, driver):
        return []


class ByulstarBase(BaseChannel):
    """byulstar 자사 관리자 공통 로그인(온라인상담/온라인예약 공유). LIST_URL·scrape는 하위에서."""
    LOGIN_URL = "https://www.byulstar.com/manager/login.php"
    USER_ID, USER_PW = account("byulstar")

    def is_logged_in(self, driver) -> bool:
        # 미로그인 시 '로그인 후 이용' alert → 닫고 login.php 로 튕김
        from selenium.common.exceptions import NoAlertPresentException
        try:
            driver.switch_to.alert.accept()
            return False                       # alert = 미로그인
        except NoAlertPresentException:
            pass
        except Exception:
            pass
        return "login" not in driver.current_url.lower()

    # 로그인 폼(login.php) — 공통부가 이 값으로 폼/버튼을 찾는다.
    # URL 표식은 'login'(login.php) — 로그인 후엔 counsel_list.php 등으로 나간다.
    ID_SELECTORS = ("#mId", "input[name='mId']")
    PW_SELECTORS = ("#mPw", "input[name='mPw']", "input[type='password']")
    EXTRA_LOGIN_BUTTONS = (("css", "input[value='LOGIN']"),)
    LOGIN_URL_MARK = "login"
    FILL_WITH_KEYS = True       # 평범한 PHP 폼 — 실제 키 입력이 확실하다

    def _prepare_submit(self, driver) -> None:
        """자동로그인 체크(세션 유지) — 재로그인 자체를 줄인다."""
        from selenium.webdriver.common.by import By
        try:
            chk = driver.find_element(By.CSS_SELECTOR, "input[name='autologin']")
            if not chk.is_selected():
                chk.click()
        except Exception:
            pass

    def login(self, driver) -> bool:
        """byulstar 관리자 자동 로그인(온라인상담·온라인예약 공용 세션).
        LOGIN 버튼 클릭은 공통부가 폴백까지 처리한다."""
        return self._do_login_flow(driver)

    @staticmethod
    def select_value(row) -> str:
        """행 안의 첫 select 선택값 반환(확인유무/신규구분 등 상태 드롭다운)."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import Select
        try:
            s = row.find_elements(By.TAG_NAME, "select")
            if s:
                return Select(s[0]).first_selected_option.text.strip()
        except Exception:
            pass
        return ""


@register
class OnlineConsultChannel(ByulstarBase):
    key, name, color = "online_consult", "온라인상담", "#1E90FF"
    LIST_URL = "https://www.byulstar.com/manager/main/counsel/counsel_list.php"

    # 시트 매핑: counsel_list td#0~#10 → '온라인상담' 탭 B~L (헤더 순서 동일)
    #   B번호 C답변여부 D신규구분 E상담분야 F제목 G작성자 H연락가능한시간
    #   I등록일 J답변일 K선택삭제 L ip
    # 필터: 신규구분(td.newbi select)의 선택값이 '미분류'인 행만(=CS 미분류/미확인).
    SHEET_TAB = "온라인상담"
    SHEET_START = "B3"
    SHEET_CLEAR = "B3:L1000"
    TARGET_NEWBI = "미분류"

    @staticmethod
    def _newbi_value(driver, row) -> str:
        """행의 신규구분 select 선택값(미분류/신규/불량 등) 반환."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import Select
        try:
            for s in row.find_elements(By.CSS_SELECTOR, "td.newbi select, select"):
                if s.tag_name == "select":
                    return Select(s).first_selected_option.text.strip()
        except Exception:
            pass
        try:                                   # JS 폴백
            return driver.execute_script(
                "var s=arguments[0].querySelector('select');"
                "return s?s.options[s.selectedIndex].text.trim():'';", row) or ""
        except Exception:
            return ""

    def scrape(self, driver) -> List[dict]:
        """counsel_list 에서 신규구분이 '미분류'인 행만 B~L로 추출. 실패 시 예외(시트 보호)."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        WebDriverWait(driver, 20).until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "#AllCheckFrm > table > tbody > tr")))
        time.sleep(1)

        rows = driver.find_elements(By.CSS_SELECTOR, "#AllCheckFrm > table > tbody > tr")
        out = []
        for row in rows:
            tds = row.find_elements(By.TAG_NAME, "td")
            if len(tds) < 11:
                continue
            newbi = self._newbi_value(driver, row)
            if newbi != self.TARGET_NEWBI:          # '미분류' 아닌 행 제외
                continue
            out.append({
                "no": tds[0].text.strip(),
                "answered": tds[1].text.strip(),
                "newbi": newbi,
                "field": tds[3].text.strip(),
                "title": " ".join(_cell_lines(tds[4])),
                "writer": tds[5].text.strip(),
                "contact_time": tds[6].text.strip(),
                "reg": tds[7].text.strip(),
                "ans": tds[8].text.strip(),
                "del": tds[9].text.strip(),
                "ip": tds[10].text.strip(),
            })
        return out

    def to_sheet_rows(self, items: list) -> list:
        return [[it["no"], it["answered"], it["newbi"], it["field"], it["title"],
                 it["writer"], it["contact_time"], _to_sheet_date(it["reg"]),
                 _to_sheet_date(it["ans"]), it["del"], it["ip"]] for it in items]

    def dashboard_rows(self, items: list) -> list:
        # [이름=작성자, 내용=제목, 시각=등록일, 연락처=제목에서 추출]
        out = []
        for it in items:
            m = re.search(r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}", it["title"])
            out.append([it["writer"], it["title"], _to_sheet_date(it["reg"]),
                        m.group(0) if m else ""])
        return out

    def after_write(self, ws) -> None:
        # I 등록일 / J 답변일 날짜 표시서식
        for col in ("I", "J"):
            ws.format(f"{col}3:{col}1000",
                      {"numberFormat": {"type": "DATE_TIME",
                                        "pattern": "yyyy-mm-dd hh:mm"}})

    def _scrape(self, driver):
        return []


@register
class OnlineBookingChannel(ByulstarBase):
    key, name, color = "online_booking", "온라인예약", "#FF9500"
    LIST_URL = "https://www.byulstar.com/manager/online/list.php"

    # 시트 매핑: list_table td#0~#9 → '온라인예약' 탭 B~K (헤더 순서 동일)
    #   B번호 C확인유무 D신규구분 E이름 F연락처 G상담부위 H희망예약시간
    #   I등록기기 J등록일 K선택삭제
    # 필터: 확인유무(td#1 select)의 선택값이 '미확인'인 행만.
    SHEET_TAB = "온라인예약"
    SHEET_START = "B3"
    SHEET_CLEAR = "B3:K1000"
    TARGET_CONFIRM = "미확인"

    def scrape(self, driver) -> List[dict]:
        """online/list.php 에서 확인유무가 '미확인'인 행만 B~K로 추출. 실패 시 예외(시트 보호)."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        WebDriverWait(driver, 20).until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "table.list_table tbody tr")))
        time.sleep(1)

        rows = driver.find_elements(By.CSS_SELECTOR, "table.list_table tbody tr")
        out = []
        for row in rows:
            tds = row.find_elements(By.TAG_NAME, "td")
            if len(tds) < 10:                       # 헤더행/빈행 제외
                continue
            confirm = self.select_value(row)         # td#1 확인유무 select 선택값
            if confirm != self.TARGET_CONFIRM:       # '미확인' 아닌 행 제외
                continue
            out.append({
                "no": tds[0].text.strip(),
                "confirm": confirm,
                "newbi": tds[2].text.strip(),
                "name": tds[3].text.strip(),
                "phone": tds[4].text.strip(),
                "area": tds[5].text.strip(),
                "want_time": tds[6].text.strip(),
                "device": tds[7].text.strip(),
                "reg": tds[8].text.strip(),
                "del": tds[9].text.strip(),
            })
        return out

    def to_sheet_rows(self, items: list) -> list:
        return [[it["no"], it["confirm"], it["newbi"], it["name"], it["phone"],
                 it["area"], _to_sheet_date(it["want_time"]), it["device"],
                 _to_sheet_date(it["reg"]), it["del"]] for it in items]

    def dashboard_rows(self, items: list) -> list:
        # [이름, 내용=상담부위, 시각=등록일, 연락처]
        return [[it["name"], it["area"], _to_sheet_date(it["reg"]), it["phone"]]
                for it in items]

    def after_write(self, ws) -> None:
        # H 희망예약시간 / J 등록일 날짜 표시서식
        for col in ("H", "J"):
            ws.format(f"{col}3:{col}1000",
                      {"numberFormat": {"type": "DATE_TIME",
                                        "pattern": "yyyy-mm-dd hh:mm"}})

    def _scrape(self, driver):
        return []


@register
class KakaoTalkChannel(BaseChannel):
    key, name, color = "kakaotalk", "카카오톡", "#F5C400"
    LOGIN_URL = "https://center-pf.kakao.com/_hxlKxcxd/dashboard"
    LIST_URL = "https://business.kakao.com/_hxlKxcxd/chats"       # 채팅 목록
    USER_ID, USER_PW = account("kakaotalk")
    CAPTCHA_WAIT = 150      # 2단계 인증(앱 승인)을 사람이 처리할 시간
    LOGIN_SUBMIT_RETRY = 3  # 로그인 폼이 그대로 다시 뜰 때 submit 재시도 횟수
    LOGIN_HELP = "python kakao_login.py 실행 후 카카오톡 앱에서 2단계 인증 승인"
    # 세션 만료 시 뜨는 '로그인할 카카오계정 선택'(간편로그인) 목록의 계정 항목
    ACCOUNT_PICK_SEL = "ul.list_easy a.wrap_profile"
    ACCOUNT_PICK_RETRY = 6  # 계정 클릭이 씹힐 때 재클릭 횟수(내 계정 클릭이라 위험 없음)

    # 시트 매핑: a.link_chat 블록 → '카카오톡' 탭
    #   B 카톡이름(span.txt_name) C 내용(p.txt_info) D 시각(span.txt_date) E 개수(span.num_round)
    # 필터: num_round >= 1(안 읽은 채팅)만.
    SHEET_TAB = "카카오톡"
    SHEET_START = "B3"
    SHEET_CLEAR = "B3:F1000"

    @staticmethod
    def _at_login(driver) -> bool:
        u = driver.current_url
        return "accounts.kakao.com" in u or "/login" in u.lower()

    def is_logged_in(self, driver) -> bool:
        """URL 이 로그인 화면이 아니고, 로그인 폼·계정 선택 목록도 화면에 없어야 로그인 상태.
        center-pf → accounts 리디렉션이 늦게 걸려 URL 만으로 보면 '로그인됨' 오판이 난다."""
        if self._at_login(driver):
            return False
        try:
            return not (self._at_login_form(driver) or self._account_rows(driver))
        except Exception:
            return True

    def _wait_logged_in(self, driver, secs: float) -> bool:
        """secs 안에 로그인이 끝나면 True. 리디렉션 도중 한 번 True 로 보이는 경우가 있어
        1초 뒤 한 번 더 확인해 두 번 연속 참일 때만 인정한다."""
        end = time.monotonic() + secs
        while True:
            if self.is_logged_in(driver):
                time.sleep(1.0)
                if self.is_logged_in(driver):
                    return True
            if time.monotonic() >= end:
                return False
            time.sleep(0.5)

    def _click_ico_check(self, driver) -> None:
        """보이는 ico_check 체크박스 클릭(간편로그인 저장 / 2차인증 안 함)."""
        from selenium.webdriver.common.by import By
        try:
            for s in driver.find_elements(By.CSS_SELECTOR, "span.ico_comm.ico_check"):
                if s.is_displayed():
                    s.click()
                    return
        except Exception:
            pass

    @staticmethod
    def _login_form_id(driver):
        """로그인 폼의 아이디 입력칸(보이는 것)을 반환. 없으면 None.
        2단계 인증 화면도 accounts.kakao.com 이라 URL 만으로는 구분이 안 되므로,
        '아이디 칸이 있다 = 아직 로그인 폼' 으로 판별한다."""
        from selenium.webdriver.common.by import By
        for e in driver.find_elements(By.CSS_SELECTOR, "input[name='loginId']"):
            if e.is_displayed():
                return e
        return None

    @staticmethod
    def _pw_field(driver):
        """보이는 비밀번호 입력칸. 계정 선택 후에는 비번 칸만 뜨는 화면도 있다."""
        from selenium.webdriver.common.by import By
        for e in driver.find_elements(By.CSS_SELECTOR, "input[name='password']"):
            if e.is_displayed():
                return e
        return None

    def _at_login_form(self, driver) -> bool:
        """아이디/비번 입력 화면(둘 중 하나라도 보이면)."""
        return (self._login_form_id(driver) is not None
                or self._pw_field(driver) is not None)

    @staticmethod
    def _safe_click(driver, el) -> bool:
        try:
            el.click()
            return True
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", el)
                return True
            except Exception:
                return False

    def _account_rows(self, driver) -> list:
        """'로그인할 카카오계정 선택'(간편로그인) 화면의 계정 항목들. 없으면 빈 리스트."""
        from selenium.webdriver.common.by import By
        return [a for a in driver.find_elements(By.CSS_SELECTOR, self.ACCOUNT_PICK_SEL)
                if a.is_displayed()]

    def _acct_match(self, shown: str) -> bool:
        """계정 목록에 표시된 아이디가 USER_ID 와 같은 계정인지.
        카카오가 abcd12**@example.com 처럼 일부를 가려 보여주는 경우도 맞춘다."""
        want = (self.USER_ID or "").strip().lower()
        shown = (shown or "").strip().lower()
        if not want or not shown:
            return False
        if shown == want:
            return True
        return (len(shown) == len(want)
                and all(c in ("*", "•") or c == w for c, w in zip(shown, want)))

    def _pick_saved_account(self, driver) -> bool:
        """세션 만료 후 뜨는 '로그인할 카카오계정 선택' 화면에서 USER_ID 계정을 클릭.
        클릭했으면 True. 그 화면이 아니거나 목록에 USER_ID 가 없으면 False —
        '새로운 계정으로 로그인'은 누르지 않는다(USER_ID 계정으로만 접속)."""
        from selenium.webdriver.support.ui import WebDriverWait

        if not self._account_rows(driver):
            return False
        time.sleep(1.5)     # 목록이 DOM 에 뜬 직후엔 아직 클릭 이벤트가 안 붙어 씹힌다
        rows = self._account_rows(driver)
        if not rows:
            return False

        target, shown = None, ""
        listed = []
        for a in rows:
            t = self._blk_text(a, "span.tit_profile")
            listed.append(t)
            if self._acct_match(t):
                target, shown = a, t
                break

        if target is None:                      # 다른 계정은 절대 누르지 않는다
            print(f"[{self.name}] 계정 목록에 {self.USER_ID} 가 없습니다 "
                  f"(목록: {', '.join(x for x in listed if x) or '없음'}) — "
                  f"직접 로그인해 주세요.")
            return False

        print(f"[{self.name}] 간편로그인 계정 선택: {shown}")
        self._safe_click(driver, target)

        try:            # 화면 전환(로그인 완료 / 비번 입력 / 2단계 인증)까지 대기
            WebDriverWait(driver, 20).until(lambda d: not self._account_rows(d))
        except Exception:
            pass
        time.sleep(1.5)
        return True

    def _clear_account_picker(self, driver) -> bool:
        """계정 선택 화면이 사라질 때까지 내 계정을 눌러본다. 로그인까지 됐으면 True.
        (내 계정을 누르는 것뿐이라 반복해도 계정 잠금 위험이 없어 비번 submit 보다
         재시도를 넉넉히 준다 — 첫 클릭이 씹히는 경우가 잦다.)"""
        for i in range(1, self.ACCOUNT_PICK_RETRY + 1):
            if not self._account_rows(driver):
                return self.is_logged_in(driver)
            if i > 1:
                print(f"[{self.name}] 계정 선택 화면 잔류 — 다시 선택 "
                      f"{i}/{self.ACCOUNT_PICK_RETRY}")
            if not self._pick_saved_account(driver):
                return False        # 목록에 내 계정이 없음 — 더 눌러봐야 소용없다
            if self._wait_logged_in(driver, 10):
                return True
        return False

    @staticmethod
    def _login_error(driver) -> str:
        """로그인 폼에 떠 있는 오류 문구(비밀번호 불일치 등). 없으면 빈 문자열.
        오류가 있는데 submit 을 반복하면 계정이 잠기므로 재시도를 멈추는 근거로 쓴다."""
        from selenium.webdriver.common.by import By
        for sel in ("div.desc_error", "p.desc_error", "div.error_msg", "span.txt_error"):
            for e in driver.find_elements(By.CSS_SELECTOR, sel):
                if e.is_displayed() and e.text.strip():
                    return e.text.strip()
        return ""

    def _submit_login(self, driver) -> bool:
        """'로그인'(button.btn_g.highlight.submit) 클릭.
        일반 클릭이 막히면 JS 클릭 → form.submit() 순으로 폴백한다."""
        from selenium.webdriver.common.by import By
        for by, sel in (
                (By.CSS_SELECTOR, "div.confirm_btn button[type='submit']"),
                (By.CSS_SELECTOR, "button.btn_g.highlight.submit"),
                (By.XPATH, "//button[@type='submit'][normalize-space()='로그인']")):
            for b in driver.find_elements(by, sel):
                if not b.is_displayed():
                    continue
                try:
                    b.click()
                    return True
                except Exception:
                    try:
                        driver.execute_script("arguments[0].click();", b)
                        return True
                    except Exception:
                        pass
        try:                                    # 버튼을 못 찾으면 폼을 직접 제출
            driver.execute_script(
                "document.querySelector(\"input[name='loginId']\").form.submit();")
            return True
        except Exception:
            return False

    def _fill_credentials(self, driver, id_in=None) -> None:
        """아이디/비번 칸이 비어 있을 때만 채운다.
        간편로그인으로 이미 채워진 화면(로그인 버튼만 누르면 되는 상태)은 건드리지 않는다."""
        id_in = id_in or self._login_form_id(driver)
        if id_in is not None and not (id_in.get_attribute("value") or "").strip():
            id_in.clear(); id_in.send_keys(self.USER_ID)
            time.sleep(0.8)
        pw = self._pw_field(driver)             # 계정 선택 후엔 비번 칸만 뜨기도 한다
        if pw is not None and not (pw.get_attribute("value") or ""):
            pw.clear(); pw.send_keys(self.USER_PW)
            time.sleep(0.5)

    def login(self, driver) -> bool:
        """
        세션 만료 시 '로그인할 카카오계정 선택'(간편로그인) 화면이 먼저 뜨는데, 이때는
        아이디 입력칸이 없으므로 목록에서 USER_ID 계정을 눌러 넘긴다. 목록에 USER_ID 가
        없으면 '새로운 계정으로 로그인'을 누르지 않고 중단한다(USER_ID 로만 접속).

        이어서 카카오계정 로그인 → 2단계 인증이 뜨면 로그로 알리고 '이 기기에서 2차 인증 안 함'
        체크 + '확인' 후, 사람이 카카오톡 앱에서 승인할 때까지 대기(CAPTCHA_WAIT).
        (name 기반 셀렉터 사용 — loginId--1 등 --N 접미사 id는 자동생성이라 불안정)

        submit 이 씹혀 로그인 폼이 그대로 남는 경우가 잦아서, 폼이 다시 보이면
        (아이디/비번이 이미 채워진 화면 포함) '로그인' 버튼만 다시 눌러 넘긴다.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait

        driver.get(self.LOGIN_URL)              # → accounts.kakao.com/login 리디렉션
        try:            # 로그인 폼이든 계정 선택 목록이든 '로그인 화면'이 뜰 때까지 대기.
                        # 리디렉션이 늦어 URL 이 아직 center-pf 인 순간에 '로그인됨'으로
                        # 단정하면 계정을 누르지도 않고 성공 처리돼 버린다.
            WebDriverWait(driver, 20).until(
                lambda d: self._at_login_form(d) or self._account_rows(d))
        except Exception:
            pass

        if not (self._at_login_form(driver) or self._account_rows(driver)):
            return self.is_logged_in(driver)    # 로그인 화면이 안 뜸 = 세션 살아있음

        # ── 세션 만료 후 '로그인할 카카오계정 선택' 화면이면 내 계정을 눌러 넘긴다 ──
        if self._account_rows(driver):
            if self._clear_account_picker(driver):
                return True                     # 비번 없이 바로 통과(간편로그인)
            if self._account_rows(driver):      # 아직 목록 = 내 계정을 못 눌렀다
                print(f"[{self.name}] 계정 선택 화면을 넘기지 못했습니다.")
                return False

        id_in = self._login_form_id(driver)
        if id_in is not None or self._pw_field(driver) is not None:
            self._fill_credentials(driver, id_in)   # 비어 있는 칸만 채움

            # 간편로그인 정보 저장 체크(세션 유지)
            try:
                box = driver.find_element(By.CSS_SELECTOR, "input[name='saveSignedIn']")
                if not box.is_selected():
                    self._click_ico_check(driver)
            except Exception:
                pass

            self._submit_login(driver)
            if self._wait_logged_in(driver, 8):  # 2차 인증 없이 통과
                return True

        # ── 로그인 폼/계정 선택이 그대로 남은 경우: 다시 눌러 넘긴다 ──
        for i in range(1, self.LOGIN_SUBMIT_RETRY + 1):
            if self._account_rows(driver):      # 계정 선택 화면으로 되돌아옴
                print(f"[{self.name}] 계정 선택 화면 재출현 {i}/{self.LOGIN_SUBMIT_RETRY}")
                if self._clear_account_picker(driver):
                    return True
                if self._account_rows(driver):
                    print(f"[{self.name}] 계정 선택 화면을 넘기지 못했습니다.")
                    return False
                continue
            f = self._login_form_id(driver)
            if f is None and self._pw_field(driver) is None:
                break                           # 폼이 사라짐 = 2단계 인증 화면으로 넘어감
            err = self._login_error(driver)
            if err:                             # 비번 오류 등 — 더 눌러도 잠기기만 한다
                print(f"[{self.name}] 로그인 실패: {err}")
                return False
            acc = (f.get_attribute("value") or "").strip() if f is not None else ""
            print(f"[{self.name}] 로그인 폼 잔류 — '로그인' 재클릭 {i}/"
                  f"{self.LOGIN_SUBMIT_RETRY} (계정: {acc or self.USER_ID})")
            self._fill_credentials(driver, f)   # 비어 있을 때만 채움
            if not self._submit_login(driver):
                print(f"[{self.name}] 로그인 버튼을 찾지 못했습니다.")
                return False
            if self._wait_logged_in(driver, 8):
                return True

        if self.is_logged_in(driver):
            return True
        if self._at_login_form(driver):         # 아직도 폼 → 2FA 대기는 무의미
            print(f"[{self.name}] 로그인 폼을 넘기지 못했습니다: "
                  f"{self._login_error(driver) or '원인 불명'}")
            return False

        # ── 2단계 인증 화면 ──
        print(f"[{self.name}] ⚠️ 2단계 인증 필요 — 카카오톡 앱에서 승인해 주세요.")
        self._click_ico_check(driver)           # '이 기기에서 2차 인증 안 함' 체크
        print(f"[{self.name}] '2차 인증 안 함' 체크 + 확인 · 수동 인증(앱 승인) 대기중...")
        try:                                    # '확인' 클릭
            for b in driver.find_elements(
                    By.XPATH, "//button[normalize-space()='확인']"
                              "|//*[@type='submit'][normalize-space()='확인']"):
                if b.is_displayed():
                    b.click()
                    break
        except Exception:
            pass
        # 앱 승인 완료(=로그인화면 벗어남)까지 대기
        if not self._wait_logged_in(driver, self.CAPTCHA_WAIT):
            print(f"[{self.name}] 2단계 인증 미완료(시간초과). 다시 실행해 주세요.")
            return False
        return True

    @staticmethod
    def _blk_text(blk, sel: str) -> str:
        from selenium.webdriver.common.by import By
        e = blk.find_elements(By.CSS_SELECTOR, sel)
        return e[0].text.strip() if e else ""

    def _unread(self, blk) -> int:
        """span.num_round(안 읽은 개수). 없으면 0."""
        from selenium.webdriver.common.by import By
        e = blk.find_elements(By.CSS_SELECTOR, "span.num_round")
        if not e:
            return 0
        digits = "".join(c for c in e[0].text if c.isdigit())
        return int(digits) if digits else 0

    def scrape(self, driver) -> List[dict]:
        """채팅 목록에서 안 읽은(num_round>=1) 채팅만 추출. 실패 시 예외(시트 보호)."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        WebDriverWait(driver, 20).until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "a.link_chat")))
        time.sleep(1.5)

        out = []
        for blk in driver.find_elements(By.CSS_SELECTOR, "a.link_chat"):
            n = self._unread(blk)
            if n < 1:                           # 안 읽은 채팅만
                continue
            out.append({
                "name": self._blk_text(blk, "span.txt_name"),
                "info": self._blk_text(blk, "p.txt_info"),
                "date": self._blk_text(blk, "span.txt_date"),
                "num": n,
            })
        return out

    def to_sheet_rows(self, items: list) -> list:
        # B이름 C내용 D시각 E개수
        return [[it["name"], it["info"], it["date"], it["num"]] for it in items]

    def dashboard_rows(self, items: list) -> list:
        # [이름=카톡이름, 내용, 시각(상대표시), 연락처=없음]
        # 한 사람이 여러 톡을 보내도 '채팅방 1개 = 상담 1건'이므로 타일 숫자는 그대로 두고,
        # 안 읽은 메시지 수(span.num_round)는 내용 앞에 붙여 상세에서 보이게 한다.
        out = []
        for it in items:
            n = it.get("num", 0)
            info = f"{it['info']} ({n})" if n > 1 else it["info"]
            out.append([it["name"], info, it["date"], ""])
        return out

    def _scrape(self, driver):
        return []


def build_enabled_channels() -> List[BaseChannel]:
    return [cls() for key, cls in _REGISTRY.items() if ENABLED.get(key, True)]


def channel_meta() -> Dict[str, dict]:
    return {k: {"name": c.name, "color": c.color} for k, c in _REGISTRY.items()}


def channel_name(key: str) -> str:
    c = _REGISTRY.get(key)
    return c.name if c else key


# ══════════════════════════════════════════════════════════════
# [데모]  연동 전 테스트용 가짜 유입
# ══════════════════════════════════════════════════════════════
_NAMES = ["김민지", "이서연", "박지우", "최수빈", "정하은",
          "강예린", "조은서", "윤채원", "임지호", "한소희", "익명"]
_TREATMENTS = ["쌍꺼풀", "눈매교정", "코성형", "안면윤곽", "지방흡입",
               "가슴성형", "보톡스", "필러", "리프팅", "눈밑지방재배치"]
_MESSAGES = ["상담 가능한 시간 문의드려요.", "비용이 대략 어느 정도인가요?",
             "회복 기간은 얼마나 걸리나요?", "예약하고 싶습니다.",
             "상담만 먼저 받아볼 수 있을까요?", "견적 부탁드립니다.",
             "후기 보고 연락드려요!", "주말에도 상담 되나요?"]


def _generate_demo(channel: BaseChannel) -> List[Consultation]:
    n = random.choices([0, 1, 2], weights=[5, 3, 2])[0]
    if n == 0:
        return []
    base = int(datetime.now().timestamp() * 1000) % 100_000_000
    out = []
    for i in range(n):
        ext = str(base + i)
        out.append(Consultation(
            id=channel.make_id(ext),
            channel_key=channel.key,
            external_id=ext,
            customer_name=random.choice(_NAMES),
            contact="010-****-" + f"{random.randint(0, 9999):04d}",
            treatment=random.choice(_TREATMENTS),
            message=random.choice(_MESSAGES),
            received_at=datetime.now() - timedelta(minutes=random.randint(0, 120)),
        ))
    return out


# ══════════════════════════════════════════════════════════════
# [저장소]  공통 인터페이스 + 로컬(SQLite) / 구글시트(gspread)
# ══════════════════════════════════════════════════════════════
# 두 백엔드 모두 아래 dict 형태로 반환:
#   {id, channel_key, customer_name, contact, treatment, message,
#    received_at(iso str), status, confirmed(bool)}
class LocalStore:
    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with self._c() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS consultations (
                id TEXT PRIMARY KEY, channel_key TEXT, customer_name TEXT,
                contact TEXT, treatment TEXT, message TEXT, received_at TEXT,
                status TEXT, confirmed INTEGER DEFAULT 0, raw TEXT)""")

    def _c(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def name(self) -> str:
        return "로컬(SQLite)"

    def upsert(self, items: List[Consultation]) -> int:
        new = 0
        with self._c() as c:
            for it in items:
                if c.execute("SELECT 1 FROM consultations WHERE id=?", (it.id,)).fetchone():
                    continue
                c.execute("""INSERT INTO consultations
                    (id, channel_key, customer_name, contact, treatment, message,
                     received_at, status, confirmed, raw)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (it.id, it.channel_key, it.customer_name, it.contact,
                     it.treatment, it.message, it.received_at.isoformat(),
                     it.status, int(it.confirmed),
                     json.dumps(it.raw, ensure_ascii=False)))
                new += 1
        return new

    def fetch_all(self) -> List[dict]:
        with self._c() as c:
            rows = c.execute("SELECT * FROM consultations ORDER BY received_at DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["confirmed"] = bool(d["confirmed"])
            out.append(d)
        return out

    def mark_confirmed(self, ids: List[str]) -> None:
        if not ids:
            return
        with self._c() as c:
            c.executemany("UPDATE consultations SET confirmed=1 WHERE id=?",
                          [(i,) for i in ids])


class SheetStore:
    HEADER = ["id", "채널", "고객명", "연락처", "관심시술", "내용",
              "유입시각", "상태", "확인"]

    def __init__(self):
        self._ws = None

    def name(self) -> str:
        return "구글시트"

    def _worksheet(self):
        if self._ws is not None:
            return self._ws
        import gspread
        gc = gspread.service_account(filename=str(SERVICE_ACCOUNT_FILE))
        sh = gc.open_by_url(SHEET_URL)
        try:
            ws = sh.worksheet(SHEET_TAB)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(SHEET_TAB, rows=2000, cols=len(self.HEADER))
            ws.append_row(self.HEADER)
        if ws.row_values(1) != self.HEADER:
            ws.update("A1", [self.HEADER])
        self._ws = ws
        return ws

    def upsert(self, items: List[Consultation]) -> int:
        ws = self._worksheet()
        existing = set(ws.col_values(1)[1:])  # id 열(헤더 제외)
        rows = []
        for it in items:
            if it.id in existing:
                continue
            rows.append([it.id, channel_name(it.channel_key), it.customer_name,
                         it.contact, it.treatment, it.message,
                         it.received_at.isoformat(timespec="minutes"),
                         it.status, ""])
        if rows:
            ws.append_rows(rows, value_input_option="USER_ENTERED")
        return len(rows)

    def fetch_all(self) -> List[dict]:
        ws = self._worksheet()
        recs = ws.get_all_records(expected_headers=self.HEADER)
        out = []
        for r in recs:
            rid = str(r.get("id", "")).strip()
            if not rid:
                continue
            out.append({
                "id": rid,
                "channel_key": rid.split(":")[0],
                "customer_name": r.get("고객명", ""),
                "contact": r.get("연락처", ""),
                "treatment": r.get("관심시술", ""),
                "message": r.get("내용", ""),
                "received_at": str(r.get("유입시각", "")),
                "status": r.get("상태", "신규"),
                "confirmed": bool(str(r.get("확인", "")).strip()),
            })
        out.sort(key=lambda d: d["received_at"], reverse=True)
        return out

    def mark_confirmed(self, ids: List[str]) -> None:
        if not ids:
            return
        ws = self._worksheet()
        col_ids = ws.col_values(1)
        confirm_col = self.HEADER.index("확인") + 1
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        idset = set(ids)
        for i, v in enumerate(col_ids):
            if v in idset:
                ws.update_cell(i + 1, confirm_col, stamp)


def get_store():
    """SHEET_URL 이 있으면 구글시트, 없으면 로컬."""
    if SHEET_URL.strip():
        return SheetStore()
    return LocalStore()


# ══════════════════════════════════════════════════════════════
# [수집]  채널 순회
# ══════════════════════════════════════════════════════════════
def collect_all(store) -> int:
    """활성 채널을 순회하며 수집·저장. 새로 추가된 건수 반환."""
    total = 0
    for ch in build_enabled_channels():
        try:
            items = ch.collect()
            total += store.upsert(items)
        except Exception as e:               # 한 채널 실패가 전체를 막지 않게
            print(f"[{ch.name}] 수집 오류: {e}")
    return total


# ══════════════════════════════════════════════════════════════
# [대시보드 기록]  채널 스크랩 결과 → 채널별 구글시트 탭
# ══════════════════════════════════════════════════════════════
# 일시적 오류로 보고 재시도할 HTTP 코드(429=분당 쓰기 쿼터 초과)
_SHEET_RETRY_CODES = (429, 500, 502, 503)
_FORMATTED_ONCE: set = set()        # 서식/헤더를 이미 적용한 채널 key


def _api_code(e) -> Optional[int]:
    """gspread APIError 에서 HTTP 상태코드 추출(버전별 차이 흡수)."""
    v = getattr(e, "code", None)
    if isinstance(v, int):
        return v
    try:
        return int(e.response.status_code)
    except Exception:
        pass
    m = re.search(r"'code':\s*(\d+)", str(e))
    return int(m.group(1)) if m else None


def _sheet_call(fn, *a, tries: int = 6, **kw):
    """구글시트 API 호출 래퍼.
    429(분당 쓰기 60회 초과) 같은 일시적 오류는 지수 백오프로 재시도한다.
    ※ 이게 없으면 쿼터 한 번 초과에 수집기가 통째로 죽는다."""
    import gspread
    delay = 5.0
    for i in range(tries):
        try:
            return fn(*a, **kw)
        except gspread.exceptions.APIError as e:
            code = _api_code(e)
            if code not in _SHEET_RETRY_CODES or i == tries - 1:
                raise
            print(f"[시트] API {code} — {delay:.0f}초 후 재시도 ({i + 1}/{tries})")
            sys.stdout.flush()
            time.sleep(delay)
            delay = min(delay * 2, 90)


def _apply_format_once(ch: "BaseChannel", ws) -> None:
    """헤더·날짜서식은 매번 바뀌지 않으므로 프로세스당 1회만 적용한다.
    (채널당 1~3회 쓰기 × 6채널 = 사이클마다 최대 18회를 아낀다)"""
    if ch.key in _FORMATTED_ONCE:
        return
    try:
        _sheet_call(ch.after_write, ws)
        _FORMATTED_ONCE.add(ch.key)
    except Exception as e:
        print(f"[{ch.name}] 서식 적용 오류(무시하고 진행): {e}")


def write_channel_sheet(ch: "BaseChannel", items: list) -> int:
    """
    스크랩 성공분(items)만 시트에 기록.
    ※ 반드시 hub.collect(ch) 가 성공(예외 없이 반환)했을 때만 호출할 것.
      실패는 예외로 걸러져 이 함수까지 오지 않으므로, 여기서 clear 해도
      '실패로 시트가 비워지는' 사고가 안 난다. (items=[] 는 '신규상담 0건'
      이라는 정상 결과 → 시트를 정상적으로 비움)
    """
    import gspread
    gc = gspread.service_account(filename=str(SERVICE_ACCOUNT_FILE))
    ws = gc.open_by_url(GSHEET_URL).worksheet(ch.SHEET_TAB)

    rows = ch.to_sheet_rows(items)
    extra = ch.extra_sheet_data(items)          # 같은 탭의 부가 블록(예: 강남언니 Q&A)
    # 부가 블록은 이번에 쓸 게 없어도 '지난 사이클 잔재'를 남기면 안 되므로 항상 비운다.
    clears = [r for r in ([ch.SHEET_CLEAR] + list(ch.SHEET_CLEAR_EXTRA)) if r]
    if clears:
        _sheet_call(ws.batch_clear, clears)

    # 값 쓰기를 한 번의 batch_update 로 묶는다.
    # (예전엔 rows / A1 / 잔액셀을 따로 호출해 채널당 3~4회를 썼다 →
    #  6채널이면 분당 쓰기 60회 한도를 쉽게 넘겨 429 로 수집기가 죽었다)
    data = []
    if rows:
        data.append({"range": ch.SHEET_START, "values": rows})
    data.extend(extra)
    data.append({"range": "A1", "values": [[_now_stamp()]]})   # A2 수식은 안 건드림
    for cell, val in (ch.header_cells or {}).items():          # 잔액 등
        data.append({"range": cell, "values": [[val]]})
    _sheet_call(ws.batch_update, data, value_input_option="USER_ENTERED")

    _apply_format_once(ch, ws)      # 헤더·날짜서식은 프로세스당 1회만
    return len(rows) + sum(len(d.get("values") or []) for d in extra)


_KOR_DOW = ["월", "화", "수", "목", "금", "토", "일"]


def _now_stamp() -> str:
    """'2026-07-10 (금) 14:30:05 업데이트 완료 · PC이름' 형식의 현재시각 문자열.
    ※ PC 이름을 붙여야 여러 대에서 돌 때 '누가 썼는지'를 시트만 보고 알 수 있다."""
    dt = datetime.now()
    return (f"{dt:%Y-%m-%d} ({_KOR_DOW[dt.weekday()]}) {dt:%H:%M:%S} 업데이트 완료"
            f" · {socket.gethostname()}")


# ══════════════════════════════════════════════════════════════
# [실패 분류]  '실패' 한 단어 대신 사유를 시트·GUI·웹앱에 그대로 전달
# ══════════════════════════════════════════════════════════════
@dataclass
class CollectError:
    """수집 실패 1건.
    kind   = 짧은 사유. 시트 '미확인' 열에 들어가 화면에 그대로 표시된다.
    detail = 예외 원문(1줄). 시트 '비고' 열 + 툴팁용."""
    kind: str
    detail: str


# (판정 키워드, 표시 사유) — 위에서부터 먼저 맞는 규칙을 쓴다
_ERROR_RULES = [
    (("quota exceeded", "rate_limit_exceeded", "resource_exhausted"), "시트 쿼터 초과"),
    (("백오프",), "로그인 대기"),
    (("자동 로그인", "로그인 실패", "login"), "로그인 실패"),
    (("timeout", "timed out", "시간초과"), "시간 초과"),
    (("no such window", "target window already closed",
      "web view not found"), "탭 닫힘"),
    (("invalid session id", "session deleted", "not reachable",
      "disconnected", "session not created"), "브라우저 끊김"),
    (("unexpected alert", "alert"), "알림창 차단"),
    (("no such element", "unable to locate element",
      "stale element"), "추출 불가"),
]


def classify_error(exc: BaseException) -> CollectError:
    """예외 → (짧은 사유, 원문). 어디에 걸렸는지 화면만 보고 알 수 있게 한다."""
    msg = (str(exc) or "").strip() or exc.__class__.__name__
    low = msg.lower()
    kind = "수집 오류"
    for keys, label in _ERROR_RULES:
        if any(k in low for k in keys):
            kind = label
            break
    return CollectError(kind, f"{exc.__class__.__name__}: {msg.splitlines()[0][:180]}")


# ══════════════════════════════════════════════════════════════
# [다중 PC 잠금]  구글시트 하트비트로 '수집기는 전체에서 한 대만'
# ══════════════════════════════════════════════════════════════
# 포트 잠금(acquire_collector_lock)은 같은 PC 안에서만 유효하다.
# 여러 PC가 같은 시트를 쓰면 서로의 결과를 '실패'로 덮어쓰므로,
# 시트 자체에 소유자와 하트비트를 남겨 한 대만 돌게 한다.
#   H1 = 소유자 'PC이름|PID'      H2 = 하트비트 '2026-07-21 14:30:05'
# ※ write_dashboard 가 A1:F1000 만 지우므로 H 열은 안전하다.
# 집계 출력 탭. 옛 빌드/외부 프로그램이 하드코딩한 '대시보드' 를 피해 별도 탭에 쓴다.
# → 그쪽이 계속 '대시보드' 를 덮어써도 이 탭은 오염되지 않는다.
#   Code.gs 의 TAB 상수도 반드시 같은 값이어야 한다.
DASHBOARD_TAB = "대시보드2"

_LAST_WRITTEN_STAMP = ""        # write_dashboard 가 마지막으로 쓴 D1 값(외부 writer 탐지용)
SHEET_LOCK_OWNER_CELL = "H1"
SHEET_LOCK_BEAT_CELL = "H2"
# H3 = 지금 수집 중인 코드의 버전. 하트비트와 함께 갱신하므로 '실제로 돌고 있는
# 버전'이 그대로 남는다 → 웹앱(Index.html)이 이 값을 읽어 화면에 표시한다.
# (PC 마다 업데이트가 늦게 반영될 수 있어, 화면에서 바로 확인할 수단이 필요하다)
SHEET_LOCK_VER_CELL = "H3"
# 하트비트가 이보다 오래되면 그 PC 는 죽은 것으로 보고 잠금을 인계한다.
# 하트비트를 수집 사이클과 분리해 HEARTBEAT_SEC(45초)마다 독립적으로 찍으므로,
# 살아있는 수집기는 이 안에 반드시 여러 번 갱신한다 → 짧게 잡아도 오인 종료 없음.
# (예전엔 사이클당 1회만 찍어 간격이 최대 300초까지 벌어져 420초로 크게 잡아야 했다)
SHEET_LOCK_STALE_SEC = 180      # 3분. 다른 PC 종료 후 인계 대기시간(구 420초→180초)
HEARTBEAT_SEC = 45              # 하트비트 갱신 주기(초). 사이클과 무관하게 백그라운드로 찍음


def _machine_id() -> str:
    return f"{socket.gethostname()}|{os.getpid()}"


def _beat_age_sec(beat: str) -> Optional[int]:
    """하트비트 문자열 → 몇 초 전인지. 못 읽으면 None(=판단 불가)."""
    try:
        dt = datetime.strptime((beat or "").strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return int((datetime.now() - dt).total_seconds())


def _dash_ws(sh):
    """집계 탭을 얻는다. 없으면 만든다(첫 실행/탭 이름 변경 대비)."""
    import gspread
    try:
        return sh.worksheet(DASHBOARD_TAB)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=DASHBOARD_TAB, rows=1000, cols=12)


def _lock_ws():
    import gspread
    gc = gspread.service_account(filename=str(SERVICE_ACCOUNT_FILE))
    return _dash_ws(gc.open_by_url(GSHEET_URL))


def _read_lock(ws) -> tuple:
    rows = ws.get(f"{SHEET_LOCK_OWNER_CELL}:{SHEET_LOCK_BEAT_CELL}")
    rows = (list(rows) + [[], []])[:2]
    owner = (rows[0][0] if rows[0] else "").strip()
    beat = (rows[1][0] if rows[1] else "").strip()
    return owner, beat


def _write_lock(ws, owner: str) -> None:
    """H1 소유자 · H2 하트비트 · H3 실행 중인 버전을 한 번에 기록.
    (버전을 같이 남겨야 웹앱에서 '이 PC 가 어떤 버전으로 돌고 있는지' 보인다)"""
    _sheet_call(ws.update,
                values=[[owner], [f"{datetime.now():%Y-%m-%d %H:%M:%S}"],
                        [app_version()]],
                range_name=f"{SHEET_LOCK_OWNER_CELL}:{SHEET_LOCK_VER_CELL}",
                value_input_option="RAW")


def sheet_lock_blocker() -> Optional[str]:
    """다른 PC가 잠금을 쥐고 있으면 그 소유자 문자열, 아니면 None.
    같은 PC 이름의 잠금은 '죽은 흔적'이므로 막지 않는다 —
    포트 잠금(9765)이 이 PC 안에 수집기가 하나뿐임을 이미 보장한다.
    (GUI 가 시작 전에 확인하는 용도로도 쓴다)"""
    try:
        owner, beat = _read_lock(_lock_ws())
    except Exception:
        return None                     # 시트를 못 읽으면 막지 않음(수집기가 알아서 실패)
    me = _machine_id()
    if not owner or owner == me:
        return None
    if owner.split("|")[0] == socket.gethostname():
        return None                     # 같은 PC의 잔여 잠금 → 회수 대상
    age = _beat_age_sec(beat)
    if age is None or age >= SHEET_LOCK_STALE_SEC:
        return None                     # 하트비트 끊김 → 그 PC 는 죽었다고 보고 인계
    return f"{owner} (하트비트 {age}초 전)"


def claim_sheet_lock() -> tuple:
    """수집기 시작 시 1회. (획득여부, 소유자문자열)."""
    blocker = sheet_lock_blocker()
    if blocker:
        return False, blocker
    ws = _lock_ws()
    me = _machine_id()
    _write_lock(ws, me)
    time.sleep(3)                       # 동시 진입 시 늦게 쓴 쪽이 이기도록 재확인
    owner2, _ = _read_lock(ws)
    return (owner2 == me), owner2


def refresh_sheet_lock() -> tuple:
    """매 사이클 하트비트 갱신. 다른 PC가 가져갔으면 (False, 소유자)."""
    ws = _lock_ws()
    owner, _ = _read_lock(ws)
    me = _machine_id()
    if owner and owner != me:
        return False, owner
    _write_lock(ws, me)
    return True, me


def release_sheet_lock() -> None:
    """이 PC 가 쥔 잠금을 반납한다(다른 PC 잠금은 건드리지 않음).
    ※ PID 가 아니라 PC 이름으로 비교한다 — GUI 가 수집기를 terminate() 로 끄면
      자식의 finally 가 안 돌아 잠금이 남는데, 그걸 GUI 가 대신 반납해야
      다른 PC 가 7분(SHEET_LOCK_STALE_SEC)을 기다리지 않는다."""
    try:
        ws = _lock_ws()
        owner, _ = _read_lock(ws)
        if owner and owner.split("|")[0] == socket.gethostname():
            ws.update(values=[[""], [""]],
                      range_name=f"{SHEET_LOCK_OWNER_CELL}:{SHEET_LOCK_BEAT_CELL}",
                      value_input_option="RAW")
    except Exception:
        pass


def beat_sheet_lock() -> bool:
    """하트비트(H1 소유자 + H2 시각)를 1회 갱신. 내가 소유자일 때만 쓴다.
    다른 PC 가 소유자로 바뀌었으면 False(=인계됨) → 호출부가 하트비트를 멈춘다."""
    ws = _lock_ws()
    owner, _ = _read_lock(ws)
    me = _machine_id()
    # 남이 가져갔으면(같은 PC 이름의 잔여 잠금은 내 것으로 회수) 중단
    if owner and owner != me and owner.split("|")[0] != socket.gethostname():
        return False
    _write_lock(ws, me)
    return True


def start_heartbeat() -> threading.Event:
    """백그라운드에서 HEARTBEAT_SEC 마다 하트비트를 갱신하는 스레드 시작.

    수집 순회(one_cycle)가 오래 걸려도 하트비트는 이 스레드가 계속 찍으므로,
    다른 PC 는 이 수집기가 '살아있음'을 정확히 안다 → SHEET_LOCK_STALE_SEC 를
    짧게(3분) 잡아도 라이브 수집기가 오인 종료되지 않는다.
    반환된 Event 의 .set() 을 호출하면 다음 주기에 스레드가 멈춘다."""
    stop = threading.Event()

    def loop():
        # stop.wait(t): t초 대기하다 stop 이 set 되면 True 반환 → 즉시 종료
        while not stop.wait(HEARTBEAT_SEC):
            try:
                if not beat_sheet_lock():
                    print("[하트비트] 잠금이 다른 PC로 인계됨 → 하트비트 중단")
                    sys.stdout.flush()
                    return
            except Exception:
                pass                    # 일시적 시트 오류는 무시(다음 주기 재시도)

    threading.Thread(target=loop, daemon=True).start()
    return stop


# 대시보드에 표시할 채널 순서
DASHBOARD_ORDER = ["gangnamunni", "babitalk", "naver_map",
                   "online_consult", "online_booking", "kakaotalk"]


INSTA_TAB = "인스타"


def read_instagram_rows(sh) -> list:
    """'인스타' 탭(IMPORTRANGE로 채워짐)에서 미연락 건만 읽어 대시보드 상세행으로.
    구조(2행 헤더, 3행부터): A=No B=이름 C=신청시각 D=원하는부위 E=연락처
                          F=연락여부(FALSE/TRUE) G=날짜 H=채널
    반환: [[이름, 신청항목, 시각(G열), 연락처], ...]  (F=FALSE & 이름 있음)
    """
    ws = sh.worksheet(INSTA_TAB)
    rows = []
    for r in ws.get("A3:H1000"):
        r = (list(r) + [""] * 8)[:8]          # 뒤쪽 빈 셀 패딩
        name = (r[1] or "").strip()           # B
        contacted = (r[5] or "").strip().upper()  # F
        if not name or contacted != "FALSE":  # 빈 행·이미 연락(TRUE) 제외
            continue
        rows.append([name,                    # 이름
                     (r[3] or "").strip(),     # D 신청항목
                     _to_sheet_date((r[6] or "").strip()),  # G 날짜
                     (r[4] or "").strip()])    # E 연락처
    return rows


# ══════════════════════════════════════════════════════════════
# [텔레그램 알림]  새 상담 / 수집 실패를 push. 이미 알린 건은 파일로 기억.
# ══════════════════════════════════════════════════════════════
NOTIFY_PATH = BASE_DIR / "notified.json"      # 이미 알린 지문 저장(중복 알림 방지)
_NOTIFY_KEEP_SEC = 7 * 24 * 3600              # 이 기간 지난 지문은 정리(파일 비대 방지)


def telegram_enabled() -> bool:
    return bool(TELEGRAM_TOKEN.strip() and TELEGRAM_CHAT_ID.strip())


def send_telegram(text: str) -> bool:
    """텔레그램 메시지 전송. 실패해도 수집을 막지 않도록 예외를 삼킨다."""
    if not telegram_enabled():
        return False
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, timeout=10, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        })
        if r.status_code != 200:
            print(f"[텔레그램] 전송 실패 {r.status_code}: {r.text[:150]}")
            return False
        return True
    except Exception as e:
        print(f"[텔레그램] 전송 오류: {e}")
        return False


def _load_notify_state() -> dict:
    """{'seen': {지문: 마지막본시각}, 'fail': {채널: 마지막알림시각}}"""
    try:
        d = json.loads(NOTIFY_PATH.read_text(encoding="utf-8"))
        d.setdefault("seen", {})
        d.setdefault("fail", {})
        d.setdefault("prev_unread", 0)
        return d
    except Exception:
        return {"seen": {}, "fail": {}, "prev_unread": 0, "_fresh": True}   # 파일 없음 = 첫 실행


def _save_notify_state(state: dict) -> None:
    now = time.time()
    # 오래된 지문 정리
    state["seen"] = {k: v for k, v in state.get("seen", {}).items()
                     if now - v < _NOTIFY_KEEP_SEC}
    state.pop("_fresh", None)
    try:
        NOTIFY_PATH.write_text(json.dumps(state, ensure_ascii=False),
                               encoding="utf-8")
    except Exception as e:
        print(f"[텔레그램] 상태 저장 오류: {e}")


def _esc_html(s: str) -> str:
    """텔레그램 parse_mode=HTML 로 보낼 본문 escape.
    예외 원문에 '<' 가 섞이면 메시지 전체가 거부돼 알림이 통째로 사라진다."""
    return (str(s or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _fmt_dur(sec: float) -> str:
    """경과 초 → '3분' / '1시간 20분' / '1일 3시간'."""
    m = int(max(0, sec) // 60)
    if m < 1:
        return "1분 미만"
    d, h, mm = m // 1440, (m % 1440) // 60, m % 60
    p = []
    if d:
        p.append(f"{d}일")
    if h:
        p.append(f"{h}시간")
    if mm or not p:
        p.append(f"{mm}분")
    return " ".join(p)


def _fail_rec(v) -> dict:
    """state['fail'] 값 정규화 → {first, last, reason}.
    first  = 처음 실패를 알린 시각(복구까지 걸린 시간 계산용)
    last   = 마지막으로 알린 시각(재알림 간격용)
    ※ 옛 형식(숫자 = 마지막 알림 시각)도 그대로 받아들인다. 버전이 섞여 돌아도
      notified.json 때문에 알림이 죽으면 안 된다."""
    if isinstance(v, dict):
        def num(k):
            try:
                return float(v.get(k) or 0)
            except (TypeError, ValueError):
                return 0.0
        return {"first": num("first"), "last": num("last"),
                "reason": str(v.get("reason") or "")}
    try:
        t = float(v)
    except (TypeError, ValueError):
        t = 0.0
    return {"first": t, "last": t, "reason": ""}


def _row_fingerprint(row: list) -> str:
    """상세행 [채널, 이름, 내용, 시각, 연락처] → 안정적 지문.
    시각은 카카오톡 상대표시('오전 11:10')처럼 바뀌므로 제외한다."""
    ch, name, info, _t, contact = (list(row) + [""] * 5)[:5]
    return "|".join(str(x).strip() for x in (ch, name, contact, info))


def _collector_footer() -> str:
    """알림 하단에 붙일 '🖥 PC · 작동 중 · N분 전' 문구.
    시트 D1 스탬프에서 수집 PC와 경과 시간을 뽑아 대시보드와 같은 표기를 쓴다."""
    try:
        stamp = read_dashboard_data().get("updated", "")
    except Exception:
        stamp = ""
    host = socket.gethostname()
    if " · " in stamp:                      # '… 완료 · DESKTOP-QDP5L4A'
        host = stamp.rsplit(" · ", 1)[1].strip() or host
    mins = _stamp_minutes_ago(stamp)
    when = "방금" if mins == 0 else (f"{mins}분 전" if mins is not None else "시각 미상")
    return f"\n\n<i>🖥 {host} · 작동 중 · {when}</i>"


def notify_new_and_failures(detail_rows: list, failed: list,
                            total_unread: int = None) -> None:
    """detail_rows: [[채널,이름,내용,시각,연락처], ...] (헤더 제외)
       failed: [(채널명, 사유), ...]
       total_unread: 전체 미확인 건수(메시지에 표기). None 이면 detail_rows 수로 대체.
    새 상담 → 건별 알림, 수집 실패 → 채널별 재알림 간격 두고 알림."""
    if total_unread is None:
        total_unread = len(detail_rows)
    if not telegram_enabled():
        return
    state = _load_notify_state()
    seen = state["seen"]
    fresh = state.get("_fresh", False)      # 첫 실행이면 폭탄 방지: 알리지 않고 학습만
    now = time.time()

    # ── 새 상담 ──
    new_rows = []
    for row in detail_rows:
        fp = _row_fingerprint(row)
        if fp not in seen:
            new_rows.append(row)
        seen[fp] = now                      # 봤으므로 갱신(재알림 방지)

    if new_rows and not fresh:
        # 채널별로 묶어서 한 메시지에(알림 폭탄 방지)
        from collections import defaultdict
        by_ch = defaultdict(list)
        for ch, name, info, t, contact in (
                (r + [""] * 5)[:5] for r in new_rows):
            by_ch[ch].append((name, info, t, contact))
        # 헤더: 새 상담 N건 + 전체 미확인 M건
        lines = [f"🔔 <b>새 상담 {len(new_rows)}건</b> · 전체 미확인 {total_unread}건"]
        for ch, rows in by_ch.items():
            lines.append(f"\n<b>[{ch}]</b> {len(rows)}건")
            for name, info, t, contact in rows[:10]:   # 채널당 최대 10건 표기
                c = f"\n   📞 {contact}" if contact else ""
                tt = f" · {t}" if t else ""
                lines.append(f"• <b>{name}</b>{tt}\n   💬 {info}{c}")
            if len(rows) > 10:
                lines.append(f"… 외 {len(rows) - 10}건")
        send_telegram("\n".join(lines) + _collector_footer())

    # ── 모두 처리 완료(미확인이 있다가 0건이 된 순간에만 1회) ──
    #    계속 0건이면 조용. prev_unread 로 전환 시점만 잡는다.
    #    ※ 수집 실패로 0이 된 걸 '완료'로 오인하지 않도록 실패 없을 때만 보낸다.
    prev_unread = state.get("prev_unread", 0)
    if not fresh and prev_unread > 0 and total_unread == 0 and not failed:
        send_telegram("✅ <b>미확인 상담 모두 처리 완료</b>\n"
                      "대기 중인 상담이 없습니다." + _collector_footer())
    # 실패로 0이 된 경우엔 prev_unread 를 덮지 않는다(실패 해소 뒤 재판정 위해).
    if not failed:
        state["prev_unread"] = total_unread

    # ── 수집 실패(재알림 간격 적용) ──
    if not fresh:
        for ch_name, reason in failed:
            rec = _fail_rec(state["fail"].get(ch_name))
            if now - rec["last"] >= TELEGRAM_FAIL_RENOTIFY_MIN * 60:
                if send_telegram(f"⚠️ <b>{ch_name} 수집 실패</b>\n{_esc_html(reason)}"
                                 + _collector_footer()):
                    rec["last"] = now
                    rec["first"] = rec["first"] or now   # 첫 실패 시각은 유지
                    rec["reason"] = reason
                    state["fail"][ch_name] = rec

    # ── 실패가 해소된 채널 → 정상화 알림(1회) + 재알림 타이머 초기화 ──
    #    '실패 알림이 실제로 나갔던' 채널만 알린다. 알린 적 없는 일시 실패까지
    #    복구를 알리면 조용히 지나갔어야 할 건에 알림이 붙는다.
    #    브라우저가 끊기면 여러 채널이 한꺼번에 죽으므로 한 메시지로 묶는다.
    failed_names = {n for n, _ in failed}
    recovered = []
    for n in list(state["fail"]):
        if n in failed_names:
            continue
        rec = _fail_rec(state["fail"].pop(n))
        if rec["last"]:
            recovered.append((n, rec))

    if recovered and not fresh:
        if len(recovered) == 1:
            name, rec = recovered[0]
            body = (f"✅ <b>{name} 수집 정상화</b>\n"
                    f"{_fmt_dur(now - rec['first'])} 만에 복구됐습니다.")
            if rec["reason"]:
                body += f"\n<i>직전 오류: {_esc_html(rec['reason'][:120])}</i>"
        else:
            lines = [f"✅ <b>수집 정상화</b> · {len(recovered)}개 채널"]
            for name, rec in recovered:
                lines.append(f"• <b>{name}</b> · {_fmt_dur(now - rec['first'])} 만에 복구")
            body = "\n".join(lines)
        send_telegram(body + _collector_footer())

    _save_notify_state(state)


def write_dashboard(results: list) -> None:
    """
    results: [(channel, items|None)]  (None=이번 사이클 수집 실패)
    '대시보드' 탭에 요약(채널별 미확인수+잔액) + 상세(통합목록) + 업데이트 시각 기록.
    실패한 채널은 시트의 기존 값(미확인수/잔액)을 유지한다.
    """
    import gspread
    gc = gspread.service_account(filename=str(SERVICE_ACCOUNT_FILE))
    sh = gc.open_by_url(GSHEET_URL)
    ws = _dash_ws(sh)

    by_key = {ch.key: (ch, items) for ch, items in results}

    # 실패 채널의 기존 값 유지를 위해 현재 요약 읽기 {채널명: [미확인, 잔액, 비고]}
    prev = {}
    try:
        for row in ws.get("A4:D12"):
            if row and row[0]:
                prev[row[0]] = (row[1:] + ["", "", ""])[:3]
    except Exception:
        pass

    summary = [["🔔 상담 통합 대시보드", "", "", _now_stamp()],
               [],
               ["채널", "미확인", "잔액", "비고"]]
    detail = [["채널", "이름", "내용", "시각", "연락처"]]
    failed = []                             # 텔레그램 실패 알림용 [(채널명, 사유)]
    total = 0

    for key in DASHBOARD_ORDER:
        pair = by_key.get(key)
        if not pair:
            continue
        ch, items = pair
        # 수집 실패 → 사유를 그대로 기록(잔액은 이전값 유지, 비고에 예외 원문)
        if isinstance(items, CollectError):
            p = prev.get(ch.name, ["", "", ""])
            summary.append([ch.name, items.kind, p[1], items.detail])
            failed.append((ch.name, f"{items.kind} · {items.detail}"))
            continue
        if items is None:                       # 사유 미상(구버전 호출 호환)
            p = prev.get(ch.name, ["", "", ""])
            summary.append([ch.name, "실패", p[1], p[2]])
            continue
        cnt = len(items)
        total += cnt
        summary.append([ch.name, cnt,
                        ch.header_cells.get("E1", ""),
                        ch.header_cells.get("F1", "")])
        for r in ch.dashboard_rows(items):
            detail.append([ch.name] + list(r))

    # 인스타: 스크랩 없이 '인스타' 탭(IMPORTRANGE) 데이터만 읽어 합침
    #   B=이름 D=신청항목 E=연락처 F=연락여부 G=날짜, F가 FALSE(미연락)인 행만
    try:
        insta = read_instagram_rows(sh)
        summary.append(["인스타", len(insta), "", ""])
        total += len(insta)
        for r in insta:
            detail.append(["인스타"] + list(r))
    except Exception as e:
        err = classify_error(e)
        print(f"[인스타] 시트 읽기 오류: {err.detail}")
        p = prev.get("인스타", ["", "", ""])
        summary.append(["인스타", err.kind, p[1], err.detail])
        failed.append(("인스타", f"{err.kind} · {err.detail}"))

    summary.append(["합계", total, "", ""])

    # 텔레그램 알림(새 상담·수집 실패). 시트 기록과 무관하게 예외를 삼킨다.
    try:
        notify_new_and_failures(detail[1:], failed, total_unread=total)   # 헤더 제외
    except Exception as e:
        print(f"[텔레그램] 알림 처리 오류: {e}")

    # 외부 writer 탐지: 지난번 내가 쓴 스탬프가 그대로 남아있어야 정상이다.
    # 다르면 이 잠금을 모르는 다른 프로그램(옛 빌드 등)이 덮어쓰고 있다는 뜻.
    global _LAST_WRITTEN_STAMP
    if _LAST_WRITTEN_STAMP:
        try:
            cur = (ws.acell("D1").value or "").strip()
            if cur and cur != _LAST_WRITTEN_STAMP:
                print(f"[경고] 외부 writer 감지 — 내가 쓴 값이 바뀌었습니다.\n"
                      f"       내가 쓴 값 : {_LAST_WRITTEN_STAMP}\n"
                      f"       현재 값    : {cur}\n"
                      f"       → 다른 PC/옛 빌드가 같은 시트를 쓰고 있습니다.")
        except Exception:
            pass

    _sheet_call(ws.batch_clear, ["A1:F1000"])
    _sheet_call(ws.update, values=summary, range_name="A1",
                value_input_option="USER_ENTERED")
    _LAST_WRITTEN_STAMP = summary[0][3]
    # 상세는 RAW로 기록: 카카오톡 '오전 11:10' 같은 상대시각이 시트에서
    # 시간값(1899-12-30 …)으로 자동변환되지 않고 문자 그대로 남게 한다.
    _sheet_call(ws.update, values=detail, range_name="A" + str(len(summary) + 2),
                value_input_option="RAW")


# ══════════════════════════════════════════════════════════════
# [연동 상태]  각 구글시트 탭의 최종 업데이트 시각 읽기(모니터링용)
# ══════════════════════════════════════════════════════════════
# (표시이름, 시트탭이름, 스탬프가 있는 셀)
#   채널 탭   → A1 에 '… 업데이트 완료' (write_channel_sheet)
#   대시보드 탭 → D1 에 요약 업데이트 시각 (write_dashboard)
SHEET_STATUS_TABS = [
    ("대시보드", DASHBOARD_TAB, "D1"),
    ("강남언니", "강남언니", "A1"),
    ("바비톡", "바비톡", "A1"),
    ("네이버지도", "네이버지도", "A1"),
    ("온라인상담", "온라인상담", "A1"),
    ("온라인예약", "온라인예약", "A1"),
    ("카카오톡", "카카오톡", "A1"),
]

# 마지막 업데이트가 이 시간(분)을 넘으면 '지연/중단'으로 간주
FRESH_MIN = 15          # 이내 → 🟢 정상
STALE_MIN = 60          # 이내 → 🟡 지연 / 넘으면 🔴 중단 의심


def _stamp_minutes_ago(stamp: str) -> Optional[int]:
    """'2026-07-10 (금) 14:30:05 …' → 지금으로부터 몇 분 전인지. 못 읽으면 None."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2}).*?(\d{1,2}):(\d{2})(?::(\d{2}))?",
                  stamp or "")
    if not m:
        return None
    try:
        dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                      int(m.group(4)), int(m.group(5)), int(m.group(6) or 0))
    except ValueError:
        return None
    return max(0, int((datetime.now() - dt).total_seconds() // 60))


def read_sheet_status() -> dict:
    """
    구글시트에 연결해 각 탭의 최종 업데이트 스탬프를 '한 번의 API 호출'로 읽는다.
    반환: {"ok": bool, "error": str, "rows": [(표시이름, 스탬프문자열|""), ...]}
    """
    try:
        import gspread
        gc = gspread.service_account(filename=str(SERVICE_ACCOUNT_FILE))
        sh = gc.open_by_url(GSHEET_URL)
        ranges = [f"'{tab}'!{cell}" for _, tab, cell in SHEET_STATUS_TABS]
        resp = sh.values_batch_get(ranges)
        vrs = resp.get("valueRanges", [])
        rows = []
        for (label, _, _), vr in zip(SHEET_STATUS_TABS, vrs):
            vals = vr.get("values", [])
            stamp = vals[0][0] if (vals and vals[0]) else ""
            rows.append((label, stamp))
        return {"ok": True, "error": "", "rows": rows}
    except Exception as e:
        return {"ok": False, "error": str(e), "rows": []}


def read_dashboard_data() -> dict:
    """
    '대시보드' 탭을 읽어 웹앱(getDashboard)과 동일한 형태로 반환.
      {"ok", "error", "updated",
       "channels": [{"name","count","balance","note"}],
       "items":    [[채널, 이름, 내용, 시각, 연락처], ...]}
    count 는 문자열('실패' 가능). 파싱은 화면단에서 처리.
    """
    try:
        import gspread
        gc = gspread.service_account(filename=str(SERVICE_ACCOUNT_FILE))
        sh = gc.open_by_url(GSHEET_URL)
        v = _dash_ws(sh).get_all_values()               # 1 API 호출
    except Exception as e:
        return {"ok": False, "error": str(e), "updated": "",
                "channels": [], "items": []}

    def cell(row, j):
        return row[j].strip() if len(row) > j else ""

    updated = cell(v[0], 3) if v else ""     # D1

    # 요약: '채널'+'미확인' 헤더 다음 ~ '합계'/빈 행 전까지
    channels, ss = [], -1
    for i, row in enumerate(v):
        if cell(row, 0) == "채널" and cell(row, 1) == "미확인":
            ss = i + 1
            break
    if ss >= 0:
        for row in v[ss:]:
            name = cell(row, 0)
            if not name or name == "합계":
                break
            channels.append({"name": name, "count": cell(row, 1),
                             "balance": cell(row, 2), "note": cell(row, 3)})

    # 상세: '채널'+'이름' 헤더 다음 ~ 빈 행 전까지
    items, ds = [], -1
    for i, row in enumerate(v):
        if cell(row, 0) == "채널" and cell(row, 1) == "이름":
            ds = i + 1
            break
    if ds >= 0:
        for row in v[ds:]:
            if not cell(row, 0):
                break
            items.append([cell(row, j) for j in range(5)])

    return {"ok": True, "error": "", "updated": updated,
            "channels": channels, "items": items}


# ══════════════════════════════════════════════════════════════
# [화면]  Tkinter GUI  (구글시트 '대시보드' 탭을 그대로 표시 = 웹과 동일)
# ══════════════════════════════════════════════════════════════
# 대시보드 상세 컬럼(구글시트 '대시보드' 탭 상세블록과 동일): 채널·이름·내용·시각·연락처
COLS = [
    ("channel", "채널", 100),
    ("name", "이름", 130),
    ("message", "내용", 320),
    ("time", "시각", 150),
    ("contact", "연락처", 130),
]

# 대시보드에 표시할 채널 순서 + 색(웹 Index.html 과 동일)
DASH_CHANNELS = [
    ("강남언니", "#EC4899"),
    ("바비톡", "#8B5CF6"),
    ("네이버지도", "#03C75A"),
    ("온라인상담", "#1E90FF"),
    ("온라인예약", "#FF9500"),
    ("카카오톡", "#E0AC00"),
    ("인스타", "#C13584"),
]
DASH_COLOR = dict(DASH_CHANNELS)

# 연동상태 행과 카드 행의 세로줄을 맞추기 위한 공통 여백(둘 다 이 값을 쓴다)
GRID_PAD = 16          # 바깥 좌우 여백
CELL_PAD = 3           # 셀 사이 간격
# 스크랩 대상 채널(= 시트 탭 스탬프가 있는 것). '대시보드'는 채널이 아니라 제외.
STATUS_LABELS = {label for label, _tab, _cell in SHEET_STATUS_TABS} - {"대시보드"}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("상담 통합 대시보드")
        self.geometry("1200x740")
        self.configure(bg="#F4F5F7")
        self.meta = channel_meta()
        # 화면 데이터 = 구글시트 '대시보드' 탭 (웹앱과 동일 소스)
        self.dash = {"updated": "", "channels": [], "items": []}
        self.card_widgets: Dict[str, Dict[str, tk.Widget]] = {}
        self._loading = False
        self._fail_detail: Dict[str, str] = {}   # 채널명 → 실패 예외 원문(비고 열)
        self.collector_proc = None         # run_dashboard.py 백그라운드 프로세스

        self.auto_var = tk.BooleanVar(value=False)  # 기본: 중지 — '수집기 켜기'로 수동 시작
        self.channel_var = tk.StringVar(value="전체")
        self.search_var = tk.StringVar()

        self._sheet_status_job = None
        self._dash_job = None

        self._build_styles()
        self._build_header()
        self._build_sheet_status()         # 구글시트 연동 상태 패널
        self._build_cards()
        self._build_toolbar()
        self._build_table()
        self._build_statusbar()

        self.protocol("WM_DELETE_WINDOW", self._on_close)  # 종료 시 수집기도 정리
        self.after(300, self.refresh_dashboard)       # 대시보드 시트 읽어 화면 채움
        self.after(600, self.refresh_sheet_status)    # 연동상태(각 시트 시각) 조회
        self.after(1200, self._boot_collector)        # 자동수집이면 수집기 시작
        self.after(2500, self._poll_collector)        # 수집기 상태 표시 루프

    # ── 스타일 ───────────────────────────────────────────────
    def _build_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", font=FONT, rowheight=28,
                        background="white", fieldbackground="white")
        style.configure("Treeview.Heading", font=FONT_BOLD)

    def _build_header(self):
        h = tk.Frame(self, bg="#F4F5F7")
        h.pack(fill="x", padx=16, pady=(14, 6))
        tk.Label(h, text="🏥 상담 통합 대시보드", font=FONT_TITLE,
                 bg="#F4F5F7", fg="#222").pack(side="left")
        tk.Label(h, text=f"v{app_version()}", font=("맑은 고딕", 9),
                 bg="#F4F5F7", fg="#868E96").pack(side="left", padx=(8, 0), pady=(8, 0))
        self.alert = tk.Label(h, text="", font=FONT_BOLD, bg="#F4F5F7", padx=12,
                              cursor="hand2")
        self.alert.pack(side="right")
        self.alert.bind("<Button-1>", lambda e: self._show_fail_detail())

    # ── 구글시트 연동 상태 패널 ──────────────────────────────
    def _build_sheet_status(self):
        panel = tk.Frame(self, bg="white",
                         highlightbackground="#E1E4E8", highlightthickness=1)
        panel.pack(fill="x", padx=GRID_PAD, pady=(4, 2))

        # 상단 줄: 제목 · 연결상태 · 대시보드 갱신시각 · 새로고침
        top = tk.Frame(panel, bg="white")
        top.pack(fill="x", padx=GRID_PAD - 4, pady=(8, 4))
        tk.Label(top, text="🔗 구글시트 연동 상태", font=FONT_BOLD,
                 bg="white", fg="#333").pack(side="left")
        self.conn_lbl = tk.Label(top, text="확인 중…", font=FONT,
                                 bg="white", fg="#868E96")
        self.conn_lbl.pack(side="left", padx=(10, 0))
        # '대시보드' 탭은 채널이 아니라 전체 하트비트 → 아래 격자 대신 여기 표기
        self.dash_stamp_lbl = tk.Label(top, text="", font=("맑은 고딕", 9),
                                       bg="white", fg="#868E96")
        self.dash_stamp_lbl.pack(side="left", padx=(12, 0))
        tk.Button(top, text="🔄 상태 새로고침", font=("맑은 고딕", 9), relief="flat",
                  bg="#E9ECEF", padx=8, pady=1,
                  command=self.refresh_sheet_status).pack(side="right")
        self.sheet_updated_lbl = tk.Label(top, text="", font=("맑은 고딕", 9),
                                          bg="white", fg="#868E96")
        self.sheet_updated_lbl.pack(side="right", padx=(0, 10))

        # 채널별 상태(이름 / 최종 업데이트 / 경과·상태).
        # 열 구성을 DASH_CHANNELS 로 맞춰 아래 카드 행과 세로줄이 정렬되게 한다.
        grid = tk.Frame(panel, bg="white")
        grid.pack(fill="x", padx=GRID_PAD - 4, pady=(0, 10))
        self.sheet_cells: Dict[str, Dict[str, tk.Widget]] = {}
        for i, (label, _color) in enumerate(DASH_CHANNELS):
            cell = tk.Frame(grid, bg="#F8F9FA",
                            highlightbackground="#E9ECEF", highlightthickness=1)
            cell.grid(row=0, column=i, sticky="nsew", padx=CELL_PAD, pady=2)
            grid.columnconfigure(i, weight=1, uniform="ch")
            tk.Label(cell, text=label, font=("맑은 고딕", 9, "bold"),
                     bg="#F8F9FA", fg="#495057").pack(anchor="w", padx=8, pady=(5, 0))
            time_lbl = tk.Label(cell, text="—", font=("맑은 고딕", 9),
                                bg="#F8F9FA", fg="#212529")
            time_lbl.pack(anchor="w", padx=8)
            state_lbl = tk.Label(cell, text="조회 전", font=("맑은 고딕", 9, "bold"),
                                 bg="#F8F9FA", fg="#868E96")
            state_lbl.pack(anchor="w", padx=8, pady=(0, 5))
            if label in STATUS_LABELS:
                self.sheet_cells[label] = {"time": time_lbl, "state": state_lbl}
            else:                       # 인스타: 스크랩 대상 아님(IMPORTRANGE 집계)
                time_lbl.config(text="—")
                state_lbl.config(text="시트 연동", fg="#868E96")

    def refresh_sheet_status(self):
        """백그라운드로 시트 상태를 읽어와 패널 갱신(네트워크 → 스레드)."""
        if self._sheet_status_job:
            self.after_cancel(self._sheet_status_job)
            self._sheet_status_job = None
        self.conn_lbl.config(text="확인 중…", fg="#868E96")
        threading.Thread(target=self._sheet_status_worker, daemon=True).start()

    def _sheet_status_worker(self):
        result = read_sheet_status()
        self.after(0, lambda: self._apply_sheet_status(result))

    def _apply_sheet_status(self, result: dict):
        if not result["ok"]:
            self.conn_lbl.config(text="✕ 연결 오류", fg="#E03131")
            self.sheet_updated_lbl.config(text=result["error"][:60])
            self.dash_stamp_lbl.config(text="", fg="#868E96")
            for w in self.sheet_cells.values():
                w["time"].config(text="—")
                w["state"].config(text="확인 불가", fg="#868E96")
        else:
            self.conn_lbl.config(text="● 연결됨", fg="#2F9E44")
            self.sheet_updated_lbl.config(
                text=f"조회 {datetime.now():%H:%M:%S}")
            stamps = dict(result["rows"])

            # 대시보드(전체 하트비트) — 격자 대신 제목 옆에 표기
            dmins = _stamp_minutes_ago(stamps.get("대시보드", ""))
            if dmins is None:
                self.dash_stamp_lbl.config(text="· 대시보드 기록 없음", fg="#868E96")
            elif dmins <= FRESH_MIN:
                self.dash_stamp_lbl.config(text=f"· 대시보드 🟢 {dmins}분 전", fg="#2F9E44")
            elif dmins <= STALE_MIN:
                self.dash_stamp_lbl.config(text=f"· 대시보드 🟡 {dmins}분 전", fg="#F08C00")
            else:
                dt = f"{dmins//60}시간 전" if dmins >= 120 else f"{dmins}분 전"
                self.dash_stamp_lbl.config(text=f"· 대시보드 🔴 {dt}", fg="#E03131")

            for label, w in self.sheet_cells.items():
                stamp = stamps.get(label, "")
                mins = _stamp_minutes_ago(stamp)
                # 시각 표시: 'MM-DD HH:MM' 로 축약
                mt = re.search(r"(\d{4})-(\d{2}-\d{2}).*?(\d{1,2}:\d{2})", stamp or "")
                w["time"].config(text=f"{mt.group(2)} {mt.group(3)}" if mt else "기록 없음")
                if mins is None:
                    w["state"].config(text="⚪ 미기록", fg="#868E96")
                elif mins <= FRESH_MIN:
                    w["state"].config(text=f"🟢 정상 · {mins}분 전", fg="#2F9E44")
                elif mins <= STALE_MIN:
                    w["state"].config(text=f"🟡 지연 · {mins}분 전", fg="#F08C00")
                else:
                    txt = f"{mins//60}시간 전" if mins >= 120 else f"{mins}분 전"
                    w["state"].config(text=f"🔴 중단? · {txt}", fg="#E03131")
        self._sheet_status_job = self.after(GUI_REFRESH_SEC * 1000, self.refresh_sheet_status)

    def _build_cards(self):
        wrap = tk.Frame(self, bg="#F4F5F7")
        wrap.pack(fill="x", padx=GRID_PAD, pady=4)
        # 위 연동상태 행과 동일한 열 구성·여백 → 세로줄이 정확히 맞음
        for i, (name, color) in enumerate(DASH_CHANNELS):
            card = tk.Frame(wrap, bg="white",
                            highlightbackground="#E1E4E8", highlightthickness=1)
            card.grid(row=0, column=i, sticky="nsew", padx=CELL_PAD, pady=2)
            wrap.columnconfigure(i, weight=1, uniform="ch")
            bar = tk.Frame(card, bg=color, width=6)
            bar.pack(side="left", fill="y")
            inner = tk.Frame(card, bg="white")
            inner.pack(side="left", fill="both", expand=True, padx=8, pady=6)
            tk.Label(inner, text=name, font=FONT_BOLD, bg="white",
                     fg="#333").pack(anchor="w")
            cnt = tk.Label(inner, text="–", font=("맑은 고딕", 18, "bold"),
                           bg="white", fg="#111")
            cnt.pack(anchor="w")
            badge = tk.Label(inner, text="", font=("맑은 고딕", 9, "bold"),
                             bg="white", fg="#E03131")
            badge.pack(anchor="w")
            self.card_widgets[name] = {"count": cnt, "badge": badge, "bar": bar}

    def _build_toolbar(self):
        bar = tk.Frame(self, bg="#F4F5F7")
        bar.pack(fill="x", padx=16, pady=(8, 4))
        tk.Button(bar, text="🔄 새로고침", font=FONT_BOLD, bg="#1E90FF", fg="white",
                  relief="flat", padx=12, pady=4,
                  command=self.refresh_dashboard).pack(side="left")
        tk.Button(bar, text="🔑 채널 로그인", font=FONT, bg="#E9ECEF", relief="flat",
                  padx=10, pady=4, command=self.open_login_dialog).pack(side="left", padx=(6, 0))
        self.collector_btn = tk.Button(bar, text="", font=FONT_BOLD, relief="flat",
                                       padx=12, pady=4, command=self._toggle_collector)
        self.collector_btn.pack(side="left", padx=(16, 0))
        self.collector_state = tk.Label(bar, text="", font=FONT, bg="#F4F5F7")
        self.collector_state.pack(side="left", padx=(6, 0))
        tk.Button(bar, text="📄 로그", font=FONT, bg="#E9ECEF", relief="flat",
                  padx=10, pady=4, command=self._open_log).pack(side="left", padx=(6, 0))
        tk.Button(bar, text="🌐 웹 대시보드", font=FONT, bg="#E7F5FF", fg="#1971C2",
                  relief="flat", padx=10, pady=4,
                  command=self._open_webapp).pack(side="left", padx=(6, 0))

        tk.Label(bar, text="채널", font=FONT, bg="#F4F5F7").pack(side="left", padx=(20, 2))
        ch_values = ["전체"] + [n for n, _ in DASH_CHANNELS]
        ttk.Combobox(bar, textvariable=self.channel_var, width=10, font=FONT,
                     state="readonly", values=ch_values).pack(side="left", padx=4)
        self.channel_var.trace_add("write", lambda *a: self._render())
        ent = tk.Entry(bar, textvariable=self.search_var, font=FONT, width=16)
        ent.pack(side="left", padx=4)
        ent.bind("<Return>", lambda e: self._render())

    def _build_table(self):
        frame = tk.Frame(self, bg="white")
        frame.pack(fill="both", expand=True, padx=16, pady=8)
        self.tree = ttk.Treeview(frame, columns=[c[0] for c in COLS],
                                 show="headings", selectmode="extended")
        for key, label, width in COLS:
            self.tree.heading(key, text=label)
            anchor = "w" if key in ("message", "treatment", "name") else "center"
            self.tree.column(key, width=width, anchor=anchor, stretch=(key == "message"))
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        for name, color in DASH_CHANNELS:
            self.tree.tag_configure(f"ch_{name}", foreground=color)
        self.tree.bind("<Double-1>", self.on_row_double)

    def _build_statusbar(self):
        self.status = tk.Label(self, text="", font=("맑은 고딕", 9),
                               bg="#F4F5F7", fg="#666", anchor="w")
        self.status.pack(fill="x", padx=16, pady=(0, 8))

    # ── 필터(로드된 대시보드 상세에 대해, 클라이언트단) ───────
    def _visible_items(self) -> List[list]:
        items = self.dash.get("items", [])
        cname = self.channel_var.get()
        if cname != "전체":
            items = [r for r in items if r[0] == cname]
        q = self.search_var.get().strip()
        if q:
            items = [r for r in items if any(q in (c or "") for c in r)]
        return items

    # ── 대시보드(구글시트) 읽기 → 화면 갱신 ───────────────────
    def refresh_dashboard(self):
        """'대시보드' 탭을 백그라운드로 읽어와 카드·목록을 채운다(네트워크→스레드)."""
        if self._dash_job:
            self.after_cancel(self._dash_job)
            self._dash_job = None
        if self._loading:
            return
        self._loading = True
        self.status.config(text="대시보드 불러오는 중…")
        threading.Thread(target=self._dashboard_worker, daemon=True).start()

    def _dashboard_worker(self):
        data = read_dashboard_data()
        self.after(0, lambda: self._apply_dashboard(data))

    def _apply_dashboard(self, data: dict):
        self._loading = False
        if data["ok"]:
            self.dash = data
        else:
            self.status.config(text=f"대시보드 읽기 오류: {data['error'][:60]}")
        self._render()
        self._dash_job = self.after(GUI_REFRESH_SEC * 1000, self.refresh_dashboard)

    def on_row_double(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0], "values")   # (채널,이름,내용,시각,연락처)
        if vals:
            self._show_detail(vals)

    def _show_detail(self, vals):
        channel, name, message, tm, contact = (list(vals) + [""] * 5)[:5]
        win = tk.Toplevel(self)
        win.title("상담 상세")
        win.geometry("440x320")
        win.configure(bg="white")
        tk.Label(win, text=channel, font=FONT_TITLE, bg="white",
                 fg=DASH_COLOR.get(channel, "#333")).pack(anchor="w", padx=16, pady=(14, 4))
        for k, v in [("이름", name), ("연락처", contact), ("시각", tm)]:
            line = tk.Frame(win, bg="white")
            line.pack(fill="x", padx=16, pady=2)
            tk.Label(line, text=k, width=8, anchor="w", font=FONT_BOLD,
                     bg="white", fg="#666").pack(side="left")
            tk.Label(line, text=v, anchor="w", font=FONT, bg="white").pack(side="left")
        tk.Label(win, text="내용", font=FONT_BOLD, bg="white",
                 fg="#666").pack(anchor="w", padx=16, pady=(10, 2))
        txt = tk.Text(win, height=5, font=FONT, wrap="word")
        txt.insert("1.0", message)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True, padx=16, pady=(0, 14))

    def open_login_dialog(self):
        win = tk.Toplevel(self)
        win.title("채널 로그인")
        win.geometry("340x300")
        win.configure(bg="white")
        tk.Label(win, text="채널별 1회 로그인", font=FONT_TITLE,
                 bg="white").pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(win, text="버튼을 누르면 브라우저가 열립니다.\n로그인 후 그 창을 닫으세요.",
                 font=FONT, bg="white", fg="#666", justify="left").pack(anchor="w", padx=16)
        for ch in build_enabled_channels():
            row = tk.Frame(win, bg="white")
            row.pack(fill="x", padx=16, pady=3)
            tk.Label(row, text=ch.name, width=12, anchor="w", font=FONT,
                     bg="white").pack(side="left")
            tk.Button(row, text="로그인", font=FONT, relief="flat", bg="#E9ECEF",
                      command=lambda c=ch: self._do_login(c)).pack(side="right")

    def _do_login(self, ch: BaseChannel):
        if DEMO_MODE:
            messagebox.showinfo("안내", "DEMO_MODE 입니다. 실제 로그인은 DEMO_MODE=False 에서.")
            return
        if not ch.LOGIN_URL:
            messagebox.showwarning("안내", f"{ch.name} 의 LOGIN_URL 이 비어있습니다(코드에 입력 필요).")
            return
        try:
            driver = make_driver(ch.key, headless=False)
            driver.get(ch.LOGIN_URL)
            messagebox.showinfo("로그인", f"{ch.name} 로그인 후 [확인]을 누르세요.")
            driver.quit()
        except Exception as e:
            messagebox.showerror("오류", f"{ch.name} 로그인 창 오류:\n{e}")

    # ── 화면 렌더(로드된 self.dash 로) ────────────────────────
    def _render(self):
        # 카드: 채널별 미확인 건수(+잔액 비고)
        counts = {c["name"]: c for c in self.dash.get("channels", [])}
        total = 0
        failed = []                               # 이번 사이클 수집 실패한 채널명
        for name, w in self.card_widgets.items():
            c = counts.get(name)
            raw = str((c or {}).get("count", "")).strip()
            # 숫자가 아니면 그 문자열이 곧 실패 사유('로그인 실패' 등)
            if raw and not raw.isdigit():
                w["count"].config(text="!", fg="#F08C00")
                w["badge"].config(text=f"⚠ {raw}", fg="#F08C00")
                failed.append(f"{name}({raw})")
                self._fail_detail[name] = (c or {}).get("note", "")
                continue
            self._fail_detail.pop(name, None)
            n = int(raw) if str(raw).isdigit() else 0
            total += n
            w["count"].config(text=str(n), fg="#111")
            w["badge"].config(text=f"🔴 미확인 {n}" if n else "", fg="#E03131")

        # 상단 미확인 합계 — 실패 채널은 0으로 묻히므로 반드시 함께 표기
        fail_txt = f"  ⚠ 수집실패 {len(failed)}건({', '.join(failed)})" if failed else ""
        if total:
            self.alert.config(text=f"🔴 미확인 상담 {total}건{fail_txt}", fg="#E03131")
        elif failed:
            self.alert.config(text=f"⚠ 미확인 0건 ·{fail_txt.strip()}", fg="#F08C00")
        else:
            self.alert.config(text="✅ 미확인 없음", fg="#2F9E44")

        # 상세 목록
        items = self._visible_items()
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(items):
            channel = r[0] if r else ""
            self.tree.insert("", "end", iid=str(i), tags=[f"ch_{channel}"],
                             values=(r[0], r[1], r[2], r[3], r[4]))

        updated = self.dash.get("updated", "")
        self.status.config(
            text=f"대시보드 {len(items)}건 · 시트 업데이트: {updated} · "
                 f"화면 새로고침 {datetime.now():%H:%M:%S}")

    # ── 수집기(run_dashboard.py) 백그라운드 실행 제어 ──────────
    def _collector_running(self) -> bool:
        p = self.collector_proc
        return bool(p and p.poll() is None)

    def _boot_collector(self):
        """시작 시 자동수집이 켜져 있으면 수집기 실행."""
        if self.auto_var.get():
            self._start_collector()
        else:
            self._update_collector_label()

    def _toggle_collector(self):
        if self._collector_running():
            self.auto_var.set(False)
            self._stop_collector()
        else:
            self.auto_var.set(True)
            self._start_collector()

    def _resolve_lock_holder(self) -> bool:
        """수집기 잠금(포트 9765)을 쥔 프로세스를 이름·PID 로 알려주고,
        사용자가 원하면 그 자리에서 종료한다.
        반환 True = 잠금이 풀렸으니 수집기를 시작해도 된다."""
        holder = collector_lock_holder()
        if holder is None:
            # 포트는 잡혔는데 주인을 못 찾음(netstat 실패·권한 등) → 안내만.
            # 소스 실행이면 app_exe_name()이 이미 python.exe 라 중복을 지운다.
            names = " · ".join(dict.fromkeys([app_exe_name(), "python.exe"]))
            messagebox.showwarning(
                "수집기 중복 실행",
                "이미 다른 수집기가 실행 중입니다.\n\n"
                "다른 대시보드 창이나 예전 버전 exe 가 떠 있는지 확인하세요.\n"
                f"(작업 관리자 › 세부 정보에서 {names} 확인)")
            return False

        pid, name = holder
        if not messagebox.askyesno(
                "수집기 중복 실행",
                "이미 다른 수집기가 실행 중입니다.\n\n"
                f"        {name}   (PID {pid})\n\n"
                "이 수집기를 종료하고 계속할까요?\n\n"
                "· 로그인된 상주 크롬은 종료되지 않습니다.\n"
                "· 저 수집기를 띄운 예전 대시보드 창이 남아 있다면\n"
                "  그 창은 따로 닫아 주세요.", icon="warning"):
            return False

        if not kill_pid(pid):
            messagebox.showerror(
                "종료 실패",
                f"{name} (PID {pid}) 를 종료하지 못했습니다.\n\n"
                "작업 관리자 › 세부 정보에서 직접 종료한 뒤 다시 시도하세요.")
            return False

        # 강제 종료된 수집기는 finally 를 못 돌아 시트 잠금이 남는다 → 대신 반납.
        # (안 하면 다른 PC 가 하트비트 만료까지 기다린다) 네트워크 호출이라 별도 스레드.
        threading.Thread(target=release_sheet_lock, daemon=True).start()
        self.status.config(text=f"이전 수집기 종료됨 · {name} (PID {pid})")
        return True

    def _start_collector(self):
        if self._collector_running():
            return
        # 이 GUI 가 띄운 게 아닌 수집기(옛 exe·다른 창)가 이미 돌고 있으면 막는다.
        # 두 수집기가 같은 브라우저/시트를 다투면 정상 결과가 '실패'로 덮어써진다.
        if collector_lock_held() and not self._resolve_lock_holder():
            self.auto_var.set(False)
            self._update_collector_label()
            return
        # 다른 PC가 잠금을 쥔 경우 — 수집기가 조용히 죽는 대신 여기서 이유를 알린다
        blocker = sheet_lock_blocker()
        if blocker:
            self.auto_var.set(False)
            self._update_collector_label()
            messagebox.showwarning(
                "다른 PC에서 실행 중",
                f"다른 PC의 수집기가 구글시트 잠금을 쥐고 있습니다.\n\n{blocker}\n\n"
                "그쪽을 종료하거나, 하트비트가 끊길 때까지"
                f" 최대 {SHEET_LOCK_STALE_SEC // 60}분 기다리세요.")
            return
        # 두 모드 모두 --collector 진입점으로 통일 → collector.log 에 기록됨
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--collector"]      # exe 자기 자신을 수집기로
        else:
            cmd = [sys.executable, str(BASE_DIR / "total.py"), "--collector"]
        try:
            # CREATE_NO_WINDOW(0x08000000): 별도 콘솔창 안 뜨게
            self.collector_proc = subprocess.Popen(
                cmd, cwd=str(BASE_DIR), creationflags=0x08000000)
            self.status.config(text=f"수집기 시작 · 2분마다 순회 · {datetime.now():%H:%M:%S}")
        except Exception as e:
            self.collector_proc = None
            self.auto_var.set(False)
            messagebox.showerror("수집기 오류", f"수집기 실행 실패:\n{e}")
        self._update_collector_label()

    def _stop_collector(self):
        p = self.collector_proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
        self.collector_proc = None
        # terminate() 는 자식의 finally 를 건너뛰므로 GUI 가 대신 잠금을 반납한다
        # (안 하면 다른 PC 가 하트비트 만료까지 최대 7분을 기다린다)
        threading.Thread(target=release_sheet_lock, daemon=True).start()
        self._update_collector_label()

    def _show_fail_detail(self):
        """상단 경고 클릭 → 채널별 실패 사유 원문(시트 '비고' 열)을 보여준다."""
        if not self._fail_detail:
            messagebox.showinfo("수집 실패 없음", "현재 실패한 채널이 없습니다.")
            return
        body = "\n\n".join(f"● {name}\n{detail or '(원문 없음)'}"
                           for name, detail in self._fail_detail.items())
        messagebox.showwarning(
            "수집 실패 사유",
            f"{body}\n\n자세한 내용은 '📄 로그' 버튼의 collector.log 를 확인하세요.")

    def _open_log(self):
        """수집기 로그를 기본 텍스트 편집기로 연다(실패 원인 확인용)."""
        path = BASE_DIR / "collector.log"
        if not path.exists():
            messagebox.showinfo("로그 없음",
                                "아직 로그가 없습니다.\n수집기를 한 번 켜면 생성됩니다.\n\n"
                                f"경로: {path}")
            return
        try:
            os.startfile(str(path))
        except Exception as e:
            messagebox.showerror("로그 열기 실패", f"{path}\n\n{e}")

    def _open_webapp(self):
        """웹 대시보드(Apps Script 웹앱)를 기본 브라우저로 연다."""
        import webbrowser
        try:
            webbrowser.open(WEBAPP_URL)
        except Exception as e:
            messagebox.showerror("웹 대시보드 열기 실패", f"{WEBAPP_URL}\n\n{e}")

    def _poll_collector(self):
        """수집기 생존 여부를 3초마다 확인해 라벨/체크박스에 반영."""
        # 자동수집 켜짐인데 프로세스가 죽었으면(크래시) 체크 해제
        if self.auto_var.get() and not self._collector_running():
            self.auto_var.set(False)
            self.collector_proc = None
        self._update_collector_label()
        self.after(3000, self._poll_collector)

    def _update_collector_label(self):
        if self._collector_running():
            self.collector_btn.config(text="⏸ 수집기 끄기", bg="#FFE3E3", fg="#C92A2A")
            self.collector_state.config(text="● 실행 중 · 2분 순회", fg="#2F9E44")
        else:
            self.collector_btn.config(text="▶ 수집기 켜기", bg="#D3F9D8", fg="#2B8A3E")
            self.collector_state.config(text="■ 중지됨", fg="#868E96")

    def _on_close(self):
        self._stop_collector()
        self.destroy()


def _pid_alive(pid: int) -> bool:
    """윈도우에서 해당 PID 가 살아있는지."""
    import ctypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    k = ctypes.windll.kernel32
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        return bool(ok) and code.value == STILL_ACTIVE
    finally:
        k.CloseHandle(h)


def _watch_parent(interval: float = 5.0) -> None:
    """부모(GUI)가 사라지면 수집기도 즉시 종료한다.
    GUI 를 작업관리자로 강제 종료해도 수집기가 고아로 남아 시트 잠금을 계속
    쥐고 있던 문제를 막는다(콘솔이 없어 눈에도 안 띈다)."""
    try:
        ppid = os.getppid()
    except Exception:
        return
    if not ppid or not _pid_alive(ppid):
        return                              # 단독 실행(부모 없음) → 감시 안 함

    def loop():
        while True:
            time.sleep(interval)
            if not _pid_alive(ppid):
                print(f"[종료] GUI(PID {ppid})가 종료되어 수집기도 함께 종료합니다.")
                sys.stdout.flush()
                release_sheet_lock()        # 다른 PC 가 7분 기다리지 않도록 반납
                os._exit(0)

    threading.Thread(target=loop, daemon=True).start()


def _run_collector() -> None:
    """수집기 루프. 콘솔이 없어도(--noconsole/CREATE_NO_WINDOW) 원인을 남기도록
    stdout/stderr 을 exe 옆 collector.log 로 돌린다."""
    import run_dashboard

    log_path = BASE_DIR / "collector.log"
    try:
        if log_path.exists() and log_path.stat().st_size > 5_000_000:   # 5MB 넘으면 1회 롤오버
            log_path.replace(log_path.with_suffix(".log.1"))
        f = open(log_path, "a", encoding="utf-8", buffering=1)
    except Exception:
        f = None

    if f is None:                       # 로그조차 못 열면 그냥 실행
        run_dashboard.main()
        return

    sys.stdout = sys.stderr = f
    print(f"\n===== 수집기 시작 {_now_stamp()} =====")
    _watch_parent()                 # GUI 가 죽으면 수집기도 함께 종료

    # 중복 실행 차단 — 두 수집기가 같은 브라우저/시트를 두고 다투면
    # 한쪽이 성공한 결과를 다른 쪽이 '실패'로 덮어쓴다.
    lock = acquire_collector_lock()
    if lock is None:
        holder = collector_lock_holder()
        who = f" — {holder[1]} (PID {holder[0]})" if holder else ""
        print(f"[중단] 이미 다른 수집기가 실행 중입니다"
              f"(포트 {COLLECTOR_LOCK_PORT} 점유){who}. 이 프로세스는 종료합니다.")
        f.flush()
        return

    try:
        run_dashboard.main()
    except BaseException:
        import traceback
        traceback.print_exc()           # 크래시 원인도 로그에 남김
        raise
    finally:
        print(f"===== 수집기 종료 {_now_stamp()} =====")
        f.flush()
        try:
            lock.close()        # 다음 실행이 바로 잠금을 잡을 수 있게
        except Exception:
            pass


if __name__ == "__main__":
    # exe 한 개가 두 역할: 인자 없으면 GUI, --collector 면 수집기 루프
    if "--collector" in sys.argv[1:]:
        _run_collector()
    else:
        App().mainloop()
