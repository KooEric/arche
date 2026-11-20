# Arche 에이전트 평가

`skills/arche-eval`을 `skills/arche`와 같은 스킬 디렉터리에 설치합니다. 저장소에서 직접 실행할 때는 루트를 작업 디렉터리로 사용합니다.

```sh
python3 -B skills/arche-eval/scripts/evaluate.py list
python3 -B skills/arche-eval/scripts/evaluate.py run --case conjunction --output /tmp/arche-study
python3 -B skills/arche-eval/scripts/evaluate.py score --run /tmp/arche-study
python3 -B skills/arche-eval/scripts/evaluate.py score --run tests/fixtures/repair-translation-demo
python3 -B skills/arche-eval/scripts/evaluate.py report --runs /tmp/arche-study tests/fixtures/repair-translation-demo --output /tmp/arche-comparison
```

수령 화면의 행동 검사에는 Python Playwright와 Chromium이 필요합니다. 공개 재생 예제는 사람이 만든 합성 자료입니다. 모델 평가도 번들 합성 과제에서 실행합니다.

`review.py sample --comparison /tmp/arche-comparison/comparison.json --count 2 --seed 42 --output /tmp/review`로 검토 표본을 만듭니다. 검토자는 자동 점수와 모델 이름이 가려진 `packet.json`을 봅니다. 응답 형식은 다음과 같습니다.

```json
{"schema_version": 1, "reviewers": ["reviewer-a"], "responses": [{"id": "R0001", "reviewer": "reviewer-a", "correct": true}]}
```

`review.py grade --sample /tmp/review --responses RESPONSES.json --output REVIEW_RESULT.json`으로 미응답과 자동 점수·검토자 간 일치율을 확인합니다.

개발 검증에서 GPT‑6.1 Terra의 이해 과제 5개를 on/off 조건에서 각각 두 번 실행해 20/20회 통과했습니다. 수정 과제 2개를 프롬프트 두 조건으로 각각 두 번 실행해 7/8회 통과했습니다. 한 실행은 기능을 고쳤지만 무관한 바이트코드 파일을 추가했습니다. 이 표본에서는 스킬 on/off의 품질 차이를 확인하지 못했습니다.

Codex에서 실행을 확인했습니다. Claude Code와 Cursor의 인증 후 실행, 외부 참가자의 독립 재현, 실제 레포 감사 정확도는 검증이 남아 있습니다.
