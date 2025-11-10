<img width="782" height="962" alt="image" src="https://github.com/user-attachments/assets/af8ed093-3582-4d09-8abc-f3dc649d1a4a" /># CHZZK-Chat-Monitor
방송인 혹은 유튜버 혹은 키리누커를 위한 프로그램 일지도 모르는 것 Ver.1.0.0
치지직 라이브 방송의 채팅을 실시간으로 수집하고, 설정한 키워드의 발생과 방송 진행 시간을 함께 기록해 주는 모니터링 도구입니다.
예를 들어 e스포츠 중계팀에서 “펜타킬” 같은 특정 키워드가 나오면 즉시 하이라이트 후보로 표시하고 싶을 때 이 프로그램을 실행합니다. 방송 주소에서 채널 ID를 입력하고, 브라우저에서 복사한 `NID_AUT`, `NID_SES` 쿠키 값을 채워 넣은 뒤 키워드 설정란에 `펜타킬:2:120, 역전승:1:300`처럼 입력하고 `시작` 버튼을 누릅니다. 그러면 방송 중 채팅이나 후원 메시지에서 해당 키워드가 2분 안에 2번 이상 감지될 때마다 `keyword_times.log`에 기록되고, 동시에 진행 시간이 `01:23:45`처럼 함께 남아 나중에 편집자가 손쉽게 타임라인을 추적할 수 있습니다.
<img width="782" height="962" alt="image" src="https://github.com/user-attachments/assets/c2f3a094-7049-405b-a8d2-bcde84b814ae" />
