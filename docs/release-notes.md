# Arkhion 0.1 공개 후보

설치 단위는 `skills/arche` 전체이며 MIT 라이선스를 함께 배포한다. 새 Codex 세션에서 `$arche 레포 분석`을 입력하면 소스 위치와 오프라인 HTML을 받는다. 후보 ZIP을 풀면 루트의 `LICENSE`와 `skills/arche`가 나온다. 압축을 푼 폴더에서 README의 설치 명령을 사용할 수 있다. 설치 절차와 수동 실행은 [설치 안내](installation-and-reuse.md)에 있다.

- Python·Go·Dart·TypeScript·JavaScript 파일의 import와 역방향 의존 후보를 탐색한다. UI 투어는 선택한 화면의 문구·동작·표시 조건을 파일 및 줄 범위와 연결한다.
- Git 커밋 비교, 순환·반복 import 감사, JSON·Markdown·Mermaid 투어 내보내기를 제공한다.
- 로컬 사례와 정답 데이터를 사용해 실제 모델 실행을 재채점한다. [실험 실행법](../evals/experiments/README.md)를 참고한다.

Codex에서 실제 활성화를 확인했다. Claude Code·Cursor의 인증 후 활성화, 배포된 앱의 런타임 호출 관계, 외부 Harness 서비스 연동은 미검증이다.
