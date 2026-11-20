# 공개 개발 절차

공개 코드의 기준은 [KooEric/arche의 main](https://github.com/KooEric/arche/tree/main)입니다. 외부 기여자는 포크에서 작업하고 이 저장소의 main으로 PR을 열 수 있습니다.

1. 공개 main의 최신 커밋에서 작업 브랜치를 만듭니다.
2. 수정할 동작과 재현 입력을 정합니다. 다른 레포에서 찾은 문제는 공개 가능한 최소 합성 예제로 재현합니다. 문서에는 이 저장소 안의 상대 경로를 사용합니다.
3. PR의 전체 변경 파일을 검토합니다. 내부 체크아웃의 이력, 사용자별 절대 경로, 실행 trace, 비밀 값이 포함된 자료는 공개 대상에서 제외합니다. 보고서 이미지는 합성 입력에서 다시 생성합니다.
4. 저장소 루트에서 다음 검사를 실행합니다.

```sh
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
python3 evals/score.py
git diff --check
```

PR 설명에는 사용자에게 달라지는 점, 실행한 검사와 확인 범위를 기록합니다. HTML을 바꾸면 실제 파일을 오프라인으로 열고 관련 화면과 근거 링크를 확인합니다. CI가 지원하는 Python 버전에서 통과한 뒤 main에 반영합니다.
