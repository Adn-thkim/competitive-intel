"""
test_url_validator.py
---------------------
official_source_resolver_node의 _validate_url() 헤더 수정안 효과 검증 스크립트.

실행:
  python3 test_url_validator.py

결과 해석:
  ✅ 200~399 → 접근 성공 (validated=True 처리 가능)
  ⚠️  403     → 봇 차단 (접근은 됐으나 거부)
  ❌ 기타     → 연결 실패 / 타임아웃
"""

import time
import requests

# ── 테스트 대상 URL ────────────────────────────────────────────────────────────
# 화면에서 403이 발생한 Samsung eSIM URL + 추가 대기업 URL 포함
TARGETS = [
    "https://www.samsung.com/kr/mobile-phones/esim/",
    "https://www.samsung.com/kr/smartphones/galaxy-s/",
    "https://www.sktelecom.com/",
    "https://www.lguplus.com/",
    "https://www.kt.com/",
]

TIMEOUT = (3, 5)

# ── 테스트 케이스 정의 ─────────────────────────────────────────────────────────
CASES = [
    {
        "label": "[현재] Bot UA / HEAD",
        "method": "HEAD",
        "headers": {
            "User-Agent": "Mozilla/5.0 (compatible; OfficialSourceResolverBot/1.0)",
        },
    },
    {
        "label": "[현재] Bot UA / GET",
        "method": "GET",
        "headers": {
            "User-Agent": "Mozilla/5.0 (compatible; OfficialSourceResolverBot/1.0)",
        },
    },
    {
        "label": "[수정안] Chrome UA / HEAD",
        "method": "HEAD",
        "headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        },
    },
    {
        "label": "[수정안] Chrome UA / GET",
        "method": "GET",
        "headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        },
    },
    {
        "label": "[강화안] Chrome UA + Sec-Fetch / GET",
        "method": "GET",
        "headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
            "Connection": "keep-alive",
        },
    },
]


def validate(method: str, url: str, headers: dict) -> tuple:
    try:
        resp = requests.request(
            method, url,
            headers=headers,
            timeout=TIMEOUT,
            allow_redirects=True,
            stream=(method == "GET"),
        )
        return resp.status_code, str(resp.url)
    except requests.exceptions.SSLError:
        return "SSL_ERR", url
    except requests.exceptions.Timeout:
        return "TIMEOUT", url
    except requests.exceptions.ConnectionError as e:
        return "CONN_ERR", str(e)[:60]
    except Exception as e:
        return "ERR", str(e)[:60]


def mark(status) -> str:
    if isinstance(status, int):
        if 200 <= status < 400:
            return "✅"
        if status == 403:
            return "⚠️ "
        return "❌"
    return "❌"


# ── 실행 ──────────────────────────────────────────────────────────────────────
print("\n" + "=" * 100)
print(f"{'URL':<45} {'케이스':<38} {'HTTP':<8} 최종 URL (60자)")
print("=" * 100)

for url in TARGETS:
    print(f"\n🔗 {url}")
    for case in CASES:
        status, final = validate(case["method"], url, case["headers"])
        icon = mark(status)
        short_final = final[:55] if isinstance(final, str) else ""
        print(f"  {icon} {case['label']:<38} {str(status):<8} {short_final}")
        time.sleep(0.3)   # 서버 Rate Limit 회피

print("\n" + "=" * 100)
print("결과 범례: ✅ 접근 성공 (200-399)  /  ⚠️  봇 차단 (403)  /  ❌ 연결 실패·오류")
print("=" * 100 + "\n")
