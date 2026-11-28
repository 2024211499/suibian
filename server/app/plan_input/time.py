"""从用户原话确定单个活动的日历日期与时间约束。"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

NUMBER = r"[零〇一二两三四五六七八九十\d]{1,3}"
CLOCK = re.compile(
    rf"(?<![\d:.])(?P<hour>{NUMBER})"
    rf"(?:(?P<colon>:)(?P<minute>\d{{2}})|点(?P<part>半|一刻|三刻|{NUMBER}分?)?)(?![\d:.])"
)
CALENDAR = re.compile(r"(?<!\d)(?:(?P<year>\d{4})[-年/.])?(?P<month>\d{1,2})[-月/.](?P<day>\d{1,2})(?:日|号)?(?!\d)")
WEEKDAY = re.compile(r"(?P<week>上|下|这|本)?(?:个)?(?:周|星期)(?P<day>[一二三四五六日天1-7])")
RELATIVE = re.compile(r"大前天|大后天|前天|昨天|今天|明天|后天|昨晚|今早|今晚|明早|明晚|明晨|今日|明日|昨日")
DAY_OFFSETS = {
    "大前天": -3, "前天": -2, "昨天": -1, "昨晚": -1, "今天": 0, "今早": 0, "今晚": 0,
    "明天": 1, "明早": 1, "明晚": 1, "明晨": 1, "后天": 2, "大后天": 3,
    "今日": 0, "明日": 1, "昨日": -1,
}
PERIOD = re.compile(r"凌晨|清晨|早晨|早上|上午|中午|下午|傍晚|晚上|今早|明早|明晨|今晚|明晚")
DURATION = re.compile(
    r"(?<![\d.])(?P<count>\d+(?:\.\d+)?|[零〇一二两三四五六七八九十]+|半)"
    r"(?P<middle>个?半)?(?P<unit>小时|分钟|天)(?P<half>半)?"
)
ACTIVITY_DURATION = re.compile(
    r"(?:上课|课程|会议|开会|停留|逗留|游玩|参观|用餐|吃饭|午饭|晚饭|早餐|看展)"
    r"(?:持续|时长(?:为|是)?|需要|共|用时)?\s*$"
)


class NeedsTime(Exception):
    """用户原话不足以安全确定本轮单个活动的时间。"""


@dataclass(frozen=True)
class Schedule:
    date: date
    latest: str
    relation: Literal["arrive_by", "start_at"]


def _number(text: str) -> int:
    if text.isascii() and text.isdigit():
        return int(text)
    if any(character.isascii() and character.isdigit() for character in text):
        raise NeedsTime("请不要混用中文和阿拉伯数字表示同一个时间数值。")
    digits = dict(zip("零〇一二两三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9), strict=True))
    if text.count("十") == 1:
        tens, units = text.split("十")
        if len(tens) > 1 or len(units) > 1:
            raise NeedsTime("请用明确的小时和分钟填写时间。")
        return digits.get(tens, 1) * 10 + digits.get(units, 0)
    if text in digits:
        return digits[text]
    raise NeedsTime("请用明确的小时和分钟填写时间。")


def extract_duration(text: str) -> float | None:
    """仅提取明确的单次活动时长，路程或提前量不能当作停留时长。"""
    text = unicodedata.normalize("NFKC", text)
    matches = list(DURATION.finditer(text))
    if not matches:
        return None
    if len(matches) != 1:
        raise NeedsTime("检测到多个时长，请明确本次活动的停留时长。")
    match = matches[0]
    if not ACTIVITY_DURATION.search(text[:match.start()]) or re.match(
        r"\s*(?:左右|以上|以下|以内|至|到)", text[match.end():]
    ):
        raise NeedsTime("请明确活动停留时长，路程时间和提前量不能作为停留时间。")
    if match["unit"] == "天":
        raise NeedsTime("本轮仅支持单日活动，请以小时或分钟明确停留时长。")
    value = match["count"]
    amount = 0.5 if value == "半" else float(value) if "." in value else _number(value)
    if match["middle"] or match["half"]:
        if value == "半" or match["unit"] != "小时" or (match["middle"] and match["half"]):
            raise NeedsTime("请使用明确的小时或分钟填写活动时长。")
        amount += 0.5
    minutes = amount * (60 if match["unit"] == "小时" else 1)
    if not math.isfinite(minutes) or not 0 < minutes < 1440:
        raise NeedsTime("活动时长必须大于零且短于一天。")
    return float(minutes)


def _calendar_date(text: str, now: datetime) -> date:
    if re.search(r"明年|后年|去年|(?:上|下|本)(?:个)?月|下下周|上上周|大下周|大上周", text):
        raise NeedsTime("请把相对月份、年份或隔周表达改为具体日期。")
    explicit = list(CALENDAR.finditer(text))
    relative = list(RELATIVE.finditer(text))
    weekdays = list(WEEKDAY.finditer(text))
    contextual = list(re.finditer(r"次日|翌日|第二天", text))
    if len(explicit) + len(relative) + len(weekdays) > 1:
        raise NeedsTime("检测到多个日期，请先明确本次活动的日期。")
    if contextual and (len(contextual) != 1 or len(explicit) != 1 or explicit[0].end() > contextual[0].start()):
        raise NeedsTime("“次日”需要前文明确的日期，请填写具体日期。")
    if explicit:
        match = explicit[0]
        try:
            resolved = date(int(match["year"] or now.year), int(match["month"]), int(match["day"]))
        except ValueError as error:
            raise NeedsTime("该日期不存在，请核对年月日。") from error
        if contextual:
            resolved += timedelta(days=1)
        return resolved
    if relative:
        return now.date() + timedelta(days=DAY_OFFSETS[relative[0].group()])
    if weekdays:
        match = weekdays[0]
        shift = {"上": -7, "下": 7}.get(match["week"], 0)
        weekday = int(match["day"]) - 1 if match["day"].isdigit() else (
            "一二三四五六日".index(match["day"].replace("天", "日"))
        )
        return now.date() + timedelta(days=shift + weekday - now.weekday())
    if re.search(r"周末|星期|(?:上|下|这|本)周|月底|月初|次日|翌日|第二天", text):
        raise NeedsTime("请明确本次活动的具体日期。")
    return now.date()


def _clock_time(text: str, match: re.Match) -> tuple[int, int]:
    hour = _number(match["hour"])
    part = match["part"]
    minute = int(match["minute"]) if match["colon"] else (
        {"半": 30, "一刻": 15, "三刻": 45}.get(part, 0) if not part or part in {"半", "一刻", "三刻"}
        else _number(part.removesuffix("分"))
    )
    if not 0 <= hour < 24 or not 0 <= minute < 60:
        raise NeedsTime("时间超出有效范围，请核对小时和分钟。")
    prefix = re.split(r"[，,。;；]", text[:match.start()])[-1]
    periods = list(PERIOD.finditer(prefix))
    if not periods:
        # “明早出发，8点前到”中的“明早”仍是当前活动的上文。
        periods = list(PERIOD.finditer(text[:match.start()]))
    period = periods[-1].group() if periods else None
    if period in {"下午", "傍晚", "晚上", "今晚", "明晚"}:
        if hour == 12 and period in {"晚上", "今晚", "明晚"}:
            raise NeedsTime("晚上十二点跨越日期，请明确是哪天零点。")
        if 1 <= hour < 12:
            hour += 12
    elif period == "中午":
        if hour in {1, 2}:
            hour += 12
        elif hour not in {11, 12, 13, 14}:
            raise NeedsTime("中午的时间不明确，请使用二十四小时制。")
    elif period == "凌晨":
        if hour == 12:
            hour = 0
        if hour > 5:
            raise NeedsTime("凌晨的时间不明确，请使用二十四小时制。")
    elif period and hour >= 12:
        raise NeedsTime("上午的时间与小时数冲突，请使用二十四小时制。")
    elif not period and not match["colon"] and hour < 13:
        raise NeedsTime("请补充上午或下午，或使用二十四小时制。")
    return hour, minute


def _relation(text: str, match: re.Match) -> Literal["arrive_by", "start_at"]:
    tail = re.split(r"[，,。;；]", text[match.end():], maxsplit=1)[0]
    prefix = re.split(r"[，,。;；]", text[:match.start()])[-1]
    if re.match(r"\s*(?:之?前)(?:到达?|抵达|赶到|抵)", tail) or (
        re.search(r"最晚|不晚于", prefix) and re.match(r"\s*(?:到达?|抵达|赶到|抵)", tail)
    ):
        return "arrive_by"
    if re.match(r"\s*(?:出发|从.+出发)", tail):
        raise NeedsTime("请补充最晚到达或活动开始时间；出发时间不能代替到达约束。")
    if re.search(r"上课|开课|开始|开会|预约|入场|开演|起飞|发车", tail) or re.search(r"预约|约定", prefix):
        return "start_at"
    raise NeedsTime("这个时间是出发、最晚到达，还是活动开始时间？")


def resolve_time(text: str, now: datetime) -> Schedule:
    """仅接受原话中明确的单活动约束，不能安全确定时返回可读追问。"""
    if now.tzinfo is None or now.utcoffset() is None:
        raise NeedsTime("请提供带时区的当前时间。")
    text = unicodedata.normalize("NFKC", text)
    matches = list(CLOCK.finditer(text))
    if len(matches) != 1:
        raise NeedsTime("请明确本次单个活动的一个时间约束。")
    resolved_date = _calendar_date(text, now)
    hour, minute = _clock_time(text, matches[0])
    relation = _relation(text, matches[0])
    target = datetime.combine(resolved_date, datetime.min.time(), tzinfo=now.tzinfo).replace(hour=hour, minute=minute)
    if target < now:
        raise NeedsTime(f"{resolved_date.isoformat()} {hour:02d}:{minute:02d} 已经过期，请确认日期和时间。")
    return Schedule(resolved_date, f"{hour:02d}:{minute:02d}", relation)
