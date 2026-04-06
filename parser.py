"""한국 은행 SMS 알림 파싱."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class ParsedTransaction:
    bank: str
    tx_type: str          # '입금' or '출금'
    amount: int           # 원 단위 정수
    balance: Optional[int]
    description: Optional[str]
    date_str: Optional[str]   # 'YYYY-MM-DD' if parseable
    time_str: Optional[str]   # 'HH:MM' if parseable


def _parse_amount(s: str) -> int:
    return int(s.replace(",", "").replace(".", ""))


# ---- 날짜/시간 추출 ----

_DATE_RE = re.compile(r"(\d{2})/(\d{2})\b")            # MM/DD or YY/MM
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")  # HH:MM or HH:MM:SS
_DATE_FULL_RE = re.compile(r"(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})")  # YYYY-MM-DD


def _extract_date(text: str) -> Optional[str]:
    m = _DATE_FULL_RE.search(text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = _DATE_RE.search(text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        today = date.today()
        if 1 <= a <= 12 and 1 <= b <= 31:
            return f"{today.year}-{a:02d}-{b:02d}"
    return None


def _extract_time(text: str) -> Optional[str]:
    m = _TIME_RE.search(text)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


# ---- 메인 파싱 패턴 ----

# 일반 패턴: [은행] 입금/출금 금액원 ...
_MAIN_RE = re.compile(
    r"\[(?P<bank>[^\]]+)\]\s*"
    r"(?P<type>입금|출금)\s*"
    r"(?P<amount>[\d,]+)\s*원"
    r"(?:\s*잔액\s*(?P<balance>[\d,]+)\s*원)?"
    r"(?:\s*(?P<desc>.+))?"
)

# 카드 패턴: [은행] 카드승인/카드결제/체크 금액원 가맹점
_CARD_RE = re.compile(
    r"\[(?P<bank>[^\]]+)\]\s*"
    r"(?:카드승인|카드결제|체크|체크카드)\s*"
    r"(?P<amount>[\d,]+)\s*원\s*"
    r"(?P<desc>.+)?"
)

# 이체 패턴: [은행] 이체 금액원
_TRANSFER_RE = re.compile(
    r"\[(?P<bank>[^\]]+)\]\s*"
    r"(?P<type>입금이체|출금이체|이체)\s*"
    r"(?P<amount>[\d,]+)\s*원"
    r"(?:\s*잔액\s*(?P<balance>[\d,]+)\s*원)?"
    r"(?:\s*(?P<desc>.+))?"
)

# 괄호 없이 은행명 시작: KB국민 입금 ...
_NO_BRACKET_RE = re.compile(
    r"(?P<bank>KB국민|신한|하나|우리|NH농협|카카오뱅크|토스|IBK기업|SC제일|케이뱅크|수협|광주|전북|제주|대구|부산|경남|우체국)\s*"
    r"(?P<type>입금|출금)\s*"
    r"(?P<amount>[\d,]+)\s*원"
    r"(?:\s*잔액\s*(?P<balance>[\d,]+)\s*원)?"
    r"(?:\s*(?P<desc>.+))?"
)


def parse_sms(text: str) -> Optional[ParsedTransaction]:
    """한국 은행 SMS를 파싱. 인식 실패 시 None."""
    text = text.strip()
    if not text:
        return None

    date_str = _extract_date(text)
    time_str = _extract_time(text)

    # 이체 패턴 먼저 (입금이체/출금이체 구분)
    m = _TRANSFER_RE.search(text)
    if m:
        raw_type = m.group("type")
        tx_type = "입금" if "입금" in raw_type else "출금"
        return ParsedTransaction(
            bank=m.group("bank"),
            tx_type=tx_type,
            amount=_parse_amount(m.group("amount")),
            balance=_parse_amount(m.group("balance")) if m.group("balance") else None,
            description=(m.group("desc") or "").strip() or None,
            date_str=date_str,
            time_str=time_str,
        )

    # 일반 패턴
    m = _MAIN_RE.search(text)
    if m:
        return ParsedTransaction(
            bank=m.group("bank"),
            tx_type=m.group("type"),
            amount=_parse_amount(m.group("amount")),
            balance=_parse_amount(m.group("balance")) if m.group("balance") else None,
            description=(m.group("desc") or "").strip() or None,
            date_str=date_str,
            time_str=time_str,
        )

    # 카드 패턴 (항상 출금)
    m = _CARD_RE.search(text)
    if m:
        return ParsedTransaction(
            bank=m.group("bank"),
            tx_type="출금",
            amount=_parse_amount(m.group("amount")),
            balance=None,
            description=(m.group("desc") or "").strip() or None,
            date_str=date_str,
            time_str=time_str,
        )

    # 괄호 없는 패턴
    m = _NO_BRACKET_RE.search(text)
    if m:
        return ParsedTransaction(
            bank=m.group("bank"),
            tx_type=m.group("type"),
            amount=_parse_amount(m.group("amount")),
            balance=_parse_amount(m.group("balance")) if m.group("balance") else None,
            description=(m.group("desc") or "").strip() or None,
            date_str=date_str,
            time_str=time_str,
        )

    return None


# ---- 자동 카테고리 분류 ----

CATEGORY_MAP: dict[str, list[str]] = {
    "식비": ["배달의민족", "요기요", "쿠팡이츠", "스타벅스", "이디야", "맥도날드", "버거킹",
            "롯데리아", "서브웨이", "파리바게뜨", "뚜레쥬르", "편의점", "CU", "GS25", "세븐일레븐",
            "미니스톱", "식당", "카페", "치킨", "피자", "떡볶이"],
    "교통": ["카카오T", "택시", "주유", "고속도로", "교통", "버스", "지하철", "코레일",
            "KTX", "SRT", "주차", "톨게이트", "하이패스"],
    "쇼핑": ["쿠팡", "이마트", "홈플러스", "다이소", "올리브영", "무신사", "네이버쇼핑",
            "SSG", "롯데마트", "코스트코", "트레이더스"],
    "통신": ["SKT", "KT", "LG유플러스", "알뜰폰"],
    "구독": ["넷플릭스", "유튜브", "스포티파이", "멜론", "웨이브", "왓챠", "디즈니"],
    "급여": ["급여", "월급", "보너스", "상여금", "인센티브"],
    "이체": ["이체", "송금"],
    "공과금": ["전기", "가스", "수도", "관리비", "국민연금", "건강보험", "세금"],
    "의료": ["병원", "약국", "의원", "치과", "안과", "피부과"],
    "교육": ["학원", "교육", "강의", "클래스"],
}


def auto_categorize(description: Optional[str]) -> Optional[str]:
    if not description:
        return None
    for category, keywords in CATEGORY_MAP.items():
        if any(kw in description for kw in keywords):
            return category
    return "기타"


# ---- CLI 테스트 ----

if __name__ == "__main__":
    samples = [
        "[KB국민] 입금 500,000원 잔액 1,200,000원 홍길동",
        "[신한] 출금 50,000원 카드결제 스타벅스",
        "[카카오뱅크] 입금 3,000,000원 급여",
        "[토스] 출금 12,500원 배달의민족",
        "[하나] 카드승인 30,000원 이마트",
        "[우리] 입금이체 2,000,000원 잔액 5,000,000원",
        "[NH농협] 출금 100,000원 ATM",
        "[신한] 체크카드 8,900원 CU편의점",
        "KB국민 입금 1,000,000원 잔액 3,000,000원",
        "그냥 일반 메시지입니다",
    ]
    for s in samples:
        result = parse_sms(s)
        if result:
            cat = auto_categorize(result.description)
            print(f"  -> {result.tx_type} {result.amount:,}원 ({result.bank}) [{cat}]")
        else:
            print(f"  -> 인식 불가")
        print()
