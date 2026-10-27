# weather-data

날씨 쇼츠용 원자료 자동 수집 저장소. 매일 06:17 KST에 GitHub Actions가 실행돼서 `data/latest.json` 을 갱신해.

- 기상청 단기예보 / 에어코리아 미세먼지 예보 (공공데이터포털, 시크릿 `DATA_GO_KR_KEY` 필요)
- ECMWF IFS 공개 데이터 (CC BY 4.0, © ECMWF)
- NOAA GFS 0.25° (미국 정부 공개 자료)

수동 실행: Actions 탭 → fetch-weather → Run workflow
