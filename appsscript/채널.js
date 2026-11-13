/**
 * 유입 채널 기록 — '초진채널' 시트에 날짜별·채널별 건수를 쌓는다.
 *   초진채널: A 날짜 | B 구분('초진콜'·'카톡') | C 채널 | D 건수
 *   초진콜 채널 = '하위 구분'(K) — 드라이브 'N월 전화내역' 과 TODAY '초진콜' 시트 모두 같은 열
 *   카톡 채널   = TODAY '카톡' 시트 E열
 * 건수 기준은 초진기록과 같다(초진콜: TODAY 는 F 에 '진', 드라이브는 상담예약 채워짐 / 카톡: G 채워짐).
 */
const CH_TAB = "초진채널";
const CH_CALL_COL  = "K";   // TODAY '초진콜' 시트의 하위 구분
const CH_KAKAO_COL = "E";   // TODAY '카톡' 시트의 채널
const CH_NONE = "미분류";

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
    const ch = String(disp[r][ci] || "").trim() || CH_NONE;
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

/** 오늘 채널을 TODAY 시트에서 세어 저장(저녁 9시 기록 때 같이 부른다) */
function recordChannelsToday_(ss) {
  const today = Utilities.formatDate(new Date(), TODAY_TZ, "yyyy-MM-dd");
  const call = channelByDate_(ss, TODAY_DETAILS["초진콜"], CH_CALL_COL, today);
  const kakao = channelByDate_(ss, TODAY_DETAILS["카톡예약"], CH_KAKAO_COL, today);
  upsertChannelRows_(ss, channelRows_(call, kakao, [today]), [today], true);
}

/** 웹앱 → [[날짜, 'a'|'b', 채널, 건수], …]  (a=초진콜, b=카톡). 오늘은 화면이 실시간 명단으로 채운다. */
function getChojinChannels() {
  const sh = SpreadsheetApp.openById(TODAY_ID).getSheetByName(CH_TAB);
  if (!sh || sh.getLastRow() < 2) return [];
  return sh.getRange(2, 1, sh.getLastRow() - 1, 4).getValues().map(function (r) {
    return [ymd_(r[0]), r[1] === "카톡" ? "b" : "a", String(r[2]), Number(r[3]) || 0];
  }).filter(function (r) { return r[0]; });
}
