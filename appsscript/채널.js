/**
 * 유입 채널 기록 — '초진채널' 시트에 날짜별·채널별 건수를 쌓는다.
 *   초진채널: A 날짜 | B 구분('초진콜'·'카톡') | C 채널 | D 건수
 *   초진콜 채널 = '하위 구분' — 드라이브 'N월 전화내역' 은 K, TODAY '초진콜' 시트는 H (K 는 상담예약 날짜)
 *   카톡 채널   = TODAY '카톡' 시트 E열
 * 건수 기준은 초진기록과 같다(초진콜: TODAY 는 F 에 '진', 드라이브는 상담예약 채워짐 / 카톡: G 채워짐).
 */
const CH_TAB = "초진채널";
const CH_CALL_COL  = "H";   // TODAY '초진콜' 시트의 하위 구분
const CH_KAKAO_COL = "E";   // TODAY '카톡' 시트의 채널
const CH_NONE = "미기재";
// TODAY '초진콜' 시트에서 초진콜로 세는 줄 = 날짜(B)가 그날이고 F 에 '진' 이 든 줄.
// TODAY C4(초진기록·KPI 의 초진콜 숫자)와 같은 기준 — 유입 채널·부위 분포 합계가 KPI 와 맞아야 한다.
// (K 상담예약 날짜 기준으로 바꿨더니 2건 모자랐다 — 2026-11-18 확인)
// ※ 다른 파일의 상수를 참조하지 않는 리터럴로 둔다(파일 로드 순서와 무관하게).
const CALL_SPEC = { tab: "초진콜", today: "B", like: ["F", "진"] };
/** 채널 칸 값 정리: 빈 칸·옛 '미분류' → '미기재' */
function chName_(v) {
  const s = String(v == null ? "" : v).trim();
  if (!s || s === "미분류") return CH_NONE;
  return s;
}

/** 시트 하나를 훑어 { 'yyyy-MM-dd': { 채널: 건수 } } — 행 조건은 TODAY_DETAILS 와 같다 */
function channelByDate_(ss, spec, chCol, onlyDate) {
  const sh = ss.getSheetByName(spec.tab);
  if (!sh) throw new Error("'" + spec.tab + "' 시트를 찾을 수 없습니다");
  const nR = sh.getLastRow(), nC = sh.getLastColumn();
  const out = {};
  if (nR < 2) return out;
  const rg = sh.getRange(1, 1, nR, nC);
  const raw = rg.getValues(), disp = rg.getDisplayValues();
  const di = colIdx_(spec.today), ci = colIdx_(chCol);
  for (let r = 1; r < nR; r++) {
    const d = ymd_(disp[r][di]) || ymd_(raw[r][di]);
    if (!d || (onlyDate && d !== onlyDate)) continue;
    if (spec.has && !String(disp[r][colIdx_(spec.has)] || "").trim()) continue;
    if (spec.like && String(disp[r][colIdx_(spec.like[0])] || "").indexOf(spec.like[1]) < 0) continue;
    const ch = chName_(disp[r][ci]);
    const m = out[d] || (out[d] = {});
    m[ch] = (m[ch] || 0) + 1;
  }
  return out;
}

/** { d: {ch: n} } 두 개(초진콜·카톡)를 시트용 줄로 */
function channelRows_(callMap, kakaoMap, dates) {
  const rows = [];
  dates.forEach(function (d) {
    [["초진콜", callMap[d]], ["카톡", kakaoMap[d]]].forEach(function (x) {
      if (!x[1]) return;
      Object.keys(x[1]).forEach(function (ch) { rows.push([d, x[0], ch, x[1][ch]]); });
    });
  });
  return rows;
}

/**
 * 채널 줄을 시트에 합쳐 넣는다. dates 에 든 날짜는 '날짜 단위'로 통째 교체한다
 * (overwrite=false 면 이미 그 날짜 줄이 있는 날은 건드리지 않는다).
 */
function upsertChannelRows_(ss, rows, dates, overwrite) {
  let sh = ss.getSheetByName(CH_TAB);
  if (!sh) {
    sh = ss.insertSheet(CH_TAB);
    sh.getRange(1, 1, 1, 4).setValues([["날짜", "구분", "채널", "건수"]]).setFontWeight("bold");
    sh.setFrozenRows(1);
  }
  const last = sh.getLastRow();
  const old = last >= 2 ? sh.getRange(2, 1, last - 1, 4).getValues() : [];
  const have = {};
  old.forEach(function (r) { const d = ymd_(r[0]); if (d) have[d] = 1; });
  const replace = {};
  dates.forEach(function (d) { if (overwrite || !have[d]) replace[d] = 1; });

  const keep = old.filter(function (r) { const d = ymd_(r[0]); return d && !replace[d]; })
                  .map(function (r) { return [ymd_(r[0]), r[1], r[2], Number(r[3]) || 0]; });
  const add = rows.filter(function (r) { return replace[r[0]]; });
  const all = keep.concat(add).sort(function (a, b) {
    return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? 1 : a[1] > b[1] ? -1 : b[3] - a[3];
  });
  if (last >= 2) sh.getRange(2, 1, last - 1, 4).clearContent();
  if (all.length) sh.getRange(2, 1, all.length, 4).setValues(all);
  sh.getRange("F1").setValue("업데이트: " + Utilities.formatDate(new Date(), TODAY_TZ, "yyyy-MM-dd HH:mm"));
  return Object.keys(replace).length;
}

/**
 * 이번 달 1일~오늘 채널을 TODAY 시트에서 다시 세어 저장(저녁 9시 기록 때 같이 부른다).
 * 지난 날을 나중에 고쳐도 다음 저녁에 반영되고, 달이 바뀐 뒤에도 같은 기준(CALL_SPEC)으로 남는다.
 */
function recordChannelsToday_(ss) {
  const today = Utilities.formatDate(new Date(), TODAY_TZ, "yyyy-MM-dd");
  const dates = [];
  for (let d = 1; d <= +today.slice(8); d++) dates.push(today.slice(0, 8) + ("0" + d).slice(-2));
  const call = channelByDate_(ss, CALL_SPEC, CH_CALL_COL);
  const kakao = channelByDate_(ss, TODAY_DETAILS["카톡예약"], CH_KAKAO_COL);
  upsertChannelRows_(ss, channelRows_(call, kakao, dates), dates, true);
}

/**
 * 한 번 실행: 예전에 TODAY '초진콜' K(상담예약 날짜)를 채널로 잘못 저장한 날을 H(하위 구분)로 다시 센다.
 * 채널 이름이 날짜 모양인 날만 고친다(드라이브로 센 정상 날짜는 그대로).
 */
function REPAIR_초진채널() {
  const ss = SpreadsheetApp.openById(TODAY_ID);
  const sh = ss.getSheetByName(CH_TAB);
  if (!sh || sh.getLastRow() < 2) return;
  const bad = {};
  sh.getRange(2, 1, sh.getLastRow() - 1, 4).getValues().forEach(function (r) {
    const d = ymd_(r[0]);
    if (d && r[1] === "초진콜" && /^\d{4}-\d{2}-\d{2}/.test(String(r[2]).trim())) bad[d] = 1;
  });
  const dates = Object.keys(bad).sort();
  if (!dates.length) { Logger.log("고칠 날짜 없음"); return; }
  const call = channelByDate_(ss, CALL_SPEC, CH_CALL_COL);
  const kakao = channelByDate_(ss, TODAY_DETAILS["카톡예약"], CH_KAKAO_COL);
  upsertChannelRows_(ss, channelRows_(call, kakao, dates), dates, true);
  Logger.log("다시 센 날짜: " + dates.join(", "));
  dates.forEach(function (d) { Logger.log("  " + d + " " + JSON.stringify(call[d] || {})); });
}

/**
 * 웹앱 → [[날짜, 'a'|'b', 채널, 건수], …]  (a=초진콜, b=카톡).
 *   초진콜 이번 달 = TODAY '초진콜' 시트를 바로 센다(저녁 9시 저장 뒤 고친 것도 반영, 부위 분포와 같은 기준)
 *   그 밖 = '초진채널' 시트 저장값. 카톡 오늘은 화면이 실시간 명단으로 채운다.
 */
function getChojinChannels() {
  const ss = SpreadsheetApp.openById(TODAY_ID);
  const cur = Utilities.formatDate(new Date(), TODAY_TZ, "yyyy-MM");
  const sh = ss.getSheetByName(CH_TAB);
  const out = (!sh || sh.getLastRow() < 2) ? [] : sh.getRange(2, 1, sh.getLastRow() - 1, 4).getValues().map(function (r) {
    return [ymd_(r[0]), r[1] === "카톡" ? "b" : "a", chName_(r[2]), Number(r[3]) || 0];
  }).filter(function (r) { return r[0] && !(r[1] === "a" && r[0].slice(0, 7) === cur); });
  const live = channelByDate_(ss, CALL_SPEC, CH_CALL_COL);
  Object.keys(live).forEach(function (d) {
    if (d.slice(0, 7) !== cur) return;
    Object.keys(live[d]).forEach(function (c) { out.push([d, "a", c, live[d][c]]); });
  });
  return out;
}

