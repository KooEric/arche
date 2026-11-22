"""Render map.html from an atlas. The viewer reads one JSON block; no source text is embedded."""
import json
from html import escape
from string import Template

from .report import _csp

_TEXT = {
    "map": ("Map", "지도"), "flows": ("Flows", "흐름"), "list": ("List", "목록"),
    "search": ("Search parts, files, routes  /", "부품·파일·라우트 검색  /"), "theme": ("Toggle dark mode", "다크 모드 전환"),
    "files": ("files", "개 파일"), "lines": ("lines", "줄"), "symbols": ("symbols", "심볼"), "parts": ("parts", "개 부품"),
    "files_title": ("Files", "파일"), "part": ("Part", "부품"), "file": ("File", "파일"), "routes": ("Endpoints", "엔드포인트"),
    "uses": ("Uses", "여기가 쓰는 곳"), "usedby": ("Used by", "여기를 쓰는 곳"),
    "evidence": ("Evidence", "근거"), "open": ("Open in the report", "리포트에서 열기"), "enter": ("Open this part", "이 부품 열기"),
    "report": ("Open the full report", "전체 리포트 열기"), "onmap": ("Show on the map", "지도에서 보기"),
    "prev": ("Previous", "이전"), "next": ("Next", "다음"),
    "verified": ("Static import the scan resolved", "스캔이 해석한 정적 import"),
    "inferred": ("Inferred: HTTP method and path match, or an inferred import", "추론: HTTP 메서드·경로 일치 또는 추론된 import"),
    "cited": ("Cited by the map author; the line exists, open it to judge the claim", "지도 작성자가 인용함. 줄의 존재만 확인했으니 열어서 판단하세요"),
    "hint": ("Select a card for details. Select it again to open it.", "카드를 선택하면 상세가 보이고, 다시 선택하면 안으로 들어갑니다."),
    "unnamed": ("Not named yet", "이름 미확정"), "undescribed": ("No description", "설명 없음"), "nosummary": ("No summary", "요약 없음"),
    "unassigned": ("Files in no part", "부품에 속하지 않은 파일"), "described": ("described", "설명됨"),
    "changed": ("changed", "변경됨"), "audit": ("audit", "감사 후보"),
    "auto": ("This map was drawn from folders. Write map.json to name the parts.", "폴더를 기준으로 자동으로 그린 지도입니다. map.json을 쓰면 부품에 이름이 붙습니다."),
    "empty": ("Nothing to show at this level.", "이 단계에 표시할 것이 없습니다."), "noflows": ("No flows in this map.", "이 지도에는 흐름이 없습니다."),
    "nohits": ("No matches.", "일치하는 항목이 없습니다."), "listhint": ("Every part and file as plain text.", "모든 부품과 파일을 글로 나열합니다."),
    "screens": ("Screens", "화면"), "changes": ("Changes", "변경"), "findings": ("Audit", "감사"),
    "edit": ("Open in editor", "편집기에서 열기"), "zoomin": ("Zoom in", "확대"), "zoomout": ("Zoom out", "축소"), "fit": ("Fit", "맞춤"),
    "findingshint": ("Review candidates, not confirmed defects. Open the cited line before changing anything.",
                     "확정된 결함이 아니라 검토 후보입니다. 수정 전에 인용된 줄을 열어 확인하세요."),
    "changeshint": ("Files changed in the compared Git range, grouped by part.", "비교한 Git 범위에서 바뀐 파일을 부품별로 묶었습니다."),
    "nopart": ("No part", "부품 없음"),
    "rule_unused-private-symbol": ("Unused private symbol", "쓰이지 않는 비공개 심볼"),
    "rule_unused-internal-export": ("Unused internal export", "쓰이지 않는 내부 export"),
    "rule_duplicate-block": ("Identical function bodies", "본문이 같은 함수"),
    "rule_isolated-module": ("Isolated module", "고립된 모듈"),
    "rule_unused-private-member": ("Unused private member", "쓰이지 않는 private 멤버"),
    "rule_unused-nonpublic-type": ("Unused non-public type", "쓰이지 않는 비공개 타입"),
    "kind_ui": ("screen", "화면"), "kind_service": ("service", "서비스"), "kind_worker": ("worker", "워커"), "kind_library": ("library", "라이브러리"),
    "kind_data": ("data", "데이터"), "kind_test": ("test", "테스트"), "kind_script": ("script", "스크립트"), "kind_config": ("config", "설정"),
    "kind_external": ("external", "외부"),
}


# {path} is the absolute file path and {line} the first cited line.
EDITORS = {
    "vscode": "vscode://file/{path}:{line}",
    "cursor": "cursor://file/{path}:{line}",
    "zed": "zed://file/{path}:{line}",
    "idea": "idea://open?file={path}&line={line}",
}


def render_map(template, atlas, lang="en", report="report.html", editor=None):
    pick = 1 if lang == "ko" else 0
    data = {**atlas, "report": report, "t": {key: pair[pick] for key, pair in _TEXT.items()}}
    if editor:
        data["editor"] = {"url": EDITORS[editor["name"]], "root": editor["root"].rstrip("/")}
    data["stats"] = {key: value for key, value in atlas["stats"].items() if key != "unassigned"}
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return Template(template).substitute(
        csp=_csp(template), lang=escape(lang, quote=True), title=escape(atlas["project"]["name"]), data=payload,
        noscript=escape(("지도를 보려면 JavaScript가 필요합니다. 리포트는 " if pick else "The map needs JavaScript. The report is ") + report))
