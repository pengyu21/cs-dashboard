/**
 * 지난 해들의 '초진기록' 채우기.
 *
 * 연도별 드라이브 폴더(안에 '1월 전화내역', '2월 전화내역' … 구글시트)를 읽어 날짜별로 센다.
 *   초진콜 = 폴더 구글시트들의 'N월 전화내역' 탭에서 '날짜'(B)가 그날이고 '상담예약'(N)이 채워진 줄
 *            (머리글에 '날짜'·'상담예약'이 있으면 그 열을 쓴다). 같은 줄이 여러 파일에 있어도 한 번만 센다.
 *   카톡   = TODAY 문서 '카톡' 시트(카톡예약 기준: B 날짜, G 채워짐). 그 해 기록이 없으면 0
 *   합계   = 초진콜 + 카톡
 * 이미 '초진기록'에 적힌 날짜는 덮어쓰지 않는다.
 *
 * ★ 사용법: 아래 CHOJIN_FOLDERS 에 '연도: 폴더ID' 를 넣고 FILL_초진기록 을 한 번 실행.
 *   6분 실행 제한에 걸리면 1분 뒤 스스로 이어서 돈다(실행 로그·초진기록 E1 로 진행 확인).
 */
const CHOJIN_FOLDERS = {
  "2026": "YOUR_GOOGLE_ID"
  // "2025": "폴더ID",
  // "2024": "폴더ID",
};
const CALL_TAB_RE = /^\s*\d{1,2}\s*월\s*전화\s*내역/;   // '1월 전화내역' … '12월 전화내역' 탭만 읽는다
const FILL_PROP ="chojinFillDone";       // 끝난 연도 목록(이어하기용)
const FILL_TIME_LIMIT_MS = 4.5 * 60 * 1000;

function FILL_초진기록() {
  PropertiesService.getScriptProperties().deleteProperty(FILL_PROP);
  fillChojinContinue_();
}

/** 아직 안 끝난 연도만 이어서 처리(시간 제한 때 1분 뒤 트리거가 부른다) */
function fillChojinContinue_() {
  const t0 = Date.now();
  const props = PropertiesService.getScriptProperties();
  const done = JSON.parse(props.getProperty(FILL_PROP) || "[]");
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === "fillChojinContinue_") ScriptApp.deleteTrigger(t);
  });

  const ss = SpreadsheetApp.openById(TODAY_ID);
  const kakao = countByDate_(ss, TODAY_DETAILS["카톡예약"]);
  // 채널: 카톡은 '카톡' 시트 E, 드라이브에 없는 달의 초진콜은 TODAY '초진콜' 시트 K
  const ch = {
    kakao: channelByDate_(ss, TODAY_DETAILS["카톡예약"], CH_KAKAO_COL),
    call:  channelByDate_(ss, TODAY_DETAILS["초진콜"], CH_CALL_COL)
  };

  const years = Object.keys(CHOJIN_FOLDERS).sort();
  for (let i = 0; i < years.length; i++) {
    const y = years[i];
    if (done.indexOf(y) > -1) continue;
    if (Date.now() - t0 > FILL_TIME_LIMIT_MS) {
      ScriptApp.newTrigger("fillChojinContinue_").timeBased().after(60 * 1000).create();
      Logger.log("⏸ 시간 제한 — 1분 뒤 " + y + "년부터 자동으로 이어서 합니다");
      return;
    }
    fillYear_(ss, y, CHOJIN_FOLDERS[y], kakao, ch, t0);
    done.push(y);
    props.setProperty(FILL_PROP, JSON.stringify(done));
  }
  Logger.log("✅ 끝: " + done.join(", "));
}

function fillYear_(ss, y, folderId, kakao, ch, t0) {
  const today = Utilities.formatDate(new Date(), TODAY_TZ, "yyyy-MM-dd");
  const call = {}, callCh = {}, seen = {};
  Logger.log("■ " + y + "년");

  const it = DriveApp.getFolderById(folderId).getFiles();
  while (it.hasNext()) {
    const f = it.next();
    if (f.getMimeType() !== MimeType.GOOGLE_SHEETS) {
      Logger.log("  · 건너뜀(구글시트 아님): " + f.getName() + " [" + f.getMimeType() + "]");
      continue;
    }
    const sheets = SpreadsheetApp.openById(f.getId()).getSheets();
    const target = sheets.filter(function (sh) { return CALL_TAB_RE.test(sh.getName()); });
    if (!target.length) {
      Logger.log("  · 건너뜀('N월 전화내역' 탭 없음): " + f.getName() + "  탭: " +
                 sheets.map(function (sh) { return sh.getName(); }).join(", "));
    }
    target.forEach(function (sh) {
      const nR = sh.getLastRow(), nC = sh.getLastColumn();
      if (nR < 2 || nC < 2) return;
      const disp = sh.getRange(1, 1, nR, nC).getDisplayValues();
      // 머리글 줄: 앞 6줄 안에서 '날짜' 와 '상담예약' 이 같이 있는 줄
      let hr = -1, dc = 1, nc = 13, kc = 10;
      for (let r = 0; r < Math.min(6, nR); r++) {
        const row = disp[r].map(function (x) { return String(x).replace(/\s/g, ""); });
        const a = row.indexOf("날짜"), b = row.indexOf("상담예약");
        if (a > -1 && b > -1) {
          hr = r; dc = a; nc = b;
          const k = row.indexOf("하위구분"); if (k > -1) kc = k;      // 유입 채널
          break;
        }
      }
      if (hr < 0) { Logger.log("  · 건너뜀(머리글 없음): " + f.getName() + " / " + sh.getName()); return; }
      let n = 0;
      for (let r = hr + 1; r < nR; r++) {
        const d = dateInYear_(disp[r][dc], y);
        if (!d || d.slice(0, 4) !== y || d >= today) continue;
        if (!String(disp[r][nc] || "").trim()) continue;
        const key = disp[r].slice(0, Math.min(nC, 6)).join("\u0001");   // 입력시간·날짜·이름·전화…
        if (seen[key]) continue;
        seen[key] = 1;
        call[d] = (call[d] || 0) + 1;
        const cn = String(disp[r][kc] || "").trim() || CH_NONE;
        const cm = callCh[d] || (callCh[d] = {});
        cm[cn] = (cm[cn] || 0) + 1;
        n++;
      }
      Logger.log("  · " + f.getName() + " / " + sh.getName() + ": " + n + "건");
    });
  }

  const rows = [], month = {};
  for (let t = Utilities.parseDate(y + "-01-01", TODAY_TZ, "yyyy-MM-dd"); ; t = new Date(t.getTime() + 86400000)) {
    const d = Utilities.formatDate(t, TODAY_TZ, "yyyy-MM-dd");
    if (d >= today || d.slice(0, 4) !== y) break;
    const a = call[d] || 0, b = kakao[d] || 0;
    const ym = d.slice(0, 7);
    const c = a + b;
    rows.push([d, a, b, c]);
    const m = month[ym] || (month[ym] = [0, 0, 0]);
    m[0] += a; m[1] += b; m[2] += c;
  }
  const added = upsertChojinRows_(ss, rows, false);

  // 채널: 드라이브에 기록이 있는 달은 드라이브, 없는 달은 TODAY '초진콜' 시트
  const driveMonths = {};
  Object.keys(call).forEach(function (d) { driveMonths[d.slice(0, 7)] = 1; });
  const callMap = {};
  rows.forEach(function (r) {
    const d = r[0];
    callMap[d] = driveMonths[d.slice(0, 7)] ? callCh[d] : ch.call[d];
  });
  const dates = rows.map(function (r) { return r[0]; });
  const chDays = upsertChannelRows_(ss, channelRows_(callMap, ch.kakao, dates), dates, false);
  Logger.log("  → 채널 새로 기록 " + chDays + "일");
  Object.keys(month).sort().forEach(function (k) {
    Logger.log("  " + k + "  초진콜 " + month[k][0] + " · 카톡 " + month[k][1] + " · 합계 " + month[k][2] + "");
  });
  Logger.log("  → 새로 기록 " + added + "일 (이미 있던 날짜는 그대로)  " + Math.round((Date.now() - t0) / 1000) + "초");
}

/** 칸의 날짜 → 'yyyy-MM-dd'. 연도 없이 '1/5' 처럼 적힌 칸은 폴더 연도로 본다. */
function dateInYear_(s, y) {
  s = String(s == null ? "" : s).trim();
  if (/\d{4}/.test(s)) return ymd_(s);
  const m = s.match(/^(\d{1,2})\D+(\d{1,2})/);
  return m ? y + "-" + ("0" + m[1]).slice(-2) + "-" + ("0" + m[2]).slice(-2) : "";
}
