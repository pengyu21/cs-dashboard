/**
 * 상담 통합 대시보드 - 서버(Code.gs)
 * '대시보드2' 시트를 읽어 웹앱(Index.html)에 데이터 제공.
 */
const SHEET_URL = "https://docs.google.com/spreadsheets/d/YOUR_GOOGLE_ID/edit";

// ※ total.py 의 DASHBOARD_TAB 과 반드시 같아야 한다.
const TAB = "대시보드2";

// 각 채널 시트(A1 에 '…업데이트 완료 · PC' 스탬프가 있음). 인스타는 스크랩 대상 아님.
const CHANNEL_TABS = ["강남언니", "바비톡", "여신티켓", "네이버지도", "온라인상담", "온라인예약", "카카오톡"];

// ── '금일 현황' 보드 ────────────────────────────────────────────────
// 대시보드와 다른 문서라 ID 로 따로 연다(웹앱 실행 계정에 읽기 권한이 있어야 함).
// A3:J6 을 병합 구조까지 그대로 읽어 화면에서 칸이 어긋나지 않게 한다.
const TODAY_ID        = "YOUR_GOOGLE_ID";
const TODAY_TAB       = "TODAY";
const TODAY_RANGE     = "A3:J6";
const TODAY_TITLE     = "A1";     // 날짜 머리글(A1:D2 병합) — 표시값 그대로 사용

// ── 금일 현황 칸 마우스오버 명단 ────────────────────────────────────
// 지표 칸에 마우스를 올렸을 때 '그 숫자가 어떤 건들인지' 보여줄 목록.
// 같은 TODAY 문서의 다른 시트에서 조건에 맞는 행의 몇 개 열만 뽑는다.
// 키(초진콜·카톡예약·초진)는 Index.html 의 화면 표기 이름과 같아야 한다.
//   tab   = 읽을 시트 이름          cols  = 보여줄 열
//   today = 이 열이 '오늘'인 행만    has   = 이 열이 비어있지 않은 행만
//   like  = [열, 문구] — 그 열에 문구가 들어간 행만
const TODAY_DETAILS = {
  "초진콜":   { tab: "초진콜", cols: ["C", "H", "I", "K"], today: "B", like: ["F", "진"] },
  "카톡예약": { tab: "카톡",   cols: ["G", "D", "E", "H"], today: "B", has: "G" },
  "초진":     { tab: "실예약", cols: ["B", "C", "D", "E"], today: "A" }
};
const TODAY_DETAIL_MAX       = 60;   // 한 칸에 최대 몇 줄까지
const TODAY_DETAIL_CACHE_SEC = 20;   // 명단 캐시(초)
// ⚠️ 날짜 판정 시간대를 한국으로 못박는다.
//    Session.getScriptTimeZone() 을 쓰면 Apps Script 프로젝트 시간대가 서울이
//    아닐 때(만든 사람 지역을 따라간다) 날짜만 든 칸이 하루씩 밀려 '오늘'이
//    하나도 안 걸린다 — 실제로 카톡·실예약이 통째로 0건이 됐다.
const TODAY_TZ = "Asia/Seoul";
// 대시보드 본문 캐시(초). Index.html 의 폴링 주기(20초)보다 짧게 두면
// 화면 갱신 체감은 그대로면서 동시 실행 수만 줄어든다.
const DASH_CACHE_SEC  = 8;

const TODAY_CACHE_SEC = 30;       // Index.html 은 20초마다 폴링하지만 TODAY 는 30초짜리 캐시로
                                  // 받친다 → 남의 문서는 30초에 한 번만 열리고, 화면 값은
                                  // 최대 30초 이내로 갱신된다.

/**
 * ★ 점검용 — Apps Script 편집기에서 이 함수를 골라 [실행] 을 누르고
 *   아래 '실행 로그'를 보세요. 화면(웹앱)과 여기 숫자가 다르면
 *   "저장은 됐는데 배포가 안 된 것" 입니다.
 *   여기 숫자도 틀리면 "붙여넣기가 안 된 것" 입니다.
 */
function TEST_명단() {
  try { CacheService.getScriptCache().remove("todayDetails"); } catch (e) {}
  Logger.log("코드 표시(있어야 정상): TODAY_TZ=" +
             (typeof TODAY_TZ === "string" ? TODAY_TZ : "없음(옛 코드!)"));
  Logger.log("카톡 설정: " + JSON.stringify(TODAY_DETAILS["카톡예약"]));
  Logger.log("초진 설정: " + JSON.stringify(TODAY_DETAILS["초진"]));
  const d = getTodayDetails_();
  Object.keys(d).forEach(function (k) {
    const x = d[k];
    Logger.log("── " + k + " → " + x.total + "건" +
               (x.error ? "  오류: " + x.error : "") +
               "   [열: " + x.headers.join(" | ") + "]");
    x.rows.slice(0, 5).forEach(function (r, i) {
      Logger.log("     " + (i + 1) + ". " + r.join(" | "));
    });
  });
}

function doGet() {
  return HtmlService.createHtmlOutputFromFile("Index")
    .setTitle("상담 통합 대시보드")
    .addMetaTag("viewport", "width=device-width, initial-scale=1");
}

/**
 * 대시보드 시트 → {updated, channels[], items[], lockOwner, lockBeat, channelTimes[]}
 *
 * ※ 여기서 바로 시트를 읽지 않고 캐시를 한 겹 둔다.
 *   '나로 실행' 배포라 모든 PC·모든 탭의 폴링이 소유자 계정 하나의
 *   '동시 실행 30개' 한도를 같이 쓴다. 캐시가 없으면 탭 수 × 6회/분 만큼
 *   실제 실행이 생겨 금방 한도(= '너무 많은 스크립트' 오류)에 걸린다.
 *   DASH_CACHE_SEC 동안은 몇 개의 탭이 불러도 실제 시트 읽기는 한 번뿐이다.
 */
function getDashboard() {
  const cache = CacheService.getScriptCache();
  const hit = cache.get("dashboard");
  if (hit) return JSON.parse(hit);

  const out = buildDashboard_();
  // 100KB 를 넘으면 캐시에 못 넣는다 — 그때는 그냥 캐시 없이 돌린다(기능은 정상).
  try { cache.put("dashboard", JSON.stringify(out), DASH_CACHE_SEC); } catch (e) {}
  return out;
}

/** 실제 시트 읽기(캐시 미스일 때만 호출된다) */
function buildDashboard_() {
  const ss = SpreadsheetApp.openByUrl(SHEET_URL);
  const ws = ss.getSheetByName(TAB);
  const last = Math.max(ws.getLastRow(), 12);
  // A~H 까지 읽는다. H1=수집기 소유 PC, H2=하트비트, H3=실행 중인 버전
  const v = ws.getRange(1, 1, last, 8).getValues();

  const updated = String(v[0][3] || "");   // D1: 업데이트 시각

  // 요약 블록: '채널'+'미확인' 헤더 다음 ~ '합계'/빈 행 전까지
  let ss2 = -1;
  for (let i = 0; i < v.length; i++) {
    if (String(v[i][0]).trim() === "채널" && String(v[i][1]).trim() === "미확인") { ss2 = i + 1; break; }
  }
  const channels = [];
  if (ss2 >= 0) for (let i = ss2; i < v.length; i++) {
    const name = String(v[i][0]).trim();
    if (!name || name === "합계") break;
    channels.push({
      name: name,
      // ※ Number() 로 바꾸면 '로그인 실패' 같은 사유가 NaN→0 이 되어
      //   실패가 '0건'으로 둔갑한다. 반드시 문자열 그대로 넘길 것.
      count: String(v[i][1] == null ? "" : v[i][1]).trim(),
      balance: String(v[i][2] || ""),
      note: String(v[i][3] || "")
    });
  }

  // 상세 블록: '채널'+'이름' 헤더 다음 ~ 빈 행 전까지
  let ds = -1;
  for (let i = 0; i < v.length; i++) {
    if (String(v[i][0]).trim() === "채널" && String(v[i][1]).trim() === "이름") { ds = i + 1; break; }
  }
  const items = [];
  if (ds >= 0) for (let i = ds; i < v.length; i++) {
    if (!String(v[i][0]).trim()) break;
    items.push([0, 1, 2, 3, 4].map(function (j) { return cellStr_(v[i][j]); }));
  }

  // 수집기 잠금(= 동작 표시). 수집기가 매 사이클 H2 를 갱신한다.
  const lockOwner = String(v[0][7] || "").trim();   // H1: 'PC이름|PID'
  const lockBeat = cellStr_(v[1][7]).trim();        // H2: 'yyyy-MM-dd HH:mm:ss'
  // H3: 지금 수집 중인 코드 버전('1.0.6' / 개발 PC 는 '1.0.6(개발)').
  // 수집기가 하트비트와 같이 갱신하므로 '실제로 돌고 있는 버전'이다.
  const version = cellStr_(v[2][7]).trim();

  // 채널별 최종 업데이트 시각 = 각 채널 시트의 A1
  const channelTimes = CHANNEL_TABS.map(function (name) {
    const sh = ss.getSheetByName(name);
    return { name: name, updated: sh ? cellStr_(sh.getRange("A1").getValue()) : "" };
  });

  return {
    updated: updated, channels: channels, items: items,
    lockOwner: lockOwner, lockBeat: lockBeat, version: version,
    channelTimes: channelTimes,
    today: getTodayBoard_(),
    todayDetails: getTodayDetails_()
  };
}

/** 열 문자('C', 'AA') → 0부터 세는 번호 */
function colIdx_(letter) {
  let n = 0;
  const s = String(letter).toUpperCase();
  for (let i = 0; i < s.length; i++) n = n * 26 + (s.charCodeAt(i) - 64);
  return n - 1;
}

/** 날짜값/날짜문자 → 'yyyy-MM-dd'. 못 읽으면 ''.
    시트 칸이 진짜 날짜면 Date 로, 텍스트면 문자열로 오므로 둘 다 받는다. */
function ymd_(v) {
  if (v instanceof Date && !isNaN(v)) {
    return Utilities.formatDate(v, TODAY_TZ, "yyyy-MM-dd");
  }
  const s = String(v == null ? "" : v).trim();
  let m = s.match(/(\d{4})\D+(\d{1,2})\D+(\d{1,2})/);      // 2026-09-07 / 2026.9.7
  if (m) return m[1] + "-" + ("0" + m[2]).slice(-2) + "-" + ("0" + m[3]).slice(-2);
  m = s.match(/^(\d{1,2})\D+(\d{1,2})$/);                   // 9/7 → 올해로 본다
  if (m) {
    return new Date().getFullYear() + "-" +
           ("0" + m[1]).slice(-2) + "-" + ("0" + m[2]).slice(-2);
  }
  return "";
}

/**
 * 금일 현황 칸별 마우스오버 명단.
 * → { 칸이름: { headers:[...], rows:[[...], ...], error:"" } }
 * 한 시트가 없거나 못 읽어도 나머지는 살린다(대시보드 전체를 죽이지 않는다).
 */
function getTodayDetails_() {
  const cache = CacheService.getScriptCache();
  const hit = cache.get("todayDetails");
  if (hit) return JSON.parse(hit);

  const out = {};
  const today = Utilities.formatDate(new Date(), TODAY_TZ, "yyyy-MM-dd");
  let ss = null;
  try { ss = SpreadsheetApp.openById(TODAY_ID); } catch (e) { ss = null; }

  Object.keys(TODAY_DETAILS).forEach(function (key) {
    const spec = TODAY_DETAILS[key];
    const res = { headers: [], rows: [], total: 0, error: "",
                  scanned: 0, today: "", sample: "" };
    out[key] = res;
    if (!ss) { res.error = "TODAY 문서를 열 수 없습니다(권한 확인)"; return; }
    try {
      const sh = ss.getSheetByName(spec.tab);
      if (!sh) { res.error = "'" + spec.tab + "' 시트를 찾을 수 없습니다"; return; }
      const nR = sh.getLastRow(), nC = sh.getLastColumn();
      if (nR < 2 || nC < 1) return;                 // 머리글뿐이거나 빈 시트
      const rg = sh.getRange(1, 1, nR, nC);
      const raw = rg.getValues();                   // 날짜 판정용(Date 그대로)
      const disp = rg.getDisplayValues();           // 표시용(시트에 보이는 그대로)
      const idx = spec.cols.map(colIdx_);
      res.headers = idx.map(function (i) {
        return String((disp[0] || [])[i] || "").trim();
      });
      // 0건일 때 '무엇과 비교했는지'를 화면에서 바로 알 수 있게 남긴다
      res.scanned = nR - 1;
      res.today = today;
      if (spec.today && nR > 1) {
        res.sample = String(disp[1][colIdx_(spec.today)] || "").trim();
      }
      // 조건에 맞는 건수는 끝까지 세고, 화면에 넘기는 줄만 상한을 둔다.
      // (예전엔 상한에서 멈춰 세는 것도 같이 멈춰, 조건이 헐거워 전부 통과해도
      //  '60건'으로만 보여 잘못된 필터를 눈치채기 어려웠다)
      for (let r = 1; r < nR; r++) {
        if (spec.today) {
          const ci = colIdx_(spec.today);
          // 표시값('2026-09-07 (월)')을 먼저 본다 — 화면에 보이는 그대로라
          // 시간대·서식에 흔들리지 않는다. 못 읽으면 날짜값으로 폴백.
          const cellYmd = ymd_(disp[r][ci]) || ymd_(raw[r][ci]);
          if (cellYmd !== today) continue;
        }
        if (spec.has &&
            !String(disp[r][colIdx_(spec.has)] || "").trim()) continue;
        if (spec.like &&
            String(disp[r][colIdx_(spec.like[0])] || "")
              .indexOf(spec.like[1]) < 0) continue;
        const row = idx.map(function (i) {
          return String(disp[r][i] == null ? "" : disp[r][i]).trim();
        });
        if (!row.join("")) continue;                // 전부 빈 줄은 버린다
        res.total++;
        if (res.rows.length < TODAY_DETAIL_MAX) res.rows.push(row);
      }
    } catch (e) {
      res.error = String((e && e.message) || e);
    }
  });

  try { cache.put("todayDetails", JSON.stringify(out), TODAY_DETAIL_CACHE_SEC); }
  catch (e) {}
  return out;
}

/**
 * TODAY 시트 A3:J6 → {title, rows:[[{t,cs,rs}, …], …]}
 * 가로 병합(예: '금일 신규' = A3:B3)은 좌상단 칸만 남기고 colspan 으로 넘긴다.
 * → 화면에서 라벨 줄과 숫자 줄의 칸이 시트와 똑같이 맞는다.
 */
function getTodayBoard_() {
  const cache = CacheService.getScriptCache();
  const hit = cache.get("todayBoard");
  if (hit) return JSON.parse(hit);

  let out;
  try {
    const sh = SpreadsheetApp.openById(TODAY_ID).getSheetByName(TODAY_TAB);
    if (!sh) throw new Error("'" + TODAY_TAB + "' 시트를 찾을 수 없습니다");

    const rg = sh.getRange(TODAY_RANGE);
    const r0 = rg.getRow(), c0 = rg.getColumn();
    const nR = rg.getNumRows(), nC = rg.getNumColumns();
    const v = rg.getDisplayValues();          // 시트에 보이는 그대로(서식 포함)

    const cs = [], rs = [], skip = [];
    for (let i = 0; i < nR; i++) {
      cs.push(new Array(nC).fill(1));
      rs.push(new Array(nC).fill(1));
      skip.push(new Array(nC).fill(false));
    }
    // 병합 덩어리 = 좌상단 한 칸 + 가려진 나머지. 범위 밖으로 걸치면 잘라서 쓴다.
    rg.getMergedRanges().forEach(function (m) {
      const ai = Math.max(m.getRow() - r0, 0);
      const aj = Math.max(m.getColumn() - c0, 0);
      const bi = Math.min(m.getRow() - r0 + m.getNumRows(), nR);
      const bj = Math.min(m.getColumn() - c0 + m.getNumColumns(), nC);
      if (ai >= bi || aj >= bj) return;
      for (let a = ai; a < bi; a++) for (let b = aj; b < bj; b++) {
        if (a === ai && b === aj) { cs[a][b] = bj - aj; rs[a][b] = bi - ai; }
        else skip[a][b] = true;
      }
    });

    const rows = [];
    for (let i = 0; i < nR; i++) {
      const cells = [];
      for (let j = 0; j < nC; j++) {
        if (skip[i][j]) continue;
        cells.push({ t: String(v[i][j] == null ? "" : v[i][j]).trim(), cs: cs[i][j], rs: rs[i][j] });
      }
      rows.push(cells);
    }
    // 끝쪽 빈 줄만 잘라낸다(중간 줄을 지우면 세로 병합이 어긋난다)
    while (rows.length && rows[rows.length - 1].every(function (c) { return !c.t; })) rows.pop();

    out = { title: sh.getRange(TODAY_TITLE).getDisplayValue().trim(), rows: rows };
  } catch (e) {
    // 권한이 없거나 시트가 바뀐 경우 — 대시보드 본체는 그대로 뜨게 두고 이 블록만 숨긴다
    out = { error: String((e && e.message) || e), rows: [] };
  }
  cache.put("todayBoard", JSON.stringify(out), TODAY_CACHE_SEC);
  return out;
}

/** 셀 값을 문자열로. 날짜(Date)는 'yyyy-MM-dd HH:mm' 로 (요일은 화면에서 붙임). */
function cellStr_(x) {
  if (Object.prototype.toString.call(x) === "[object Date]") {
    return Utilities.formatDate(x, "Asia/Seoul", "yyyy-MM-dd HH:mm");
  }
  return String(x == null ? "" : x);
}

// ══════════════════════════════════════════════════════════════
// [수집 중단 감시]  수집기 PC 가 꺼져도 알람이 오게 한다.
// ══════════════════════════════════════════════════════════════
// 수집기(total.py/runner.py)가 꺼지면 파이썬 쪽 텔레그램 알림도 같이 죽는다.
// → 구글 서버에서 도는 이 스크립트가 1분마다 H2(하트비트)·D1(마지막 수집 완료)을 보고,
//   하트비트가 끊기면(꺼짐) 또는 D1 이 WATCH_STALE_MIN 을 넘으면(멈춤) 텔레그램 + 메일로 알린다.
//   계속 멈춰 있으면 WATCH_RENOTIFY_MIN 마다 다시 알리고, 살아나면 복구 알림 1회.
//
// ▶ 최초 1회 설정 (Apps Script 편집기)
//   1) 프로젝트 설정 → 스크립트 속성에 추가
//        TELEGRAM_TOKEN   = secrets.json 의 telegram_token
//        TELEGRAM_CHAT_ID = secrets.json 의 telegram_chat_id
//      (토큰을 코드에 적지 않는다 — 이 파일은 텍스트로 여기저기 복사된다)
//   2) 함수 목록에서 SETUP_수집감시 를 골라 [실행] → 권한 허용
//      → 1분 트리거가 생기고 테스트 알림이 텔레그램·메일로 간다.
//   끄려면 STOP_수집감시 실행.
const WATCH_STALE_MIN    = 10;    // D1 이 이 분수보다 오래되면 '수집 멈춤'(프로그램은 켜져 있는데 순회가 안 끝남)
const WATCH_BEAT_MIN     = 3;     // H2 하트비트(45초마다)가 이 분수보다 오래되면 '프로그램 꺼짐'
const WATCH_CONFIRM_MIN  = 3;     // 이상 상태가 이 분수 이상 '계속'돼야 알린다.
                                  // (write_dashboard 가 A1:F1000 을 지우고 다시 쓰는 1~2초 사이에
                                  //  D1 이 비어 보여 '수집 멈춤 → 바로 재개' 오알림이 하루 몇 번씩 났다)
const WATCH_RENOTIFY_MIN = 30;    // 멈춘 동안 재알림 간격(분)
const WATCH_EVERY_MIN    = 1;     // 감시 주기(분) — 트리거는 1/5/10/15/30 만 가능
                                  // (1회 ~1초 × 1440회/일 ≈ 25분 — 개인 계정 트리거 한도 90분/일 안)
const WATCH_EMAILS       = ["alert@example.com"];
const WEBAPP_LINK        = "";    // (선택) 알림에 넣을 웹 대시보드 주소

/** 트리거가 1분마다 부르는 본체 */
function watchCollector() {
  const props = PropertiesService.getScriptProperties();
  const now = new Date();

  let stamp = "", beat = "", owner = "", mins = null, readErr = "";
  try {
    const v = SpreadsheetApp.openByUrl(SHEET_URL).getSheetByName(TAB).getRange("D1:H2").getValues();
    stamp = cellStr_(v[0][0]).trim();                // D1 마지막 수집 완료
    owner = String(v[0][4] || "").trim();            // H1 'PC|PID' (정상 종료하면 비워짐)
    beat  = cellStr_(v[1][4]).trim();                // H2 하트비트
    mins  = kstMinsAgo_(stamp, now);
  } catch (e) {
    readErr = String((e && e.message) || e);
  }
  // 시트를 못 읽은 건 수집기 문제가 아닐 수 있다(구글 일시 오류) → 알리지 않고 다음 주기에 재확인
  if (readErr) { console.warn("감시: 시트 읽기 실패 — " + readErr); return; }

  // 꺼짐: 잠금(H1)이 비었거나 하트비트가 끊김 → 1~3분 안에 바로 잡힌다.
  // 멈춤: 프로그램은 살아 있는데(하트비트 O) 순회가 안 끝남 → D1 기준.
  const beatMins = kstMinsAgo_(beat, now);
  const off   = !owner || beatMins == null || beatMins > WATCH_BEAT_MIN;
  const stuck = mins == null || mins > WATCH_STALE_MIN;
  const down  = off || stuck;
  const since = Number(props.getProperty("watch_down_since") || 0);   // 중단 감지 시각(ms)
  const lastSent = Number(props.getProperty("watch_last_sent") || 0);

  if (!down) {
    if (since) {
      if (lastSent) {                            // 중단 알림을 보냈던 경우에만 복구 알림 1회
        notifyAll_("✅ [CS대시보드] 수집 재개",
          "수집이 다시 정상 동작합니다.\n" +
          "마지막 수집: " + stamp + "\n" +
          "중단 시간: 약 " + fmtMin_(Math.round((now - since) / 60000)));
      } else {                                   // 확인 시간 안에 풀린 일시적 이상 → 조용히 해제
        console.log("감시: 일시적 이상 " + fmtMin_(Math.round((now - since) / 60000)) + " — 알림 없이 해제");
      }
      props.deleteProperty("watch_down_since");
      props.deleteProperty("watch_last_sent");
    }
    return;
  }

  // 처음 감지하면 시각만 적고, WATCH_CONFIRM_MIN 동안 계속 이상일 때 알린다.
  if (!since) { props.setProperty("watch_down_since", String(now.getTime())); return; }
  const downMins = Math.round((now - since) / 60000);
  if (downMins < WATCH_CONFIRM_MIN) return;
  if (lastSent && now - lastSent < (WATCH_RENOTIFY_MIN - 1) * 60000) return;  // 재알림 간격 안 됐음

  const pc = owner.split("|")[0] || (stamp.split(" · ")[1] || "").trim() || "알 수 없음";
  notifyAll_("🔴 [CS대시보드] " + (off ? "수집 프로그램 꺼짐" : "수집 멈춤") +
             (downMins ? " (" + fmtMin_(downMins) + "째)" : ""),
    (off ? "수집 PC의 CS대시보드 프로그램이 꺼졌습니다(하트비트 끊김).\n" +
           "PC 전원·프로그램 창을 확인하고 다시 실행해 주세요.\n\n"
         : "프로그램은 켜져 있지만 수집이 " + WATCH_STALE_MIN + "분 넘게 끝나지 않습니다.\n" +
           "수집 PC의 크롬 창이 멈췄는지 확인하고, 안 되면 프로그램을 재시작해 주세요.\n\n") +
    "마지막 수집: " + (stamp || "기록 없음") + (mins == null ? "" : " (" + fmtMin_(mins) + " 전)") + "\n" +
    "수집 PC: " + pc + "\n" +
    "하트비트: " + (beat ? beat + (beatMins == null ? "" : " (" + fmtMin_(beatMins) + " 전)")
                         : "없음(프로그램 종료됨)") + "\n\n" +
    "※ 복구될 때까지 " + WATCH_RENOTIFY_MIN + "분마다 다시 알립니다." +
    (WEBAPP_LINK ? "\n" + WEBAPP_LINK : ""));
  props.setProperty("watch_last_sent", String(now.getTime()));
}

/** 텔레그램 + 메일. 한쪽이 실패해도 다른 쪽은 보낸다. */
function notifyAll_(subject, body) {
  const props = PropertiesService.getScriptProperties();
  const token = (props.getProperty("TELEGRAM_TOKEN") || "").trim();
  const chat  = (props.getProperty("TELEGRAM_CHAT_ID") || "").trim();
  if (token && chat) {
    try {
      const res = UrlFetchApp.fetch("https://api.telegram.org/bot" + token + "/sendMessage", {
        method: "post", muteHttpExceptions: true,
        payload: { chat_id: chat, text: subject + "\n\n" + body, disable_web_page_preview: "true" }
      });
      if (res.getResponseCode() !== 200) console.warn("텔레그램 실패: " + res.getContentText());
    } catch (e) { console.warn("텔레그램 오류: " + e); }
  } else {
    console.warn("텔레그램 건너뜀: 스크립트 속성 TELEGRAM_TOKEN / TELEGRAM_CHAT_ID 없음");
  }
  try {
    MailApp.sendEmail({ to: WATCH_EMAILS.join(","), subject: subject, body: body });
  } catch (e) { console.warn("메일 오류: " + e); }
}

/** 'yyyy-MM-dd … HH:mm(:ss)' → 몇 분 전인지. 수집 PC 가 한국 시각으로 쓰므로 서울 기준으로 해석한다.
    (프로젝트 시간대가 서울이 아니면 new Date(y,m,d,…) 는 시차만큼 틀린다 — TODAY_TZ 주석 참고) */
function kstMinsAgo_(s, now) {
  const m = String(s || "").match(/(\d{4})-(\d{2})-(\d{2}).*?(\d{1,2}):(\d{2})(?::(\d{2}))?/);
  if (!m) return null;
  const t = Utilities.parseDate(m[1] + "-" + m[2] + "-" + m[3] + " " + m[4] + ":" + m[5] + ":" + (m[6] || "00"),
                                "Asia/Seoul", "yyyy-MM-dd HH:mm:ss");
  return Math.max(0, Math.round(((now || new Date()) - t) / 60000));
}

function fmtMin_(n) {
  const d = Math.floor(n / 1440), h = Math.floor(n % 1440 / 60), mm = n % 60;
  return (d ? d + "일 " : "") + (h ? h + "시간 " : "") + mm + "분";
}

/** ★ 최초 1회 실행: 1분 감시 트리거 설치 + 테스트 알림 */
function SETUP_수집감시() {
  STOP_수집감시();
  ScriptApp.newTrigger("watchCollector").timeBased().everyMinutes(WATCH_EVERY_MIN).create();
  notifyAll_("🔔 [CS대시보드] 수집 중단 감시 설정 완료",
    WATCH_EVERY_MIN + "분마다 수집 상태를 확인합니다.\n" +
    "프로그램이 꺼지면 약 " + (WATCH_BEAT_MIN + WATCH_CONFIRM_MIN) + "분 안에, 수집이 " + (WATCH_STALE_MIN + WATCH_CONFIRM_MIN) +
    "분 넘게 멈추면 텔레그램과 메일(" + WATCH_EMAILS.join(", ") +
    ")로 " + WATCH_RENOTIFY_MIN + "분마다 알립니다.");
  Logger.log("트리거 설치 완료. 지금 상태 확인:");
  watchCollector();
}

/** 알림이 안 올 때 실행 → [실행 로그]에 트리거·속성·현재 판정을 찍는다(알림은 안 보냄) */
function CHECK_수집감시() {
  const n = ScriptApp.getProjectTriggers().filter(function (t) {
    return t.getHandlerFunction() === "watchCollector";
  }).length;
  const p = PropertiesService.getScriptProperties();
  Logger.log("감시 트리거: " + n + "개" + (n ? "" : "  ← 0개면 SETUP_수집감시 를 실행하세요"));
  Logger.log("TELEGRAM_TOKEN: " + (p.getProperty("TELEGRAM_TOKEN") ? "있음" : "없음 ← 스크립트 속성에 추가"));
  Logger.log("TELEGRAM_CHAT_ID: " + (p.getProperty("TELEGRAM_CHAT_ID") ? "있음" : "없음 ← 스크립트 속성에 추가"));
  Logger.log("중단 감지 중: " + (p.getProperty("watch_down_since") ? "예" : "아니오"));
  const v = SpreadsheetApp.openByUrl(SHEET_URL).getSheetByName(TAB).getRange("D1:H2").getValues();
  Logger.log("D1 마지막 수집: " + cellStr_(v[0][0]) + " (" + kstMinsAgo_(cellStr_(v[0][0])) + "분 전)");
  Logger.log("H1 잠금: " + (v[0][4] || "(비어 있음 = 프로그램 꺼짐)"));
  Logger.log("H2 하트비트: " + cellStr_(v[1][4]) + " (" + kstMinsAgo_(cellStr_(v[1][4])) + "분 전)");
}

/** 감시 끄기(트리거 삭제 + 상태 초기화) */
function STOP_수집감시() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === "watchCollector") ScriptApp.deleteTrigger(t);
  });
  const p = PropertiesService.getScriptProperties();
  p.deleteProperty("watch_down_since");
  p.deleteProperty("watch_last_sent");
}
