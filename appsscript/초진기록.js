/**
 * 매일 저녁 9시 TODAY 시트의 C4(초진콜)·F4(카톡)·J4(합계)를 '초진기록' 시트에 한 줄로 남긴다.
 *   초진기록: A 날짜 | B 초진콜 | C 카톡 | D 합계
 * 같은 날짜 행이 이미 있으면 새로 추가하지 않고 그 행을 덮어쓴다(수동 실행·중복 실행 대비).
 * TODAY_ID / TODAY_TAB / TODAY_TZ / TODAY_DETAILS / ymd_ / colIdx_ 는 Code.js 에 있다.
 */
const CHOJIN_LOG_TAB   = "초진기록";
const CHOJIN_LOG_CELLS = ["C4", "F4", "J4"];   // 초진콜, 카톡, 합계
const CHOJIN_LOG_HOUR  = 21;                    // 저녁 9시(프로젝트 시간대 Asia/Seoul)
// 지난 날짜 소급 계산: TODAY 칸과 같은 기준(웹앱 마우스오버 명단 설정)으로 날짜별 건수를 센다.
//   C4 초진콜 = '초진콜' 시트, F4 카톡 = '카톡' 시트(카톡예약), J4 합계 = '실예약' 시트
const CHOJIN_LOG_SOURCES = ["초진콜", "카톡예약", "초진"];   // TODAY_DETAILS 의 키, 열 순서대로
const CHOJIN_BACKFILL_FROM = "2026-11-01";
const CHOJIN_LOG_CACHE_SEC = 60;

/** 트리거가 부르는 함수: 오늘 값을 TODAY 시트에서 그대로 읽어 기록 */
function recordChojin() {
  const ss  = SpreadsheetApp.openById(TODAY_ID);
  const src = ss.getSheetByName(TODAY_TAB);
  if (!src) throw new Error("'" + TODAY_TAB + "' 시트를 찾을 수 없습니다");
  SpreadsheetApp.flush();
  const c4 = Number(src.getRange("C4").getValue()) || 0;   // 초진콜
  const f4 = Number(src.getRange("F4").getValue()) || 0;   // 카톡
  const vals = [c4, f4, c4 + f4];                           // 합계 = 초진콜 + 카톡
  const today = Utilities.formatDate(new Date(), TODAY_TZ, "yyyy-MM-dd");
  upsertChojinRows_(ss, [[today].concat(vals)], true);
  try { recordChannelsToday_(ss); } catch (e) { Logger.log("채널 기록 실패: " + e); }
  Logger.log("초진기록 " + today + ": " + JSON.stringify(vals));
}

/**
 * rows([[yyyy-MM-dd, b, c, d], …])를 초진기록 시트에 합쳐 넣고 날짜순으로 다시 쓴다.
 * overwrite=false 면 이미 있는 날짜는 건드리지 않는다(소급 계산이 실제 기록을 덮지 않게).
 */
function upsertChojinRows_(ss, rows, overwrite) {
  const dst = ss.getSheetByName(CHOJIN_LOG_TAB);
  if (!dst) throw new Error("'" + CHOJIN_LOG_TAB + "' 시트를 찾을 수 없습니다");
  const map = {};
  const last = dst.getLastRow();
  if (last >= 2) {
    dst.getRange(2, 1, last - 1, 4).getValues().forEach(function (r) {
      const d = ymd_(r[0]);
      if (d) map[d] = [d, r[1], r[2], r[3]];
    });
  }
  let added = 0;
  rows.forEach(function (r) {
    if (overwrite || !map[r[0]]) { map[r[0]] = r; added++; }
  });
  const out = Object.keys(map).sort().map(function (k) { return map[k]; });
  if (last >= 2) dst.getRange(2, 1, last - 1, 4).clearContent();
  if (out.length) dst.getRange(2, 1, out.length, 4).setValues(out);
  // E1: 마지막으로 기록한 시각
  dst.getRange("E1").setValue("업데이트: " + Utilities.formatDate(new Date(), TODAY_TZ, "yyyy-MM-dd HH:mm"));
  try { CacheService.getScriptCache().remove("chojinLog"); } catch (e) {}
  return added;
}

/** 시트 하나를 훑어 { 'yyyy-MM-dd': 건수 } — 조건은 TODAY_DETAILS 와 같다 */
function countByDate_(ss, spec) {
  const sh = ss.getSheetByName(spec.tab);
  if (!sh) throw new Error("'" + spec.tab + "' 시트를 찾을 수 없습니다");
  const nR = sh.getLastRow(), nC = sh.getLastColumn();
  const cnt = {};
  if (nR < 2) return cnt;
  const rg = sh.getRange(1, 1, nR, nC);
  const raw = rg.getValues(), disp = rg.getDisplayValues();
  const di = colIdx_(spec.today);
  for (let r = 1; r < nR; r++) {
    const d = ymd_(disp[r][di]) || ymd_(raw[r][di]);
    if (!d) continue;
    if (spec.has && !String(disp[r][colIdx_(spec.has)] || "").trim()) continue;
    if (spec.like && String(disp[r][colIdx_(spec.like[0])] || "").indexOf(spec.like[1]) < 0) continue;
    cnt[d] = (cnt[d] || 0) + 1;
  }
  return cnt;
}

/** 지난 날짜(from ~ 어제)를 원본 시트에서 다시 세어 채운다. 이미 있는 날짜는 그대로 둔다. */
function BACKFILL_초진기록(from) {
  from = from || CHOJIN_BACKFILL_FROM;
  const ss = SpreadsheetApp.openById(TODAY_ID);
  const counts = CHOJIN_LOG_SOURCES.map(function (k) { return countByDate_(ss, TODAY_DETAILS[k]); });
  const today = Utilities.formatDate(new Date(), TODAY_TZ, "yyyy-MM-dd");

  // 검증: 오늘 값을 같은 방식으로 세어 TODAY 칸과 비교한다
  const src = ss.getSheetByName(TODAY_TAB);
  const live = CHOJIN_LOG_CELLS.map(function (a) { return Number(src.getRange(a).getValue()) || 0; });
  const calc = counts.map(function (c) { return c[today] || 0; });
  const ok = live.join() === calc.join();
  Logger.log("검증(오늘) TODAY 칸 " + JSON.stringify(live) + " / 다시 센 값 " + JSON.stringify(calc) +
             (ok ? "  → 일치" : "  → ⚠️ 다름: 소급 값이 실제와 다를 수 있습니다"));

  const rows = [];
  const t = Utilities.parseDate(from, TODAY_TZ, "yyyy-MM-dd");
  for (let d = t; ; d = new Date(d.getTime() + 86400000)) {
    const k = Utilities.formatDate(d, TODAY_TZ, "yyyy-MM-dd");
    if (k >= today) break;
    const a = counts[0][k] || 0, b = counts[1][k] || 0;
    rows.push([k, a, b, a + b]);              // 합계 = 초진콜 + 카톡
  }
  const added = upsertChojinRows_(ss, rows, false);
  Logger.log("소급 " + rows.length + "일 중 " + added + "일 새로 기록");
  rows.forEach(function (r) { Logger.log("  " + r.join(" | ")); });
}

/** 웹앱 '초진기록' 탭 → [{d, a, b, c}] (날짜순) */
function getChojinLog() {
  const cache = CacheService.getScriptCache();
  const hit = cache.get("chojinLog");
  let out;
  if (hit) out = JSON.parse(hit);
  else {
    const sh = SpreadsheetApp.openById(TODAY_ID).getSheetByName(CHOJIN_LOG_TAB);
    if (!sh) throw new Error("'" + CHOJIN_LOG_TAB + "' 시트를 찾을 수 없습니다");
    const last = sh.getLastRow();
    out = [];
    if (last >= 2) sh.getRange(2, 1, last - 1, 4).getValues().forEach(function (r) {
      const d = ymd_(r[0]);
      if (d) out.push({ d: d, a: Number(r[1]) || 0, b: Number(r[2]) || 0, c: Number(r[3]) || 0 });
    });
    out.sort(function (x, y) { return x.d < y.d ? -1 : 1; });
    try { cache.put("chojinLog", JSON.stringify(out), CHOJIN_LOG_CACHE_SEC); } catch (e) {}
  }
  // 오늘 줄은 저장값(저녁 9시 기록) 대신 TODAY 시트 C4·F4 를 바로 읽어 쓴다 → 실시간 현황과 항상 같다
  try {
    const today = Utilities.formatDate(new Date(), TODAY_TZ, "yyyy-MM-dd");
    const v = SpreadsheetApp.openById(TODAY_ID).getSheetByName(TODAY_TAB).getRange("C4:F4").getValues()[0];
    const a = Number(v[0]) || 0, b = Number(v[3]) || 0;
    out = out.filter(function (r) { return r.d !== today; });
    out.push({ d: today, a: a, b: b, c: a + b, live: true });
  } catch (e) {}
  return out;
}

/** ★ 최초 1회 실행: 매일 저녁 9시 트리거 설치 + 11월 소급 + 오늘 기록 */
function SETUP_초진기록() {
  STOP_초진기록();
  ScriptApp.newTrigger("recordChojin").timeBased()
    .everyDays(1).atHour(CHOJIN_LOG_HOUR).nearMinute(0).inTimezone(TODAY_TZ).create();
  Logger.log("트리거 설치 완료(매일 " + CHOJIN_LOG_HOUR + "시).");
  BACKFILL_초진기록();
  recordChojin();
}

/** 자동 기록 끄기 */
function STOP_초진기록() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === "recordChojin") ScriptApp.deleteTrigger(t);
  });
}

/** ★ 합계(D) 를 전부 초진콜(B) + 카톡(C) 로 다시 계산한다 */
function FIX_합계_초진기록() {
  const sh = SpreadsheetApp.openById(TODAY_ID).getSheetByName(CHOJIN_LOG_TAB);
  const last = sh.getLastRow();
  if (last < 2) return;
  const v = sh.getRange(2, 2, last - 1, 2).getValues();
  let changed = 0;
  const old = sh.getRange(2, 4, last - 1, 1).getValues();
  const out = v.map(function (r, i) {
    if (r[0] === "" && r[1] === "") return [old[i][0]];
    const s = (Number(r[0]) || 0) + (Number(r[1]) || 0);
    if (Number(old[i][0]) !== s) changed++;
    return [s];
  });
  sh.getRange(2, 4, out.length, 1).setValues(out);
  sh.getRange("E1").setValue("업데이트: " + Utilities.formatDate(new Date(), TODAY_TZ, "yyyy-MM-dd HH:mm"));
  try { CacheService.getScriptCache().remove("chojinLog"); } catch (e) {}
  Logger.log("합계 다시 계산: " + out.length + "줄 중 " + changed + "줄 바뀜");
}
