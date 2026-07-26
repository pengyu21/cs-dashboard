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
"""
from __future__ import annotations

import sys
import time

from total import (BrowserHub, classify_error, claim_sheet_lock, refresh_sheet_lock,
                   release_sheet_lock, start_heartbeat, write_channel_sheet,
                   write_dashboard,
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


def main() -> None:
    once = "once" in sys.argv[1:]

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
