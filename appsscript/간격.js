/**
 * 상담 간격 — 예약을 잡은 날(예약일)과 상담 날짜(상담일)가 며칠 차이 나는지.
 *   목적: 당월 상담으로 얼마나 빨리 이끄는지 보기(먼 날짜로 잡힌 예약은 이탈 위험이 크다).
 *
 *   한 줄 = 예약 1건 = [예약일, 'a'|'b', 채널, 상담일('' = 못 읽음)]   (a=초진콜, b=카톡)
 *   초진콜 : 이번 달 = TODAY '초진콜' 시트(CALL_SPEC: B 날짜, F 에 '진'), 상담일 = '상담예약' 머리글 칸(없으면 K)
 *            지난 달 = 드라이브 'N월 전화내역' 탭(날짜·상담예약 머리글). 처음 열 때 한 번 읽어 '초진간격' 시트에 저장.
 *   카톡   : TODAY '카톡' 시트(카톡예약 기준: B 날짜, G 채워짐), 채널 = E, 상담일 = 머리글/값 모양으로 찾은 칸.
 *   채널·건수 기준은 초진기록·유입 채널과 같다.
 */
const GAP_TAB = "초진간격";                  // A 예약일 | B 채널 | C 상담일   (지난 달 초진콜만)
const GAP_DONE_PROP = "gapMonthsDone";
const GAP_CACHE_SEC = 60;
// 상담일 칸 머리글로 쓰일 만한 말(앞에 있을수록 우선)
const GAP_HEAD_WORDS = ["상담일", "상담날짜", "상담일자", "상담예약일", "예약일", "예약날짜", "예약일자", "내원일", "내원날짜", "상담예약"];
const GAP_FALLBACK = { "초진콜": "K", "카톡예약": "G" };

/**
 * 칸 값 → 상담일 'yyyy-MM-dd'. 연도가 없으면 예약일 연도로 보고,
 * 그러면 예약일보다 한 달 넘게 앞서는 경우(12월 예약 → '1/5') 다음 해로 넘긴다.
 */
function gapDate_(raw, disp, base) {
  if (raw instanceof Date && !isNaN(raw)) return Utilities.formatDate(raw, TODAY_TZ, "yyyy-MM-dd");
  const s = String(disp == null ? "" : disp).trim();
  if (!s) return "";
  let m = s.match(/(\d{4})\s*[.\-\/년]\s*(\d{1,2})\s*[.\-\/월]\s*(\d{1,2})/);
  if (m) return m[1] + "-" + ("0" + m[2]).slice(-2) + "-" + ("0" + m[3]).slice(-2);
  m = s.match(/^(\d{2})[.\-\/](\d{1,2})[.\-\/](\d{1,2})(?!\d)/);            // 26.12.03
  if (m) return "20" + m[1] + "-" + ("0" + m[2]).slice(-2) + "-" + ("0" + m[3]).slice(-2);
  m = s.match(/(?:^|[^\d:])(\d{1,2})\s*[.\-\/월]\s*(\d{1,2})(?![\d:])/);    // 12/3 · 12.3 · 12월 3일 (14:00 같은 시각은 제외)
  if (!m || !base) return "";
  const mo = +m[1], da = +m[2];
  if (mo < 1 || mo > 12 || da < 1 || da > 31) return "";
  let y = +base.slice(0, 4);
  let d = y + "-" + ("0" + mo).slice(-2) + "-" + ("0" + da).slice(-2);
  if (gapDiff_(base, d) < -31) d = (y + 1) + d.slice(4);
  return d;
}
function gapDiff_(a, b) {
  const t = function (x) { return Date.UTC(+x.slice(0, 4), +x.slice(5, 7) - 1, +x.slice(8, 10)); };
  return Math.round((t(b) - t(a)) / 86400000);
}
/** 상담일로 쓸 수 있는 값인가(예약일 0~365일 뒤) */
function gapOk_(base, d) {
  if (!d) return false;
  const n = gapDiff_(base, d);
  return n >= 0 && n <= 365;
}

/**
 * 상담일 칸 찾기: ① 머리글 낱말 ② 없으면 값이 '예약일 뒤 날짜'로 가장 많이 읽히는 칸 ③ 그래도 없으면 기본 칸.
 * rows = 조건을 통과한 줄 번호들
 */
function gapFindCol_(disp, raw, hr, rows, dateCol, fallback) {
  const head = (disp[hr] || []).map(function (x) { return String(x).replace(/\s/g, ""); });
  const sample = rows.slice(-300);
  const nC = (disp[hr] || []).length;
  // 칸 c 의 값이 '예약일 0~365일 뒤 날짜'로 읽히는 줄 수
  const score = function (c) {
    let n = 0;
    sample.forEach(function (r) {
      const b = ymd_(disp[r][dateCol]) || ymd_(raw[r][dateCol]) || dateInYear_(disp[r][dateCol], String(new Date().getFullYear()));
      if (b && gapOk_(b, gapDate_(raw[r][c], disp[r][c], b))) n++;
    });
    return n;
  };
  const enough = function (n) { return sample.length && n >= sample.length * 0.4; };
  for (let w = 0; w < GAP_HEAD_WORDS.length; w++) {
    for (let i = 0; i < nC; i++) {
      if (i === dateCol || !head[i] || head[i].indexOf(GAP_HEAD_WORDS[w]) < 0) continue;
      if (enough(score(i))) return { col: i, how: "머리글" };
    }
  }
  let best = -1, bestN = 0;
  for (let c = 0; c < nC; c++) {
    if (c === dateCol) continue;
    const n = score(c);
    if (n > bestN) { bestN = n; best = c; }
  }
  if (best > -1 && enough(bestN)) return { col: best, how: "값 모양" };
  return fallback ? { col: colIdx_(fallback), how: "기본" } : null;
}

function gapColName_(c) {
  let s = "", n = c + 1;
  while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); }
  return s;
}

/** TODAY 문서 시트 하나 → { rows: [[예약일, 채널, 상담일]], col: '설명' } */
function gapSheetRows_(ss, key) {
  const spec = key === "초진콜" ? CALL_SPEC : TODAY_DETAILS[key];
  const chCol = key === "초진콜" ? CH_CALL_COL : CH_KAKAO_COL;
  const sh = ss.getSheetByName(spec.tab);
  if (!sh) throw new Error("'" + spec.tab + "' 시트를 찾을 수 없습니다");
  const nR = sh.getLastRow(), nC = sh.getLastColumn();
  if (nR < 2) return { rows: [], col: "" };
  const rg = sh.getRange(1, 1, nR, nC);
  const raw = rg.getValues(), disp = rg.getDisplayValues();
  const di = colIdx_(spec.today), ci = colIdx_(chCol);
  const ok = [];
  for (let r = 1; r < nR; r++) {
    const d = ymd_(disp[r][di]) || ymd_(raw[r][di]);
    if (!d) continue;
    if (spec.has && !String(disp[r][colIdx_(spec.has)] || "").trim()) continue;
    if (spec.like && String(disp[r][colIdx_(spec.like[0])] || "").indexOf(spec.like[1]) < 0) continue;
    ok.push(r);
  }
  const fc = gapFindCol_(disp, raw, 0, ok, di, GAP_FALLBACK[key]);
  const rows = ok.map(function (r) {
    const d = ymd_(disp[r][di]) || ymd_(raw[r][di]);
    const c = fc ? gapDate_(raw[r][fc.col], disp[r][fc.col], d) : "";
    return [d, chName_(disp[r][ci]), gapOk_(d, c) ? c : ""];
  });
  const col = fc ? spec.tab + " " + gapColName_(fc.col) + "열 '" + String(disp[0][fc.col] || "").trim() + "' (" + fc.how + ")" : "";
  return { rows: rows, col: col };
}

/** TODAY 시트 실시간(1분 캐시) → { rows: [[예약일, 'a'|'b', 채널, 상담일]], col: {a, b}, err: {a, b} } */
function gapLive_(ss) {
  const cache = CacheService.getScriptCache();
  const hit = cache.get("gapLive");
  if (hit) return JSON.parse(hit);
  const out = { rows: [], col: { a: "", b: "" }, err: { a: "", b: "" } };
  [["a", "초진콜"], ["b", "카톡예약"]].forEach(function (x) {
    try {
      const g = gapSheetRows_(ss, x[1]);
      out.col[x[0]] = g.col;
      g.rows.forEach(function (r) { out.rows.push([r[0], x[0], r[1], r[2]]); });
    } catch (e) { out.err[x[0]] = String((e && e.message) || e); }
  });
  try { cache.put("gapLive", JSON.stringify(out), GAP_CACHE_SEC); } catch (e) {}
  return out;
}

// ── 지난 달 초진콜(드라이브) ──────────────────────────────────────────
function gapDone_() {
  return JSON.parse(PropertiesService.getScriptProperties().getProperty(GAP_DONE_PROP) || "[]");
}

/** 드라이브에서 한 달 → [[예약일, 채널, 상담일]]. 그 달 탭이 없으면 null */
function gapDriveMonth_(ym) {
  const y = ym.slice(0, 4), m = +ym.slice(5);
  const folderId = CHOJIN_FOLDERS[y];
  if (!folderId) return null;
  const nameRe = new RegExp("(^|[^0-9])" + m + "\\s*월");
  const tabRe = new RegExp("^\\s*" + m + "\\s*월\\s*전화\\s*내역");
  const files = [], others = [];
  const it = DriveApp.getFolderById(folderId).getFiles();
  while (it.hasNext()) {
    const f = it.next();
    if (f.getMimeType() !== MimeType.GOOGLE_SHEETS) continue;
    (nameRe.test(f.getName()) ? files : others).push(f);
  }
  const out = [], seen = {};
  let found = false;
  const scan = function (list) {
    list.forEach(function (f) {
      SpreadsheetApp.openById(f.getId()).getSheets().forEach(function (sh) {
        if (!tabRe.test(sh.getName())) return;
        const nR = sh.getLastRow(), nC = sh.getLastColumn();
        if (nR < 2 || nC < 2) return;
        const rg = sh.getRange(1, 1, nR, nC);
        const disp = rg.getDisplayValues(), raw = rg.getValues();
        // 머리글 줄: 앞 6줄 안에서 '날짜'·'상담예약' 이 같이 있는 줄(초진기록 소급과 같은 기준)
        let hr = -1, dc = 1, nc = 13, kc = 10;
        for (let r = 0; r < Math.min(6, nR); r++) {
          const row = disp[r].map(function (x) { return String(x).replace(/\s/g, ""); });
          const a = row.indexOf("날짜"), b = row.indexOf("상담예약");
          if (a > -1 && b > -1) {
            hr = r; dc = a; nc = b;
            const k = row.indexOf("하위구분"); if (k > -1) kc = k;
            break;
          }
        }
        if (hr < 0) return;
        found = true;
        const ok = [];
        for (let r = hr + 1; r < nR; r++) {
          const d = dateInYear_(disp[r][dc], y);
          if (!d || d.slice(0, 7) !== ym) continue;
          if (!String(disp[r][nc] || "").trim()) continue;
          const key = disp[r].slice(0, Math.min(nC, 6)).join("\u0001");
          if (seen[key]) continue;
          seen[key] = 1;
          ok.push([r, d]);
        }
        // 상담일 칸: '상담예약' 칸이 날짜로 읽히면 그 칸, 아니면 다른 상담일 머리글/값 모양
        const asDate = ok.filter(function (x) { return gapOk_(x[1], gapDate_(raw[x[0]][nc], disp[x[0]][nc], x[1])); }).length;
        let cc = nc;
        if (ok.length && asDate < ok.length * 0.4) {
          const fc = gapFindCol_(disp, raw, hr, ok.map(function (x) { return x[0]; }), dc, "");
          if (fc) cc = fc.col;
        }
        ok.forEach(function (x) {
          const r = x[0], d = x[1];
          const c = gapDate_(raw[r][cc], disp[r][cc], d);
          out.push([d, chName_(disp[r][kc]), gapOk_(d, c) ? c : ""]);
        });
      });
    });
  };
  scan(files);
  if (!found) scan(others);
  return found ? out : null;
}

/** 지난 달들 → { ym: [[예약일, 채널, 상담일]] }. 아직 없는 달은 드라이브에서 읽어 저장 */
function gapStored_(ss, months) {
  const out = {};
  if (!months.length) return out;
  let done = gapDone_();
  const need = months.filter(function (ym) { return done.indexOf(ym) < 0; });
  if (need.length) {
    const lock = LockService.getScriptLock();
    if (lock.tryLock(60000)) {
      try {
        done = gapDone_();
        need.filter(function (ym) { return done.indexOf(ym) < 0; }).forEach(function (ym) {
          const rows = gapDriveMonth_(ym);
          if (!rows) return;                        // 드라이브에 그 달 없음 → TODAY 시트 값 사용
          let sh = ss.getSheetByName(GAP_TAB);
          if (!sh) {
            sh = ss.insertSheet(GAP_TAB);
            sh.getRange(1, 1, 1, 3).setValues([["예약일", "채널", "상담일"]]).setFontWeight("bold");
            sh.setFrozenRows(1);
          }
          if (rows.length) {
            const rg = sh.getRange(sh.getLastRow() + 1, 1, rows.length, 3);
            rg.setNumberFormat("@");                // 글자 그대로(날짜 자동 변환 막기)
            rg.setValues(rows);
          }
          done.push(ym);
          PropertiesService.getScriptProperties().setProperty(GAP_DONE_PROP, JSON.stringify(done));
          Logger.log("초진간격 " + ym + " 드라이브에서 " + rows.length + "줄 저장");
        });
      } finally { lock.releaseLock(); }
    }
  }
  const sh = ss.getSheetByName(GAP_TAB);
  if (sh && sh.getLastRow() >= 2) {
    sh.getRange(2, 1, sh.getLastRow() - 1, 3).getDisplayValues().forEach(function (r) {
      const d = ymd_(r[0]), ym = d.slice(0, 7);
      if (!d || months.indexOf(ym) < 0 || done.indexOf(ym) < 0) return;
      (out[ym] || (out[ym] = [])).push([d, String(r[1]), ymd_(r[2])]);
    });
  }
  months.forEach(function (ym) { if (done.indexOf(ym) > -1 && !out[ym]) out[ym] = []; });
  return out;
}

/**
 * 웹앱 '상담 간격' 탭 → { rows: [[예약일, 'a'|'b', 채널, 상담일]], col: {a, b}, err: {a, b}, today }
 * from~to = 예약일 기간
 */
function getConsultGap(from, to) {
  const today = Utilities.formatDate(new Date(), TODAY_TZ, "yyyy-MM-dd");
  const cur = today.slice(0, 7);
  from = from || cur + "-01"; to = to || today;
  const ss = SpreadsheetApp.openById(TODAY_ID);
  const live = gapLive_(ss);
  const err = { a: live.err.a, b: live.err.b };
  const col = { a: live.col.a, b: live.col.b };
  const inR = function (d) { return d >= from && d <= to; };
  const months = [];
  for (let ym = from.slice(0, 7); ym <= to.slice(0, 7) && ym < cur; ) {
    months.push(ym);
    const y = +ym.slice(0, 4), m = +ym.slice(5);
    ym = m === 12 ? (y + 1) + "-01" : y + "-" + ("0" + (m + 1)).slice(-2);
  }
  let stored = {};
  try { stored = gapStored_(ss, months); }
  catch (e) { err.a = "지난 달 초진콜 읽기 실패: " + ((e && e.message) || e); }
  if (Object.keys(stored).length) col.a += (col.a ? " · " : "") + "지난 달: 드라이브 'N월 전화내역' 상담예약";
  const rows = [];
  live.rows.forEach(function (r) {
    if (!inR(r[0])) return;
    if (r[1] === "a" && stored[r[0].slice(0, 7)]) return;
    rows.push(r);
  });
  Object.keys(stored).forEach(function (ym) {
    stored[ym].forEach(function (r) { if (inR(r[0])) rows.push([r[0], "a", r[1], r[2]]); });
  });
  return { rows: rows, col: col, err: err, today: today };
}

/** 점검용 — 편집기에서 실행해 어떤 칸을 상담일로 읽는지, 몇 건이 날짜로 읽히는지 본다 */
function TEST_상담간격() {
  try { CacheService.getScriptCache().remove("gapLive"); } catch (e) {}
  const r = getConsultGap();
  Logger.log("상담일 칸: " + JSON.stringify(r.col));
  Logger.log("오류: " + JSON.stringify(r.err));
  ["a", "b"].forEach(function (k) {
    const all = r.rows.filter(function (x) { return x[1] === k; });
    const ok = all.filter(function (x) { return x[3]; });
    Logger.log((k === "a" ? "초진콜" : "카톡") + " " + all.length + "건 중 상담일 읽힘 " + ok.length + "건");
    all.slice(-5).forEach(function (x) { Logger.log("   " + x.join(" | ")); });
  });
}

/** 드라이브 파일을 고친 뒤 지난 달을 다시 읽게 할 때(전부 비움) */
function RESET_초진간격() {
  const sh = SpreadsheetApp.openById(TODAY_ID).getSheetByName(GAP_TAB);
  if (sh && sh.getLastRow() >= 2) sh.getRange(2, 1, sh.getLastRow() - 1, 3).clearContent();
  PropertiesService.getScriptProperties().setProperty(GAP_DONE_PROP, "[]");
  Logger.log("비움 — 화면에서 지난 달을 열면 드라이브에서 다시 읽습니다");
}
