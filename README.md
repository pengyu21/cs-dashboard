# 상담 통합 대시보드 — 코드 배포 저장소

CSdashboard.exe(런처)가 실행될 때마다 이 저장소의 `version.json` 을 확인하고,
버전이 다르면 `app/` 안의 코드를 내려받아 실행합니다.

```
version.json      배포 버전 + 파일 목록 + sha256
app/total.py      본체 (GUI · 수집 · 시트)
app/run_dashboard.py  수집기 루프
```

배포는 원본 폴더에서 `python release.py` 로만 합니다 — 이 폴더의 파일을
직접 고치면 다음 배포 때 덮어써집니다.

## 여기에 올리면 안 되는 것

계정·비밀번호·토큰(`secrets.json`), 구글 서비스계정 키(`service_account.json`).
`release.py` 가 배포 전에 검사해서 하나라도 섞여 있으면 중단합니다.
