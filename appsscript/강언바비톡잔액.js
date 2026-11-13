/** 잔액 부족 알람 — '강남언니'·'바비톡' 시트 E1 을 감시해 기준 이하면 메일 발송
 *  기준 이하가 계속되면 '하루 한 번' 계속 알린다(회복될 때까지 매일 발송). */

var BAL_SHEETS  = ['강남언니', '바비톡','네이버지도'];   // 감시할 시트 이름
var BAL_CELL    = 'E1';                    // 잔액이 적힌 셀
var BAL_MIN     = 500000;                  // 이 값 이하면 알람 (원)
var BAL_MAIL_TO = 'alert@example.com';

/** 12,345 → '12,345원' (로그·메일 공용) */
function balWon_(n) {
  return Number(n).toLocaleString('ko-KR') + '원';
}

/** 오늘 날짜 'yyyy-MM-dd' — 하루 1회 발송 판정의 기준 */
function balToday_() {
  var tz = SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone() || 'Asia/Seoul';
  return Utilities.formatDate(new Date(), tz, 'yyyy-MM-dd');
}

/**
 * E1 값을 숫자로 바꾼다.
 * 셀이 진짜 숫자(서식만 '원')일 수도, "500,000원" 같은 문자열일 수도 있어 둘 다 받는다.
 * 숫자로 볼 수 없으면 null → 호출부에서 '판정 불가'로 건너뛴다(오알람 방지).
 */
function balParse_(sheet) {
  var cell = sheet.getRange(BAL_CELL);
  var raw = cell.getValue();
  var shown = String(cell.getDisplayValue() || '').trim();

  if (typeof raw === 'number') {              // 숫자 + '원' 표시서식인 경우
    Logger.log('      숫자셀 "' + shown + '" → ' + raw);
    return raw;
  }

  // 문자열: "잔액 1,234,500 원", "1,234,500원" 등 → 숫자·마이너스·소수점만 남긴다
  var text = shown || String(raw || '').trim();
  if (!text) {
    Logger.log('      ⚠ ' + BAL_CELL + ' 이 비어 있음 — 건너뜀');
    return null;
  }
  var digits = text.replace(/[^0-9.\-]/g, '');
  if (!digits || isNaN(Number(digits))) {
    Logger.log('      ⚠ 숫자로 해석 불가: "' + text + '" — 건너뜀');
    return null;
  }
  Logger.log('      문자셀 "' + text + '" → ' + Number(digits));
  return Number(digits);
}

/** 트리거로 돌릴 본체 */
function checkBalances() {
  var started = new Date().getTime();
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var props = PropertiesService.getScriptProperties();
  var today = balToday_();
  var low = [];
  var okCount = 0;
  var skipCount = 0;

  Logger.log('════════ 잔액 점검 시작 (' + today + ') ════════');
  Logger.log('기준 : ' + balWon_(BAL_MIN) + ' 이하 | 셀 : ' + BAL_CELL +
    ' | 수신 : ' + BAL_MAIL_TO + ' | 발송주기 : 하루 1회');

  for (var i = 0; i < BAL_SHEETS.length; i++) {
    var name = BAL_SHEETS[i];
    var seq = '[' + (i + 1) + '/' + BAL_SHEETS.length + '] ' + name;

    var sheet = ss.getSheetByName(name);
    if (!sheet) {                               // 시트 이름이 바뀌었으면 조용히 건너뜀
      skipCount++;
      Logger.log(seq + ' ❌ 시트 없음 — 건너뜀');
      continue;
    }

    Logger.log(seq + ' ▶ ' + BAL_CELL + ' 확인 중…');
    var bal = balParse_(sheet);
    if (bal === null) { skipCount++; continue; }   // 값이 비었거나 해석 불가 → 알람 안 함

    var key = 'balAlerted_' + name;               // 마지막으로 알린 '날짜'를 기억
    var lastSent = props.getProperty(key);        // 'yyyy-MM-dd' 또는 null

    if (bal <= BAL_MIN) {
      if (lastSent === today) {                   // 오늘 이미 보냈으면 내일 다시
        Logger.log('      🔕 ' + balWon_(bal) + ' — 기준 이하지만 오늘(' + today +
          ') 이미 발송함(내일 다시 알림)');
      } else {                                    // 오늘 아직 안 보냈으면 발송 대상
        low.push({ name: name, bal: bal, prev: lastSent });
        props.setProperty(key, today);
        Logger.log('      🚨 ' + balWon_(bal) + ' — 기준 이하! 메일 대상에 추가' +
          (lastSent ? ' (직전 발송 ' + lastSent + ')' : ' (첫 알림)'));
      }
    } else {
      okCount++;
      Logger.log('      ✅ ' + balWon_(bal) + ' — 여유 ' + balWon_(bal - BAL_MIN));
      if (lastSent) {
        props.deleteProperty(key);                // 충전되어 회복 → 다음에 떨어지면 즉시 재알람
        Logger.log('      ↻ 충전 확인 — 알림 기록 초기화(다시 떨어지면 즉시 재알람)');
      }
    }
  }

  var sec = Math.round((new Date().getTime() - started) / 1000);

  if (!low.length) {
    Logger.log('════════ 완료 (' + sec + '초) ════════');
    Logger.log('정상 ' + okCount + ' / 건너뜀 ' + skipCount + ' — 보낼 메일 없음');
    return;
  }

  var subj = '[잔액 부족] ' + low.map(function (c) { return c.name; }).join(', ') +
    ' (' + today + ')';                           // 날짜를 넣어 매일 새 메일로 보이게
  var body = low.map(function (c) {
    return '• ' + c.name + ' : ' + balWon_(c.bal) + '  (기준 ' + balWon_(BAL_MIN) + ' 이하)' +
      (c.prev ? '  ※ ' + c.prev + '부터 계속 부족' : '');
  }).join('\n') + '\n\n충전 전까지 매일 1회 안내됩니다.\n시트: ' + ss.getUrl();

  try {
    MailApp.sendEmail(BAL_MAIL_TO, subj, body);
    Logger.log('📧 메일 발송 완료 → ' + BAL_MAIL_TO + ' | ' + subj);
    Logger.log('      남은 일일 발송 한도 ' + MailApp.getRemainingDailyQuota() + '통');
  } catch (e) {
    // 발송에 실패했는데 '오늘 보냄'으로 남으면 오늘치를 못 받는다 → 기록을 되돌린다
    low.forEach(function (c) {
      if (c.prev) { props.setProperty('balAlerted_' + c.name, c.prev); }
      else { props.deleteProperty('balAlerted_' + c.name); }
    });
    Logger.log('❌ 메일 발송 실패: ' + e + ' — 발송 기록 되돌림(다음 실행에서 재시도)');
  }

  Logger.log('════════ 완료 (' + sec + '초) ════════');
  Logger.log('정상 ' + okCount + ' / 부족 ' + low.length + ' / 건너뜀 ' + skipCount);
}

/** 한 번만 실행 — 1시간마다 checkBalances 를 돌리는 트리거를 건다(중복 생성 방지 포함) */
function installBalanceTrigger() {
  var removed = 0;
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'checkBalances') { ScriptApp.deleteTrigger(t); removed++; }
  });
  ScriptApp.newTrigger('checkBalances').timeBased().everyHours(1).create();
  Logger.log('⏰ 트리거 설정 완료 — 1시간마다 checkBalances 실행' +
    (removed ? ' (기존 ' + removed + '개 정리)' : ''));
}

/** (선택) 알림 기록 초기화 — 테스트 중 바로 다시 메일을 받아보고 싶을 때 실행 */
function resetBalanceAlerts() {
  var props = PropertiesService.getScriptProperties();
  BAL_SHEETS.forEach(function (name) { props.deleteProperty('balAlerted_' + name); });
  Logger.log('🧹 알림 기록 초기화 완료 — 다음 실행 때 다시 발송됩니다');
}