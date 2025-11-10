import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore


DEFAULT_OUTPUT = Path("broadcast_times.log")
STREAM_START_TOOLTIP_PATTERN = re.compile(
    r'data-knife-tooltip="라이브 시작:\s*([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})"'
)
LIVE_DETAIL_API = "https://api.chzzk.naver.com/service/v1/channels/{channel_id}/live-detail"

# 여기에서 기본으로 사용할 스트리머 아이디를 설정할 수 있습니다.
# 명령행 인자로 직접 입력하지 않을 경우 아래 값이 사용됩니다.
# 예시: DEFAULT_STREAMER_ID = "0ca4527714b7bee3220d9de23b6d63dd"
DEFAULT_STREAMER_ID = ""


def _load_cookies() -> dict[str, str]:
    cookies_path = Path("cookies.json")
    if not cookies_path.exists():
        return {}
    try:
        raw = json.loads(cookies_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    cookies: dict[str, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(key, str) and isinstance(value, str):
                cookies[key] = value
    return cookies


def _resolve_timezone() -> Optional[timezone]:
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo("Asia/Seoul")
    except Exception:  # pragma: no cover
        return None


def _normalize_streamer_id(streamer_id: str) -> str:
    streamer_id = streamer_id.strip()
    if not streamer_id:
        raise ValueError("스트리머 아이디가 비어 있습니다.")
    if streamer_id.startswith("http://") or streamer_id.startswith("https://"):
        parsed = urlparse(streamer_id)
        segments = [seg for seg in parsed.path.split("/") if seg]
        if len(segments) >= 2 and segments[0].lower() == "live":
            return segments[1]
        raise ValueError("라이브 URL 형식을 인식하지 못했습니다. 예: https://chzzk.naver.com/live/<아이디>")
    return streamer_id


def _get_live_url(streamer_id: str) -> str:
    channel_id = _normalize_streamer_id(streamer_id)
    return f"https://chzzk.naver.com/live/{channel_id}"


def fetch_live_page(streamer_id: str, timeout: float = 10.0) -> str:
    url = _get_live_url(streamer_id)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/129.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    cookies = _load_cookies()
    response = requests.get(url, headers=headers, cookies=cookies, timeout=timeout)
    response.raise_for_status()
    return response.text


def _parse_start_time_html(html: str, tz: Optional[timezone]) -> datetime:
    match = STREAM_START_TOOLTIP_PATTERN.search(html)
    if not match:
        raise RuntimeError("라이브 시작 시간을 찾을 수 없습니다. 방송이 오프라인인지 확인해 주세요.")
    start_str = match.group(1)
    start = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
    if tz is not None:
        return start.replace(tzinfo=tz)
    return start


def _parse_start_time_api(data: dict[str, object], tz: Optional[timezone]) -> datetime:
    content = data.get("content")
    if not isinstance(content, dict):
        raise RuntimeError("라이브 정보를 불러오지 못했습니다. 방송이 오프라인인지 확인해 주세요.")

    status = str(content.get("status", "")).upper()
    if status not in {"OPEN", "ON", "LIVE"}:
        raise RuntimeError("방송이 오프라인 상태로 보입니다.")

    start_raw = content.get("openDate") or content.get("liveOpenDate") or content.get("startDate")
    if not isinstance(start_raw, str) or not start_raw:
        raise RuntimeError("라이브 시작 시간이 응답에 포함되어 있지 않습니다.")

    try:
        start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("라이브 시작 시간 파싱에 실패했습니다.") from exc

    if tz is None or start.tzinfo is not None:
        return start
    return start.replace(tzinfo=tz)


def fetch_start_time(streamer_id: str, timeout: float = 10.0) -> datetime:
    tz = _resolve_timezone()
    channel_id = _normalize_streamer_id(streamer_id)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/129.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    cookies = _load_cookies()

    api_url = LIVE_DETAIL_API.format(channel_id=channel_id)
    response = requests.get(api_url, headers=headers, cookies=cookies, timeout=timeout)
    if response.status_code == 200:
        try:
            data = response.json()
        except Exception:
            data = {}
        if isinstance(data, dict):
            try:
                return _parse_start_time_api(data, tz)
            except RuntimeError:
                pass  # API 응답에 시작 시간이 없으면 HTML 파싱으로 폴백

    # API로 실패했거나 파싱이 되지 않은 경우 HTML에서 추출
    html = fetch_live_page(channel_id, timeout=timeout)
    return _parse_start_time_html(html, tz)


def format_duration(delta_seconds: int) -> str:
    hours, remainder = divmod(delta_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def log_broadcast_time(streamer_id: str, output_path: Path) -> None:
    tz = _resolve_timezone()
    start_time = fetch_start_time(streamer_id, timeout=10.0)

    if tz is not None:
        now = datetime.now(tz)
    else:
        now = datetime.now()

    if start_time.tzinfo is None and now.tzinfo is not None:
        # Align to naive for subtraction if necessary
        now = now.replace(tzinfo=None)
    elif start_time.tzinfo is not None and now.tzinfo is None:
        start_time = start_time.replace(tzinfo=None)

    elapsed_seconds = int((now - start_time).total_seconds())
    if elapsed_seconds < 0:
        elapsed_seconds = 0

    duration_text = format_duration(elapsed_seconds)

    log_line = (
        f"current={now.isoformat()} | start={start_time.isoformat()} | duration={duration_text}"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as fp:
        fp.write(log_line + os.linesep)
    print(log_line)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="네이버 치지직 방송 시작 시간을 기반으로 현재 방송 진행 시간을 기록합니다."
    )
    parser.add_argument(
        "streamer_id",
        nargs="?",
        default=None,
        help=(
            "방송 채널의 스트리머 ID 또는 chzzk 라이브 URL. "
            "입력하지 않으면 파일 상단 DEFAULT_STREAMER_ID 값이 사용됩니다."
        ),
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="로그를 저장할 파일 경로 (기본값: broadcast_times.log)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="라이브 페이지 요청 타임아웃(초) (기본값: 10초)",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    output_path = Path(args.output)

    streamer_id = args.streamer_id or DEFAULT_STREAMER_ID
    if not streamer_id:
        parser.error(
            "스트리머 아이디가 지정되지 않았습니다. "
            "명령행에서 입력하거나 DEFAULT_STREAMER_ID 값을 설정해 주세요."
        )

    try:
        log_broadcast_time(streamer_id, output_path)
    except Exception as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()

