"""한국 은행 SMS 알림 파싱.

SMS 전달 앱이 보내는 포맷:
    [수신날짜] 2026.04.07 15:07:37
    [발신번호] 토스
    [수신번호] 01012341234[.]
    [수신내용]
    1원 입금
    김정민 → 내 우체국은행 통장

또는 원본 SMS 그대로:
    [KB국민] 입금 500,000원 잔액 1,200,000원 홍길동
"""
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
    date_str: Optional[str]   # 'YYYY-MM-DD'
    time_str: Optional[str]   # 'HH:MM'


def _parse_amount(s: str) -> int:
    return int(s.replace(",", "").replace(".", "").replace(" ", ""))


# ---- SMS 전달 앱 헤더 파싱 ----

_HEADER_DATE_RE = re.compile(r"\[수신날짜\]\s*(\d{4})[./](\d{1,2})[./](\d{1,2})\s+(\d{1,2}):(\d{2})")
_HEADER_SENDER_RE = re.compile(r"\[발신번호\]\s*(.+)")
_HEADER_CONTENT_RE = re.compile(r"\[수신내용\]\s*\n?([\s\S]+)", re.MULTILINE)


def _parse_forwarded_sms(text: str) -> Optional[dict]:
    """SMS 전달 앱 포맷에서 헤더를 추출. 아니면 None."""
    if "[수신내용]" not in text and "[수신날짜]" not in text:
        return None

    result: dict = {}

    m = _HEADER_DATE_RE.search(text)
    if m:
        result["date"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        result["time"] = f"{int(m.group(4)):02d}:{m.group(5)}"

    m = _HEADER_SENDER_RE.search(text)
    if m:
        result["sender"] = m.group(1).strip()

    m = _HEADER_CONTENT_RE.search(text)
    if m:
        result["content"] = m.group(1).strip()

    return result if result.get("content") else None


# ---- 본문 파싱 패턴 ----

# "1원 입금" / "50,000원 출금" / "입금 500,000원" / "출금 12,500원"
_AMOUNT_TYPE_RE = re.compile(r"(?P<amount>[\d,]+)\s*원\s*(?P<type>입금|출금)")
_TYPE_AMOUNT_RE = re.compile(r"(?P<type>입금|출금)\s*(?P<amount>[\d,]+)\s*원")

# "잔액 1,200,000원" / "잔액:1,200,000원"
_BALANCE_RE = re.compile(r"잔액\s*:?\s*([\d,]+)\s*원")

# "카드승인 50,000원" / "카드결제 12,500원" / "체크카드 8,900원"
_CARD_RE = re.compile(r"(?:카드승인|카드결제|체크카드|체크)\s*(?P<amount>[\d,]+)\s*원")

# "→ 내 우체국은행 통장" / "→ 내 신한은행"
_TO_ACCOUNT_RE = re.compile(r"→\s*(.+)")
_FROM_ACCOUNT_RE = re.compile(r"(.+?)\s*→")

# 은행 추출 (본문에서)
_BANK_NAMES = [
    "KB국민", "국민은행", "신한", "신한은행", "하나", "하나은행",
    "우리", "우리은행", "NH농협", "농협", "카카오뱅크", "토스", "토스뱅크",
    "IBK기업", "기업은행", "SC제일", "케이뱅크", "수협", "우체국", "우체국은행",
    "광주은행", "전북은행", "제주은행", "대구은행", "부산은행", "경남은행",
    "새마을금고", "신협", "산업은행", "수출입은행",
]
_BANK_RE = re.compile(r"(" + "|".join(re.escape(b) for b in _BANK_NAMES) + r")")

# [은행] 입금/출금 패턴 (기존)
_BRACKET_MAIN_RE = re.compile(
    r"\[(?P<bank>[^\]]+)\]\s*"
    r"(?P<type>입금|출금)\s*"
    r"(?P<amount>[\d,]+)\s*원"
    r"(?:\s*잔액\s*(?P<balance>[\d,]+)\s*원)?"
    r"(?:\s*(?P<desc>.+))?"
)
_BRACKET_CARD_RE = re.compile(
    r"\[(?P<bank>[^\]]+)\]\s*"
    r"(?:카드승인|카드결제|체크|체크카드)\s*"
    r"(?P<amount>[\d,]+)\s*원\s*"
    r"(?P<desc>.+)?"
)
_BRACKET_TRANSFER_RE = re.compile(
    r"\[(?P<bank>[^\]]+)\]\s*"
    r"(?P<type>입금이체|출금이체|이체)\s*"
    r"(?P<amount>[\d,]+)\s*원"
    r"(?:\s*잔액\s*(?P<balance>[\d,]+)\s*원)?"
    r"(?:\s*(?P<desc>.+))?"
)


# ---- 날짜/시간 추출 (일반 텍스트용) ----

_DATE_FULL_RE = re.compile(r"(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")


def _extract_date(text: str) -> Optional[str]:
    m = _DATE_FULL_RE.search(text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def _extract_time(text: str) -> Optional[str]:
    m = _TIME_RE.search(text)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


# ---- 메인 파싱 ----

def _parse_content(content: str, bank: Optional[str] = None,
                   date_str: Optional[str] = None,
                   time_str: Optional[str] = None) -> Optional[ParsedTransaction]:
    """실제 SMS 본문을 파싱."""
    content = content.strip()
    if not content:
        return None

    # 날짜/시간이 아직 없으면 본문에서 추출
    if not date_str:
        date_str = _extract_date(content)
    if not time_str:
        time_str = _extract_time(content)

    # 1) [은행] 패턴 (기존 직접 SMS)
    for regex in [_BRACKET_TRANSFER_RE, _BRACKET_MAIN_RE]:
        m = regex.search(content)
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

    m = _BRACKET_CARD_RE.search(content)
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

    # 2) "금액원 입금/출금" 또는 "입금/출금 금액원" 패턴
    m = _AMOUNT_TYPE_RE.search(content)
    if not m:
        m = _TYPE_AMOUNT_RE.search(content)
    if m:
        tx_type = m.group("type")
        amount = _parse_amount(m.group("amount"))

        # 잔액 추출
        bm = _BALANCE_RE.search(content)
        balance = _parse_amount(bm.group(1)) if bm else None

        # 은행명 추출 (본문 또는 발신번호)
        detected_bank = bank
        bk = _BANK_RE.search(content)
        if bk:
            detected_bank = bk.group(1)
        if not detected_bank:
            detected_bank = "알수없음"

        # 설명 추출: 보낸사람/받는곳
        desc = None
        lines = content.split("\n")
        desc_parts = []
        for line in lines:
            line = line.strip()
            # 금액 라인이나 빈 라인은 건너뛰기
            if not line or re.match(r"^[\d,]+\s*원\s*(입금|출금)", line) or re.match(r"^(입금|출금)", line):
                continue
            desc_parts.append(line)
        if desc_parts:
            desc = " / ".join(desc_parts)

        return ParsedTransaction(
            bank=detected_bank,
            tx_type=tx_type,
            amount=amount,
            balance=balance,
            description=desc,
            date_str=date_str,
            time_str=time_str,
        )

    # 3) 카드 패턴 (본문에서)
    m = _CARD_RE.search(content)
    if m:
        detected_bank = bank
        bk = _BANK_RE.search(content)
        if bk:
            detected_bank = bk.group(1)
        if not detected_bank:
            detected_bank = "알수없음"

        # 가맹점명 추출
        desc = content[m.end():].strip() or None

        return ParsedTransaction(
            bank=detected_bank,
            tx_type="출금",
            amount=_parse_amount(m.group("amount")),
            balance=None,
            description=desc,
            date_str=date_str,
            time_str=time_str,
        )

    return None


def parse_sms(text: str) -> Optional[ParsedTransaction]:
    """SMS 메시지를 파싱. SMS 전달 앱 포맷 + 원본 SMS 모두 지원."""
    text = text.strip()
    if not text:
        return None

    # SMS 전달 앱 포맷인지 확인
    forwarded = _parse_forwarded_sms(text)
    if forwarded:
        return _parse_content(
            content=forwarded["content"],
            bank=forwarded.get("sender"),
            date_str=forwarded.get("date"),
            time_str=forwarded.get("time"),
        )

    # 원본 SMS 포맷
    return _parse_content(content=text)


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
    "이체": ["이체", "송금", "→"],
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
        # SMS 전달 앱 포맷
        """[수신날짜] 2026.04.07 15:07:37
[발신번호] 토스
[수신번호] 01012341234[.]
[수신내용]
1원 입금
김정민 → 내 우체국은행 통장""",

        """[수신날짜] 2026.04.07 12:30:00
[발신번호] KB국민
[수신번호] 01012341234[.]
[수신내용]
50,000원 출금
스타벅스 카드결제""",

        """[수신날짜] 2026.04.07 09:00:00
[발신번호] 카카오뱅크
[수신번호] 01012341234[.]
[수신내용]
3,000,000원 입금
급여""",

        # 기존 [은행] 포맷
        "[KB국민] 입금 500,000원 잔액 1,200,000원 홍길동",
        "[신한] 출금 50,000원 카드결제 스타벅스",
        "[토스] 출금 12,500원 배달의민족",

        # 인식 불가
        "그냥 일반 메시지입니다",
    ]
    for s in samples:
        print(f"입력: {s[:60]}...")
        result = parse_sms(s)
        if result:
            cat = auto_categorize(result.description)
            print(f"  -> {result.tx_type} {result.amount:,}원 ({result.bank}) "
                  f"[{cat}] 날짜={result.date_str} 시간={result.time_str}")
            if result.description:
                print(f"     설명: {result.description}")
        else:
            print(f"  -> 인식 불가")
        print()
