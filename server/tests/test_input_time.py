"""原始输入的确定性日期与时间语义测试。"""

from datetime import date, datetime

import pytest

from app.plan_input.time import NeedsTime, extract_duration, resolve_time

NOW = datetime.fromisoformat("2026-11-28T21:00:00+08:00")


@pytest.mark.parametrize(
    ("text", "day", "latest", "relation"),
    [
        ("明早8点前到三教，从宿舍出发骑车", date(2026, 11, 29), "08:00", "arrive_by"),
        ("明早八点半之前到三教", date(2026, 11, 29), "08:30", "arrive_by"),
        ("后天上午九点十五分前到医院", date(2026, 11, 30), "09:15", "arrive_by"),
        ("大后天下午两点上课", date(2026, 12, 1), "14:00", "start_at"),
        ("这周日14:30前到车站", date(2026, 11, 29), "14:30", "arrive_by"),
        ("本周星期天上午十点预约", date(2026, 11, 29), "10:00", "start_at"),
        ("下周三上午八点上课", date(2026, 12, 2), "08:00", "start_at"),
        ("下周3上午八点上课", date(2026, 12, 2), "08:00", "start_at"),
        ("下星期日下午三点开始看展", date(2026, 12, 6), "15:00", "start_at"),
        ("2026年12月5日上午8点前到学校", date(2026, 12, 5), "08:00", "arrive_by"),
        ("2026-12-05 08:00前到学校", date(2026, 12, 5), "08:00", "arrive_by"),
        ("12月5号上午八点前到学校", date(2026, 12, 5), "08:00", "arrive_by"),
        ("2026年12月5日入住，次日上午八点前到学校", date(2026, 12, 6), "08:00", "arrive_by"),
        ("2026年12月5日入住，第二天上午八点前到学校", date(2026, 12, 6), "08:00", "arrive_by"),
        ("2026年12月31日入住，翌日上午八点前到学校", date(2027, 1, 1), "08:00", "arrive_by"),
        ("明天１４：３０前到学校", date(2026, 11, 29), "14:30", "arrive_by"),
        ("明日１４：３０前到学校", date(2026, 11, 29), "14:30", "arrive_by"),
        ("预约明天上午8点到医院", date(2026, 11, 29), "08:00", "start_at"),
    ],
)
def test_resolves_explicit_semantics(text, day, latest, relation):
    result = resolve_time(text, NOW)

    assert result.date == day
    assert result.latest == latest
    assert result.relation == relation


@pytest.mark.parametrize(
    "text",
    [
        "明天8点前到学校",  # 缺上午或下午。
        "明天上午8点去学校",  # 去，不等于出发/到达。
        "明天上午8点出发",  # 本轮不擅自改写出发约束。
        "明天上午8点从家出发，9点到学校",  # 多个时间。
        "明天上午8点前到学校，后天回来",  # 多日。
        "次日上午8点前到学校",  # 缺少前文日期。
        "明天入住，次日上午8点前到学校",  # 本轮仅接受明确前文日期。
        "这周三上午8点前到学校",  # 已过去，不能自行滚动到下周。
        "上周日上午8点前到学校",
        "昨天上午8点前到学校",
        "前天上午8点前到学校",
        "今天上午8点前到学校",
        "周末上午8点前到学校",  # 周末两日不确定。
        "明天晚上12点前到学校",  # 当日24点或次日0点不确定。
        "明天上午14点前到学校",
        "明天25:00前到学校",
        "明天08:70前到学校",
        "2026年2月30日上午8点前到学校",
        "2026年12月5日或12月6日上午8点前到学校",
        "明天上午8点到9点在学校",
        "明年12月5日上午8点前到学校",  # 不能把明年当作今年。
        "下个月5号上午8点前到学校",
        "下下周三上午8点前到学校",
        "明早3十五点前到学校",
        "明早8.5点前到学校",
        "明天08:00:30前到学校",
    ],
)
def test_asks_instead_of_guessing(text):
    with pytest.raises(NeedsTime):
        resolve_time(text, NOW)


def test_requires_timezone_aware_reference():
    with pytest.raises(NeedsTime, match="时区"):
        resolve_time("明早8点前到学校", NOW.replace(tzinfo=None))


def test_day_without_relative_word_is_current_day_and_never_rolls_forward():
    now = datetime.fromisoformat("2026-11-28T06:00:00+08:00")

    assert resolve_time("上午8点前到学校", now).date == date(2026, 11, 28)
    with pytest.raises(NeedsTime):
        resolve_time("上午8点前到学校", NOW)


def test_leap_day_and_year_boundary_use_calendar_rules():
    now = datetime.fromisoformat("2028-02-28T21:00:00+08:00")

    assert resolve_time("明早8点前到学校", now).date == date(2028, 2, 29)
    assert resolve_time("后天早上8点前到学校", now).date == date(2028, 3, 1)


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("明早8点前到学校，上课两小时", 120),
        ("到展馆后停留30分钟", 30),
        ("课程持续一个半小时", 90),
        ("会议持续一小时半", 90),
        ("吃饭半小时", 30),
        ("参观1.5小时", 90),
        ("明早8点前到学校", None),
    ],
)
def test_extracts_only_explicit_activity_duration(text, minutes):
    assert extract_duration(text) == minutes


@pytest.mark.parametrize(
    "text",
    [
        "骑车10分钟到学校",
        "提前10分钟到学校",
        "上课前提前10分钟到学校",
        "上课两小时，骑车10分钟到家",
        "在学校30分钟",
        "停留零分钟",
        "停留24小时",
        "停留一天",
        "上课约两小时",
        "上课两小时左右",
        "停留-1小时",
    ],
)
def test_duration_requires_an_unambiguous_role_and_valid_value(text):
    with pytest.raises(NeedsTime):
        extract_duration(text)
