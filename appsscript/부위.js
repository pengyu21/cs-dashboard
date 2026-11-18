/**
 * 부위 분포 — TODAY 문서 '초진콜'·'카톡' 시트에서 날짜별·부위별 건수를 센다(저장 안 하고 바로 읽음).
 *   부위 열 = 머리글(앞 6줄)에 '부위'가 들어간 칸. 못 찾으면 아래 기본 열.
 *   행 조건은 초진기록과 같다(TODAY_DETAILS: 초진콜은 F 에 '진', 카톡은 G 채워짐).
 *   한 줄 = 1건. '골절+코재수술' 처럼 여러 부위가 적혀도 나누지 않고 적힌 그대로 한 항목으로 센다.
 */
const PART_FALLBACK = { "초진콜": "I", "카톡예약": "" };   // '' = 머리글에서 못 찾으면 안 셈
const PART_CACHE_SEC = 60;

function partCol_(disp, fallback) {
  for (let r = 0; r < Math.min(6, disp.length); r++) {
    for (let c = 0; c < disp[r].length; c++) {
      if (String(disp[r][c]).replace(/\s/g, "").indexOf("부위") > -1) return { row: r, col: c };
    }
  }
  return fallback ? { row: 0, col: colIdx_(fallback) } : null;
}

/** 시트 하나 → [[날짜, 부위, 건수], …] */
function partRows_(ss, key) {
  const spec = TODAY_DETAILS[key];
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
    // 나누지 않는다. 기호 앞뒤 띄어쓰기만 맞춰 '골절 + 코재수술' = '골절+코재수술'
    const p = String(disp[r][pc.col] || "").replace(/\s*([,，\/+·&])\s*/g, "$1")
                .replace(/\s+/g, " ").trim() || CH_NONE;
    const k = d + "\u0001" + p;
    cnt[k] = (cnt[k] || 0) + 1;
  }
  return Object.keys(cnt).map(function (k) {
    const x = k.split("\u0001");
    return [x[0], x[1], cnt[k]];
  });
}

/** 웹앱 → { rows: [[날짜, 'a'|'b', 부위, 건수], …], err: {a: '', b: ''} } (a=초진콜, b=카톡) */
function getChojinParts() {
  const cache = CacheService.getScriptCache();
  const hit = cache.get("chojinParts");
  if (hit) return JSON.parse(hit);
  const ss = SpreadsheetApp.openById(TODAY_ID);
  const out = { rows: [], err: { a: "", b: "" } };
  [["a", "초진콜"], ["b", "카톡예약"]].forEach(function (x) {
    try {
      partRows_(ss, x[1]).forEach(function (r) { out.rows.push([r[0], x[0], r[1], r[2]]); });
    } catch (e) {
      out.err[x[0]] = String((e && e.message) || e);
    }
  });
  try { cache.put("chojinParts", JSON.stringify(out), PART_CACHE_SEC); } catch (e) {}
  return out;
}
