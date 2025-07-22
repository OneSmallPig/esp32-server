"""
带缓存的天气查询插件 - 集成幻影池优化版本
相比原版get_weather.py，增加了缓存机制，提升性能并减少API调用
"""

import requests
from bs4 import BeautifulSoup
from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from core.utils.util import get_ip_info
from core.utils.weather_cache import get_weather_cache_pool

TAG = __name__
logger = setup_logging()

GET_WEATHER_CACHED_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "get_weather_cached",
        "description": (
            "获取某个地点的天气（缓存优化版本），用户应提供一个位置，比如用户说杭州天气，参数为：杭州。"
            "如果用户说的是省份，默认用省会城市。如果用户说的不是省份或城市而是一个地名，默认用该地所在省份的省会城市。"
            "如果用户没有指明地点，说'天气怎么样'，'今天天气如何'，location参数为空。"
            "此版本使用了缓存机制，响应更快，API调用更少。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "地点名，例如杭州。可选参数，如果不提供则不传",
                },
                "lang": {
                    "type": "string",
                    "description": "返回用户使用的语言code，例如zh_CN/zh_HK/en_US/ja_JP等，默认zh_CN",
                },
                "force_refresh": {
                    "type": "boolean",
                    "description": "是否强制刷新缓存，默认false",
                },
            },
            "required": ["lang"],
        },
    },
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36"
    )
}

# 天气代码映射
WEATHER_CODE_MAP = {
    "100": "晴", "101": "多云", "102": "少云", "103": "晴间多云", "104": "阴",
    "150": "晴", "151": "多云", "152": "少云", "153": "晴间多云",
    "300": "阵雨", "301": "强阵雨", "302": "雷阵雨", "303": "强雷阵雨",
    "304": "雷阵雨伴有冰雹", "305": "小雨", "306": "中雨", "307": "大雨",
    "308": "极端降雨", "309": "毛毛雨/细雨", "310": "暴雨", "311": "大暴雨",
    "312": "特大暴雨", "313": "冻雨", "314": "小到中雨", "315": "中到大雨",
    "316": "大到暴雨", "317": "暴雨到大暴雨", "318": "大暴雨到特大暴雨",
    "350": "阵雨", "351": "强阵雨", "399": "雨",
    "400": "小雪", "401": "中雪", "402": "大雪", "403": "暴雪",
    "404": "雨夹雪", "405": "雨雪天气", "406": "阵雨夹雪", "407": "阵雪",
    "408": "小到中雪", "409": "中到大雪", "410": "大到暴雪",
    "456": "阵雨夹雪", "457": "阵雪", "499": "雪",
    "500": "薄雾", "501": "雾", "502": "霾", "503": "扬沙", "504": "浮尘",
    "507": "沙尘暴", "508": "强沙尘暴", "509": "浓雾", "510": "强浓雾",
    "511": "中度霾", "512": "重度霾", "513": "严重霾", "514": "大雾",
    "515": "特强浓雾", "900": "热", "901": "冷", "999": "未知",
}


def fetch_city_info_cached(location, api_key, api_host, cache_pool):
    """获取城市信息（缓存版本）"""
    # 先尝试从缓存获取
    cached_city_info = cache_pool.get_city_info(location)
    if cached_city_info:
        logger.bind(tag=TAG).debug(f"使用缓存的城市信息: {location}")
        return cached_city_info
    
    # 缓存未命中，调用API
    logger.bind(tag=TAG).info(f"调用API获取城市信息: {location}")
    url = f"https://{api_host}/geo/v2/city/lookup?key={api_key}&location={location}&lang=zh"
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        city_info = data.get("location", [])[0] if data.get("location") else None
        
        if city_info:
            # 缓存结果
            cache_pool.set_city_info(location, city_info)
            logger.bind(tag=TAG).info(f"城市信息已缓存: {location}")
        
        return city_info
        
    except Exception as e:
        logger.bind(tag=TAG).error(f"获取城市信息失败: {location}, 错误: {e}")
        return None


def fetch_and_parse_weather_cached(city_info, cache_pool, force_refresh=False):
    """获取并解析天气信息（缓存版本）"""
    location = city_info.get("name", "未知")
    
    # 检查是否强制刷新
    if not force_refresh:
        # 先尝试从缓存获取
        cached_weather_data = cache_pool.get_weather_data(location)
        if cached_weather_data:
            logger.bind(tag=TAG).debug(f"使用缓存的天气数据: {location}")
            return cached_weather_data
    
    # 缓存未命中或强制刷新，爬取页面
    logger.bind(tag=TAG).info(f"爬取天气页面: {location}")
    
    try:
        response = requests.get(city_info["fxLink"], headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        # 解析天气信息
        weather_data = parse_weather_info(soup)
        
        if weather_data and weather_data[0]:  # 确保解析成功
            # 包装数据用于缓存
            cached_data = {
                "city_name": weather_data[0],
                "current_abstract": weather_data[1],
                "current_basic": weather_data[2],
                "temps_list": weather_data[3]
            }
            
            # 缓存结果
            cache_pool.set_weather_data(location, cached_data)
            logger.bind(tag=TAG).info(f"天气数据已缓存: {location}")
            
            return cached_data
        
        return None
        
    except Exception as e:
        logger.bind(tag=TAG).error(f"获取天气页面失败: {location}, 错误: {e}")
        return None


def parse_weather_info(soup):
    """解析天气信息（从原版复制）"""
    try:
        city_name = soup.select_one("h1.c-submenu__location").get_text(strip=True)

        current_abstract = soup.select_one(".c-city-weather-current .current-abstract")
        current_abstract = (
            current_abstract.get_text(strip=True) if current_abstract else "未知"
        )

        current_basic = {}
        for item in soup.select(
            ".c-city-weather-current .current-basic .current-basic___item"
        ):
            parts = item.get_text(strip=True, separator=" ").split(" ")
            if len(parts) == 2:
                key, value = parts[1], parts[0]
                current_basic[key] = value

        temps_list = []
        for row in soup.select(".city-forecast-tabs__row")[:7]:  # 取前7天的数据
            date = row.select_one(".date-bg .date").get_text(strip=True)
            weather_code = (
                row.select_one(".date-bg .icon")["src"].split("/")[-1].split(".")[0]
            )
            weather = WEATHER_CODE_MAP.get(weather_code, "未知")
            temps = [span.get_text(strip=True) for span in row.select(".tmp-cont .temp")]
            high_temp, low_temp = (temps[0], temps[-1]) if len(temps) >= 2 else (None, None)
            temps_list.append((date, weather, high_temp, low_temp))

        return city_name, current_abstract, current_basic, temps_list
        
    except Exception as e:
        logger.bind(tag=TAG).error(f"解析天气信息失败: {e}")
        return None, None, None, []


def format_weather_report(weather_data, cache_hit=False):
    """格式化天气报告"""
    city_name = weather_data.get("city_name", "未知")
    current_abstract = weather_data.get("current_abstract", "未知")
    current_basic = weather_data.get("current_basic", {})
    temps_list = weather_data.get("temps_list", [])
    
    # 添加缓存标识
    cache_indicator = "📋 [缓存数据]" if cache_hit else "🌐 [实时数据]"
    
    weather_report = f"{cache_indicator} 您查询的位置是：{city_name}\n\n当前天气: {current_abstract}\n"

    # 添加有效的当前天气参数
    if current_basic:
        weather_report += "详细参数：\n"
        for key, value in current_basic.items():
            if value != "0":  # 过滤无效值
                weather_report += f"  · {key}: {value}\n"

    # 添加7天预报
    weather_report += "\n未来7天预报：\n"
    for date, weather, high, low in temps_list:
        weather_report += f"{date}: {weather}，气温 {low}~{high}\n"

    # 提示语
    weather_report += "\n（如需某一天的具体天气，请告诉我日期）"
    
    return weather_report


@register_function("get_weather_cached", GET_WEATHER_CACHED_FUNCTION_DESC, ToolType.SYSTEM_CTL)
def get_weather_cached(conn, location: str = None, lang: str = "zh_CN", force_refresh: bool = False):
    """缓存优化版本的天气查询"""
    try:
        # 获取配置
        api_host = conn.config["plugins"]["get_weather"].get("api_host", "mj7p3y7naa.re.qweatherapi.com")
        api_key = conn.config["plugins"]["get_weather"].get("api_key", "a861d0d5e7bf4ee1a83d9a9e4f96d4da")
        default_location = conn.config["plugins"]["get_weather"]["default_location"]
        client_ip = conn.client_ip
        
        # 初始化缓存池
        cache_config = conn.config["plugins"].get("weather_cache", {})
        cache_pool = get_weather_cache_pool(cache_config)
        
        # 确定查询位置
        if not location:
            if client_ip:
                ip_info = get_ip_info(client_ip, logger)
                location = ip_info.get("city") if ip_info and "city" in ip_info else default_location
            else:
                location = default_location
        
        logger.bind(tag=TAG).info(f"查询天气: {location}, 强制刷新: {force_refresh}")
        
        # 获取城市信息（使用缓存）
        city_info = fetch_city_info_cached(location, api_key, api_host, cache_pool)
        if not city_info:
            return ActionResponse(
                Action.REQLLM, f"未找到相关的城市: {location}，请确认地点是否正确", None
            )
        
        # 获取天气数据（使用缓存）
        weather_data = fetch_and_parse_weather_cached(city_info, cache_pool, force_refresh)
        if not weather_data:
            return ActionResponse(Action.REQLLM, None, "请求失败")
        
        # 检查是否来自缓存
        cache_hit = not force_refresh and cache_pool.get_weather_data(location) is not None
        
        # 格式化报告
        weather_report = format_weather_report(weather_data, cache_hit)
        
        # 添加缓存统计信息（调试模式）
        if conn.config.get("log", {}).get("log_level") == "DEBUG":
            cache_stats = cache_pool.get_cache_info()
            weather_report += f"\n\n{cache_stats}"
        
        logger.bind(tag=TAG).info(f"天气查询完成: {location}, 缓存命中: {cache_hit}")
        
        return ActionResponse(Action.REQLLM, weather_report, None)
        
    except Exception as e:
        logger.bind(tag=TAG).error(f"天气查询失败: {str(e)}")
        return ActionResponse(Action.REQLLM, f"天气查询出现错误: {str(e)}", None)


# 添加缓存管理的辅助函数
def get_cache_stats():
    """获取缓存统计信息"""
    try:
        cache_pool = get_weather_cache_pool()
        return cache_pool.get_stats()
    except Exception as e:
        logger.bind(tag=TAG).error(f"获取缓存统计失败: {e}")
        return None


def clear_weather_cache():
    """清空天气缓存"""
    try:
        cache_pool = get_weather_cache_pool()
        cache_pool.clear_cache()
        logger.bind(tag=TAG).info("天气缓存已清空")
        return True
    except Exception as e:
        logger.bind(tag=TAG).error(f"清空缓存失败: {e}")
        return False 