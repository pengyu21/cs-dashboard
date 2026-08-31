# -*- coding: utf-8 -*-
"""
상담 통합 대시보드 러너
------------------------
브라우저 1개 + 채널별 탭 상주(BrowserHub) → 3분마다 순회 수집 →
채널별 구글시트 탭에 '미상담(신규상담) 현황' 기록.

안전장치(총정리):
  1) 프로필 1개 공유          5) 6시간마다 브라우저 재시작(메모리)
  2) 드라이버 접근 락          6) 스크랩 실패 시 시트 미변경(대시보드 보호)
  3) 닫힌 탭 자동 재생성
  4) 페이지 로드 타임아웃

실행:
  python run_dashboard.py          # 3분 간격 무한 루프
  python run_dashboard.py once     # 1회만 수집(테스트)
  python run_dashboard.py check    # 화면 점검만(시트·알림 안 건드림)

※ 이 파일은 런처가 깃허브 raw 에서 그대로 받아간다(version.json 의 files 목록).
  받다가 하나라도 실패하면 런처는 업데이트를 통째로 취소하고 조용히 구버전으로
  실행한다 — 실제로 v1.0.24 배포 때 이 파일만 raw 의 main 경로에서 HTTP 400 이
  나서(같은 파일을 커밋 SHA 경로로는 정상 수신) 각 PC 가 v1.0.23 에 머물렀다.
  같은 증상이면 파일 내용을 바꿔 새 blob 을 만들고 재배포하면 캐시가 갱신된다.
  배포 뒤에는 반드시: python release.py --verify
"""
from __future__ import annotations

import sys
import time

from total import (BrowserHub, classify_error, claim_sheet_lock, refresh_sheet_lock,
                   release_sheet_lock, start_heartbeat, write_channel_sheet,
                   write_dashboard, _load_drift_state, _diff_item,
                   _console_safe,
                   GangnamUnniChannel, BabitalkChannel, NaverMapChannel,
                   OnlineConsultChannel, OnlineBookingChannel, KakaoTalkChannel)

INTERVAL_SEC = 120      # 순회 주기(초). 2분. 채널 사이트 부하·차단 위험 때문에 60초 밑은 비권장

# 수집 대상 채널 (검증된 것만 등록 → 순차 추가)
CHANNELS = [
    GangnamUnniChannel(),
    BabitalkChannel(),
    NaverMapChannel(),
    OnlineConsultChannel(),
    OnlineBookingChannel(),
    KakaoTalkChannel(),
]


def one_cycle(hub: BrowserHub) -> None:
    results = []                             # (채널, items|CollectError) — 대시보드 집계용
    for ch in CHANNELS:
        try:
            items = hub.collect(ch)          # 실패 시 예외 → 시트 안 건드림
        except Exception as e:
            err = classify_error(e)          # 사유를 시트/GUI/웹앱까지 그대로 전달
            print(f"[{ch.name}] 수집 실패({err.kind}) → 시트 유지: {err.detail}")
            results.append((ch, err))
            continue
        try:
            n = write_channel_sheet(ch, items)
            print(f"[{ch.name}] {n}건 기록 · {time.strftime('%H:%M:%S')}")
        except Exception as e:
            # 수집은 됐으나 시트 기록만 실패 — 집계는 살리고 사유만 남긴다
            print(f"[{ch.name}] 시트 기록 오류: {classify_error(e).detail}")
        results.append((ch, items))

    # 대시보드 통합 집계 + 업데이트 시각(하트비트)
    try:
        write_dashboard(results)
        print(f"[대시보드] 집계 완료 · {time.strftime('%H:%M:%S')}")
    except Exception as e:
        print(f"[대시보드] 집계 오류: {classify_error(e).detail}")


def check_pages(hub: BrowserHub) -> None:
    """사이트 화면이 코드가 기대하는 모양 그대로인지 지금 확인해서 보여준다.

    수집은 하되 시트·텔레그램은 건드리지 않는다. '이상하다' 싶을 때 사람이
    직접 돌려보는 점검 명령. 평소에는 러너가 매 사이클 자동으로 같은 비교를
    해서 달라진 것만 텔레그램으로 알린다(total.py check_page_changes).

    ※ 출력은 전부 _console_safe 를 거친다 — 지문 값은 사이트에서 읽어온
      글자라 윈도우 콘솔(cp949)이 못 찍는 문자가 섞여 있을 수 있다.
    """
    def p(text: str = "") -> None:
        print(_console_safe(text))

    saved = (_load_drift_state()).get("sig", {})
    p()
    p("=" * 60)
    p("화면 점검 · 기준(마지막 저장 지문) 대비 현재 화면")
    p("=" * 60)
    for ch in CHANNELS:
        try:
            items = hub.collect(ch)
        except Exception as e:
            p(f"\n[{ch.name}] 수집 실패 · {classify_error(e).detail}")
            continue
        sig = getattr(ch, "signature", None) or {}
        if not sig:
            p(f"\n[{ch.name}] 신규 {len(items)}건 · (화면 지문 미등록 채널)")
            continue
        old = saved.get(ch.key) or {}
        diffs = []
        for k, v in sig.items():
            diffs += _diff_item(k, old[k], v) if k in old else []
        p(f"\n[{ch.name}] 신규 {len(items)}건 · 표에 보인 행 "
          f"{len(getattr(ch, 'row_keys', []) or [])}개")
        for k, v in sig.items():
            shown = ", ".join(map(str, v)) if isinstance(v, list) else str(v)
            p(f"   - {k}: {shown[:150]}")
        if diffs:
            p("   [달라짐] 기준과 다른 점:")
            for d in diffs:
                p("     " + d)
        elif old:
            p("   [OK] 기준과 동일")
        else:
            p("   (기준 없음 · 수집기가 한 사이클 돌면 기준이 만들어집니다)")
    p()
    p("=" * 60)
    p("※ 시트·텔레그램은 건드리지 않았습니다.")


def main() -> None:
    once = "once" in sys.argv[1:]

    # 화면 점검만: 잠금·하트비트·시트 없이 브라우저만 띄워 비교하고 끝낸다
    if "check" in sys.argv[1:]:
        hub = BrowserHub(headless=False).start()
        try:
            check_pages(hub)
        finally:
            hub.quit()
        return

    # 다중 PC 중복 실행 차단 — 두 대가 같은 시트를 쓰면 서로를 '실패'로 덮어쓴다
    ok, owner = claim_sheet_lock()
    if not ok:
        print(f"[중단] 다른 PC에서 수집기가 실행 중입니다: {owner}\n"
              f"       그쪽을 먼저 종료하거나, 하트비트가 끊길 때까지 기다리세요.")
        return
    print(f"[잠금] 수집기 잠금 획득: {owner}")

    # 하트비트를 수집 사이클과 분리해 45초마다 백그라운드로 찍는다.
    # → 순회가 오래 걸려도 다른 PC 는 '살아있음'을 정확히 알고(오인 종료 방지),
    #   이 수집기가 죽으면 3분 안에 다른 PC 가 인계받는다(구 7분).
    stop_beat = start_heartbeat() if not once else None

    hub = BrowserHub(headless=False).start()
    try:
        while True:
            hub.maybe_recycle()
            one_cycle(hub)
            if once:
                break
            # 소유권 확인(하트비트 자체는 백그라운드 스레드가 갱신).
            # 시트 API 오류(쿼터 등)로 실패해도 수집은 계속한다 —
            # 여기서 예외가 올라가면 수집기가 통째로 죽는다(실제로 그랬음).
            try:
                ok, owner = refresh_sheet_lock()
                if not ok:
                    print(f"[중단] 잠금을 다른 PC가 가져갔습니다: {owner}")
                    break
            except Exception as e:
                print(f"[경고] 잠금 갱신 실패(계속 진행): {classify_error(e).detail}")
            time.sleep(INTERVAL_SEC)
    except KeyboardInterrupt:
        print("\n[종료] 사용자 중단")
    finally:
        if stop_beat:
            stop_beat.set()             # 백그라운드 하트비트 중단
        hub.quit()
        release_sheet_lock()


if __name__ == "__main__":
    main()
