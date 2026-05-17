"""tasks/steps/_geo.py — 城市/地区代码映射(用户输入友好化)。

前端用人类友好的字符串(中文城市名 / 国家 ISO code),后端 step
把它们转成各平台 API 需要的内部 ID。

Boss   : 中文城市名 → 9 位数字 city code
LinkedIn: ISO country code → geoUrn 数字 ID
Indeed : ISO country code 直接传给 indeed_search_jobs(API 用 country 参数)

未命中时:
  - Boss:用户输入了 code(纯数字)→ 直接用;否则 fallback 北京
  - LinkedIn:留空 = 全球
  - Indeed:fallback 'US'
"""
from __future__ import annotations


# Boss 直聘城市编码(常用 30+ 城市)
# 来源:Boss 公开 API(https://www.zhipin.com/wapi/zpgeek/getCityList.json)
BOSS_CITY_CODES = {
    # 一线
    "北京": 101010100, "上海": 101020100, "广州": 101280100, "深圳": 101280600,
    # 新一线
    "杭州": 101210100, "成都": 101270101, "南京": 101190101, "武汉": 101200101,
    "重庆": 101040100, "西安": 101110101, "苏州": 101190401, "天津": 101030100,
    "长沙": 101250101, "郑州": 101180101, "青岛": 101120201, "沈阳": 101070101,
    # 二线
    "宁波": 101210401, "厦门": 101230201, "福州": 101230101, "无锡": 101190201,
    "合肥": 101220101, "济南": 101120101, "大连": 101070201, "东莞": 101281600,
    "佛山": 101280800, "昆明": 101290101, "哈尔滨": 101050101, "长春": 101060101,
    "南昌": 101240101, "贵阳": 101260101, "南宁": 101300101,
    # 特殊
    "全国": 100010000,    # 不限地区(Boss 接受这个 code)
    "远程": 100010000,    # 远程也用全国
    "不限": 100010000,
}


def boss_city_code(value: str | int | None, default: int = 101010100) -> int:
    """中文城市名 → Boss 9 位 city code。
    数字字符串 / int → 直接用(允许高级用户传 code)。"""
    if value is None or value == "":
        return default
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if s.isdigit():
        return int(s)
    return BOSS_CITY_CODES.get(s, default)


# LinkedIn geoUrn ID(数字 ID,用于 search_jobs 的 geoLocationId 参数)
# 来源:LinkedIn 公开 geoUrn enum
LINKEDIN_GEO_BY_COUNTRY = {
    "US": "103644278",   # United States
    "CN": "102890883",   # China
    "CA": "101174742",   # Canada
    "GB": "101165590",   # United Kingdom
    "UK": "101165590",   # alias
    "JP": "101355337",   # Japan
    "AU": "101452733",   # Australia
    "SG": "102454443",   # Singapore
    "DE": "101282230",   # Germany
    "IN": "102713980",   # India
    "FR": "105015875",   # France
    "BR": "106057199",   # Brazil
}


def linkedin_geo_urn(country: str | None) -> str:
    """ISO country code(US/CN/...)→ LinkedIn geoUrn ID 字符串。
    空 → 空(=全球搜索)"""
    if not country:
        return ""
    return LINKEDIN_GEO_BY_COUNTRY.get(str(country).upper().strip(), "")


# Indeed 接受 country 参数直接(US/UK/SG 等),无需 ID 转换
# 这里只做 normalize(大写 + 默认 US)
def indeed_country(value: str | None, default: str = "US") -> str:
    if not value:
        return default
    s = str(value).upper().strip()
    # 兼容 GB / UK
    if s == "GB":
        s = "GB"
    return s or default
