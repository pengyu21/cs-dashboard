/**
 * 부위 분포 — 날짜별·부위별 초진 건수.
 *   카톡   : TODAY 문서 '카톡' 시트(지난 기록도 다 있음) — 실시간으로 바로 센다.
 *   초진콜 : 이번 달 = TODAY 문서 '초진콜' 시트(실시간)
 *            지난 달 = 드라이브 연도 폴더(CHOJIN_FOLDERS)의 'N월 전화내역' 탭(현재 L열 '부위').
 *                      화면에서 그 달을 처음 열 때 한 번 읽어 '초진부위' 시트에 저장하고, 다음부터는 저장값을 쓴다.
 *   부위 열 = 머리글(앞 6줄)에 '부위'가 들어간 칸. TODAY '초진콜' 은 못 찾으면 I열.
 *   행 조건: TODAY 초진콜은 F 에 '진'(CALL_SPEC = C4·유입 채널과 같음), 카톡은 G 채워짐 / 드라이브: '상담예약' 채워짐.
 *   한 줄 = 1건. '골절+코재수술' 처럼 여러 부위가 적혀도 나누지 않고 적힌 그대로 한 항목으로 센다.
 */
const PART_FALLBACK = { "초진콜": "I", "카톡예약": "" };   // '' = 머리글에서 못 찾으면 안 셈
const PART_CACHE_SEC = 60;
const PART_TAB = "초진부위";              // A 날짜 | B 부위 | C 건수   (지난 달 초진콜만)
const PART_DONE_PROP = "partMonthsDone";  // 저장 끝난 달 목록 ['2026-10', …]

/** 부위 칸 값 정리: 나누지 않고 기호 앞뒤 띄어쓰기만 맞춘다('골절 + 코재수술' = '골절+코재수술') */
function partName_(v) {
  return String(v == null ? "" : v).replace(/\s*([,，\/+·&])\s*/g, "$1")
           .replace(/\s+/g, " ").trim() || CH_NONE;
}

function partCol_(disp, fallback) {
  for (let r = 0; r < Math.min(6, disp.length); r++) {
    for (let c = 0; c < disp[r].length; c++) {
      if (String(disp[r][c]).replace(/\s/g, "").indexOf("부위") > -1) return { row: r, col: c };
    }
  }
  return fallback ? { row: 0, col: colIdx_(fallback) } : null;
}

/** TODAY 문서 시트 하나 → [[날짜, 부위, 건수], …] */
function partRows_(ss, key) {
  const spec = key === "초진콜" ? CALL_SPEC : TODAY_DETAILS[key];   // 초진콜 = CALL_SPEC(채널.js)
  const sh = ss.getSheetByName(spec.tab);
  if (!sh) throw new Error("'" + spec.tab + "' 시트를 찾을 수 없습니다");
  const nR = sh.getLastRow(), nC = sh.getLastColumn();
  if (nR < 2) return [];
  const rg = sh.getRange(1, 1, nR, nC);
  const raw = rg.getValues(), disp = rg.getDisplayValues();
  const pc = partCol_(disp, PART_FALLBACK[key]);
  if (!pc) throw new Error("'" + spec.tab + "' 시트에서 '부위' 머리글을 찾지 못했습니다");
  const di = colIdx_(spec.today);
  const cnt = {};
  for (let r = pc.row + 1; r < nR; r++) {
    const d = ymd_(disp[r][di]) || ymd_(raw[r][di]);
    if (!d) continue;
    if (spec.has && !String(disp[r][colIdx_(spec.has)] || "").trim()) continue;
    if (spec.like && String(disp[r][colIdx_(spec.like[0])] || "").indexOf(spec.like[1]) < 0) continue;
    const k = d + "\u0001" + partName_(disp[r][pc.col]);
    cnt[k] = (cnt[k] || 0) + 1;
  }
  return Object.keys(cnt).map(function (k) {
    const x = k.split("\u0001");
    return [x[0], x[1], cnt[k]];
  });
}

/** TODAY 시트 실시간 부위(1분 캐시) → { rows: [[날짜, 'a'|'b', 부위, 건수]], err } */
function liveParts_(ss) {
  const cache = CacheService.getScriptCache();
  const hit = cache.get("chojinPartsLive");
  if (hit) return JSON.parse(hit);
  const out = { rows: [], err: { a: "", b: "" } };
  [["a", "초진콜"], ["b", "카톡예약"]].forEach(function (x) {
    try {
      partRows_(ss, x[1]).forEach(function (r) { out.rows.push([r[0], x[0], r[1], r[2]]); });
    } catch (e) {
      out.err[x[0]] = String((e && e.message) || e);
    }
  });
  try { cache.put("chojinPartsLive", JSON.stringify(out), PART_CACHE_SEC); } catch (e) {}
  return out;
}

// ── 지난 달 초진콜 부위(드라이브) ──────────────────────────────────────
function partDone_() {
  return JSON.parse(PropertiesService.getScriptProperties().getProperty(PART_DONE_PROP) || "[]");
}

/** 드라이브에서 한 달 읽기 → { 'yyyy-MM-dd': { 부위: 건수 } }. 그 달 탭이 없으면 null */
function drivePartsMonth_(ym) {
  const y = ym.slice(0, 4), m = +ym.slice(5);
  const folderId = CHOJIN_FOLDERS[y];
  if (!folderId) return null;
  const nameRe = new RegExp("(^|[^0-9])" + m + "\\s*월");          // 'CS 전화내역 - 26년 10월'
  const tabRe = new RegExp("^\\s*" + m + "\\s*월\\s*전화\\s*내역");  // '10월 전화내역'
  const files = [], others = [];
  const it = DriveApp.getFolderById(folderId).getFiles();
  while (it.hasNext()) {
    const f = it.next();
    if (f.getMimeType() !== MimeType.GOOGLE_SHEETS) continue;
    (nameRe.test(f.getName()) ? files : others).push(f);
  }
  const out = {}, seen = {};
  let found = false;
  const scan = function (list) {
    list.forEach(function (f) {
      SpreadsheetApp.openById(f.getId()).getSheets().forEach(function (sh) {
        if (!tabRe.test(sh.getName())) return;
        const nR = sh.getLastRow(), nC = sh.getLastColumn();
        if (nR < 2 || nC < 2) return;
        const disp = sh.getRange(1, 1, nR, nC).getDisplayValues();
        // 머리글 줄: 앞 6줄 안에서 '날짜'·'상담예약' 이 같이 있는 줄(초진기록 소급과 같은 기준)
        let hr = -1, dc = 1, nc = 13, pc = -1;
        for (let r = 0; r < Math.min(6, nR); r++) {
          const row = disp[r].map(function (x) { return String(x).replace(/\s/g, ""); });
          const a = row.indexOf("날짜"), b = row.indexOf("상담예약");
          if (a > -1 && b > -1) {
            hr = r; dc = a; nc = b;
            pc = row.findIndex(function (x) { return x.indexOf("부위") > -1; });
            break;
          }
        }
        if (hr < 0 || pc < 0) return;
        found = true;
        for (let r = hr + 1; r < nR; r++) {
          const d = dateInYear_(disp[r][dc], y);
          if (!d || d.slice(0, 7) !== ym) continue;
          if (!String(disp[r][nc] || "").trim()) continue;
          const key = disp[r].slice(0, Math.min(nC, 6)).join("\u0001");   // 같은 줄이 여러 파일에 있어도 한 번만
          if (seen[key]) continue;
          seen[key] = 1;
          const p = partName_(disp[r][pc]);
          const mm = out[d] || (out[d] = {});
          mm[p] = (mm[p] || 0) + 1;
        }
      });
    });
  };
  scan(files);
  if (!found) scan(others);       // 파일 이름에 'N월' 이 없으면 폴더 전체에서 찾는다
  return found ? out : null;
}

/** 요청한 지난 달들의 저장값 → { ym: [[날짜, 부위, 건수], …] }. 아직 없는 달은 드라이브에서 읽어 저장. */
function storedParts_(ss, months) {
  const out = {};
  if (!months.length) return out;
  let done = partDone_();
  const need = months.filter(function (ym) { return done.indexOf(ym) < 0; });
  if (need.length) {
    const lock = LockService.getScriptLock();
    if (lock.tryLock(60000)) {
      try {
        done = partDone_();                         // 기다리는 동안 다른 창이 저장했을 수 있다
        need.filter(function (ym) { return done.indexOf(ym) < 0; }).forEach(function (ym) {
          const m = drivePartsMonth_(ym);
          if (!m) return;                           // 드라이브에 그 달 기록 없음 → TODAY 시트 값 사용
          let sh = ss.getSheetByName(PART_TAB);
          if (!sh) {
            sh = ss.insertSheet(PART_TAB);
            sh.getRange(1, 1, 1, 3).setValues([["날짜", "부위", "건수"]]).setFontWeight("bold");
            sh.setFrozenRows(1);
          }
          const rows = [];
          Object.keys(m).sort().forEach(function (d) {
            Object.keys(m[d]).forEach(function (p) { rows.push([d, p, m[d][p]]); });
          });
          if (rows.length) {
            const rg = sh.getRange(sh.getLastRow() + 1, 1, rows.length, 3);
            rg.offset(0, 0, rows.length, 2).setNumberFormat("@");   // 날짜·부위는 글자 그대로
            rg.setValues(rows);
          }
          done.push(ym);
          PropertiesService.getScriptProperties().setProperty(PART_DONE_PROP, JSON.stringify(done));
          Logger.log("초진부위 " + ym + " 드라이브에서 " + rows.length + "줄 저장");
        });
      } finally { lock.releaseLock(); }
    }
  }
  const sh = ss.getSheetByName(PART_TAB);
  if (sh && sh.getLastRow() >= 2) {
    sh.getRange(2, 1, sh.getLastRow() - 1, 3).getValues().forEach(function (r) {
      const d = ymd_(r[0]), ym = d.slice(0, 7);
      if (!d || months.indexOf(ym) < 0 || done.indexOf(ym) < 0) return;
      (out[ym] || (out[ym] = [])).push([d, String(r[1]), Number(r[2]) || 0]);
    });
  }
  months.forEach(function (ym) { if (done.indexOf(ym) > -1 && !out[ym]) out[ym] = []; });
  return out;
}

/**
 * 웹앱 → { rows: [[날짜, 'a'|'b', 부위, 건수], …], err: {a, b} } (a=초진콜, b=카톡) — from~to 기간만.
 */
function getChojinParts(from, to) {
  const today = Utilities.formatDate(new Date(), TODAY_TZ, "yyyy-MM-dd");
  const cur = today.slice(0, 7);
  from = from || cur + "-01"; to = to || today;
  const ss = SpreadsheetApp.openById(TODAY_ID);
  const live = liveParts_(ss);
  const err = { a: live.err.a, b: live.err.b };
  const inR = function (d) { return d >= from && d <= to; };
  const months = [];                                // 기간에 걸친 지난 달들
  for (let ym = from.slice(0, 7); ym <= to.slice(0, 7) && ym < cur; ) {
    months.push(ym);
    const y = +ym.slice(0, 4), m = +ym.slice(5);
    ym = m === 12 ? (y + 1) + "-01" : y + "-" + ("0" + (m + 1)).slice(-2);
  }
  let stored = {};
  try { stored = storedParts_(ss, months); }
  catch (e) { err.a = "지난 달 부위 읽기 실패: " + ((e && e.message) || e); }
  const rows = [];
  live.rows.forEach(function (r) {
    if (!inR(r[0])) return;
    if (r[1] === "a" && stored[r[0].slice(0, 7)]) return;   // 드라이브에서 읽은 달은 그 값을 쓴다
    rows.push(r);
  });
  Object.keys(stored).forEach(function (ym) {
    stored[ym].forEach(function (r) { if (inR(r[0])) rows.push([r[0], "a", r[1], r[2]]); });
  });
  return { rows: rows, err: err };
}

/**
 * 드라이브 파일을 고친 뒤 그 달을 다시 읽게 할 때 실행.
 *   RESET_초진부위()  → 전부 비움 / 특정 달만: 아래 RESET_초진부위_달 의 값을 바꿔 실행
 */
function RESET_초진부위(ym) {
  const props = PropertiesService.getScriptProperties();
  const sh = SpreadsheetApp.openById(TODAY_ID).getSheetByName(PART_TAB);
  const last = sh ? sh.getLastRow() : 0;
  const keep = !ym || last < 2 ? [] : sh.getRange(2, 1, last - 1, 3).getValues()
    .filter(function (r) { return ymd_(r[0]).slice(0, 7) !== ym; });
  if (last >= 2) sh.getRange(2, 1, last - 1, 3).clearContent();
  if (keep.length) sh.getRange(2, 1, keep.length, 3).setValues(keep);
  props.setProperty(PART_DONE_PROP, JSON.stringify(ym ? partDone_().filter(function (x) { return x !== ym; }) : []));
  Logger.log((ym || "전체") + " 비움 — 화면에서 그 달을 열면 드라이브에서 다시 읽습니다");
}
function RESET_초진부위_달() { RESET_초진부위("2026-10"); }
