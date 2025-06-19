import sqlite3
import requests
import json
from datetime import datetime
from bs4 import BeautifulSoup
from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from core.utils.util import get_ip_info

# 尝试导入MySQL依赖
try:
    import pymysql
    MYSQL_AVAILABLE = True
except ImportError:
    MYSQL_AVAILABLE = False

TAG = __name__
logger = setup_logging()

# 天气数据存储插件的函数描述
SAVE_WEATHER_TO_DB_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "save_weather_to_db",
        "description": (
            "将天气信息保存到指定的业务系统数据库中。用户可以说：'把今天的天气存储到ERP系统'、"
            "'将广州的天气保存到客户管理系统'、'存储武汉的天气到办公系统'等。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "system_alias": {
                    "type": "string",
                    "description": "业务系统别名，如：ERP系统、客户管理系统、办公系统等配置中的别名",
                },
                "location": {
                    "type": "string",
                    "description": "天气查询位置，可选参数。如果不提供则使用默认位置或IP解析位置",
                },
                "lang": {
                    "type": "string",
                    "description": "返回用户使用的语言code，例如zh_CN，默认zh_CN",
                },
            },
            "required": ["system_alias"],
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
    "308": "极端降雨", "309": "毛毛雨", "310": "暴雨", "311": "大暴雨",
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


def fetch_city_info(location, api_key, api_host):
    """获取城市信息"""
    url = f"https://{api_host}/geo/v2/city/lookup?key={api_key}&location={location}&lang=zh"
    response = requests.get(url, headers=HEADERS).json()
    return response.get("location", [])[0] if response.get("location") else None


def fetch_weather_page(url):
    """获取天气页面信息"""
    response = requests.get(url, headers=HEADERS)
    return BeautifulSoup(response.text, "html.parser") if response.ok else None


def parse_weather_info(soup):
    """解析天气信息"""
    try:
        city_name = soup.select_one("h1.c-submenu__location").get_text(strip=True)
        current_abstract = soup.select_one(".c-city-weather-current .current-abstract")
        current_abstract = current_abstract.get_text(strip=True) if current_abstract else "未知"

        current_basic = {}
        # 尝试多种选择器来解析基本信息
        basic_selectors = [
            ".c-city-weather-current .current-basic .current-basic___item",
            ".c-city-weather-current .current-basic .item",
            ".current-basic .item",
            ".current-info .item"
        ]
        
        for selector in basic_selectors:
            items = soup.select(selector)
            if items:
                for item in items:
                    text = item.get_text(strip=True, separator=" ")
                    logger.bind(tag=TAG).debug(f"解析基本信息项: {text}")
                    parts = text.split(" ")
                    if len(parts) >= 2:
                        # 尝试不同的解析方式
                        if "°" in text or "℃" in text:  # 温度信息
                            temp_value = None
                            for part in parts:
                                if "°" in part or "℃" in part:
                                    temp_value = part
                                    break
                            if temp_value:
                                current_basic["气温"] = temp_value
                        elif "%" in text:  # 湿度信息
                            for part in parts:
                                if "%" in part:
                                    current_basic["湿度"] = part
                                    break
                        elif any(wind_word in text for wind_word in ["风", "级", "m/s"]):  # 风力信息
                            current_basic["风力"] = text
                        else:
                            # 默认解析方式
                            key, value = parts[-1], parts[0]
                            current_basic[key] = value
                break
        
        # 如果基本信息解析失败，尝试从其他地方获取温度
        if "气温" not in current_basic:
            # 尝试查找温度显示
            temp_elements = soup.select(".temp, .temperature, .current-temp")
            for temp_elem in temp_elements:
                temp_text = temp_elem.get_text(strip=True)
                if temp_text and ("°" in temp_text or "℃" in temp_text):
                    current_basic["气温"] = temp_text
                    break

        temps_list = []
        for row in soup.select(".city-forecast-tabs__row")[:7]:
            try:
                date = row.select_one(".date-bg .date").get_text(strip=True)
                icon_elem = row.select_one(".date-bg .icon")
                weather_code = icon_elem["src"].split("/")[-1].split(".")[0] if icon_elem else "999"
                weather = WEATHER_CODE_MAP.get(weather_code, "未知")
                temps = [span.get_text(strip=True) for span in row.select(".tmp-cont .temp")]
                high_temp, low_temp = (temps[0], temps[-1]) if len(temps) >= 2 else (None, None)
                temps_list.append((date, weather, high_temp, low_temp))
            except Exception as row_e:
                logger.bind(tag=TAG).warning(f"解析天气预报行失败: {row_e}")
                continue

        logger.bind(tag=TAG).info(f"成功解析天气信息 - 城市: {city_name}, 当前天气: {current_abstract}, 基本信息: {current_basic}")
        return city_name, current_abstract, current_basic, temps_list
    except Exception as e:
        logger.bind(tag=TAG).error(f"解析天气信息失败: {e}")
        return None, None, None, []


def create_weather_table(conn, table_name):
    """创建天气数据表"""
    create_table_sql = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        location TEXT NOT NULL,
        weather_date DATE NOT NULL,
        current_weather TEXT,
        temperature TEXT,
        humidity TEXT,
        wind TEXT,
        forecast_data TEXT,
        created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        source TEXT DEFAULT 'xiaozhi-server'
    )
    """
    
    create_index_sql = f"""
    CREATE INDEX IF NOT EXISTS idx_{table_name}_location_date 
    ON {table_name} (location, weather_date)
    """
    
    conn.execute(create_table_sql)
    conn.execute(create_index_sql)
    conn.commit()


def create_mysql_weather_table(conn, table_name):
    """创建MySQL天气数据表"""
    create_table_sql = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        location VARCHAR(100) NOT NULL COMMENT '地点',
        weather_date DATE NOT NULL COMMENT '天气日期',
        current_weather VARCHAR(50) COMMENT '当前天气',
        temperature VARCHAR(20) COMMENT '气温',
        humidity VARCHAR(20) COMMENT '湿度',
        wind VARCHAR(50) COMMENT '风力',
        forecast_data TEXT COMMENT '预报数据JSON',
        created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        source VARCHAR(50) DEFAULT 'xiaozhi-server' COMMENT '数据来源',
        INDEX idx_location_date (location, weather_date),
        INDEX idx_created_time (created_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='天气数据表'
    """
    
    cursor = conn.cursor()
    cursor.execute(create_table_sql)
    conn.commit()


def save_weather_data_to_mysql(mysql_config, table_name, weather_data):
    """保存天气数据到MySQL数据库"""
    try:
        if not MYSQL_AVAILABLE:
            raise Exception("MySQL依赖包未安装，请执行：pip install pymysql")
        
        # 连接MySQL数据库
        conn = pymysql.connect(
            host=mysql_config['host'],
            port=mysql_config['port'],
            user=mysql_config['username'],
            password=mysql_config['password'],
            database=mysql_config['database'],
            charset='utf8mb4'
        )
        
        # 创建表（如果不存在）
        create_mysql_weather_table(conn, table_name)
        
        cursor = conn.cursor()
        
        # 检查是否已存在相同日期和地点的数据
        check_sql = f"""
        SELECT id FROM {table_name} 
        WHERE location = %s AND weather_date = %s
        """
        
        cursor.execute(check_sql, (weather_data['location'], weather_data['weather_date']))
        existing_record = cursor.fetchone()
        
        if existing_record:
            # 更新现有记录
            update_sql = f"""
            UPDATE {table_name} SET
                current_weather = %s,
                temperature = %s,
                humidity = %s,
                wind = %s,
                forecast_data = %s,
                updated_time = CURRENT_TIMESTAMP
            WHERE id = %s
            """
            cursor.execute(update_sql, (
                weather_data['current_weather'],
                weather_data['temperature'],
                weather_data['humidity'],
                weather_data['wind'],
                weather_data['forecast_data'],
                existing_record[0]
            ))
            logger.bind(tag=TAG).info(f"更新了{weather_data['location']}的天气数据")
        else:
            # 插入新记录
            insert_sql = f"""
            INSERT INTO {table_name} (
                location, weather_date, current_weather, temperature, 
                humidity, wind, forecast_data
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(insert_sql, (
                weather_data['location'],
                weather_data['weather_date'],
                weather_data['current_weather'],
                weather_data['temperature'],
                weather_data['humidity'],
                weather_data['wind'],
                weather_data['forecast_data']
            ))
            logger.bind(tag=TAG).info(f"新增了{weather_data['location']}的天气数据")
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        logger.bind(tag=TAG).error(f"保存天气数据到MySQL数据库失败: {e}")
        return False


def save_weather_data_to_sqlite(db_path, table_name, weather_data):
    """保存天气数据到SQLite数据库"""
    try:
        conn = sqlite3.connect(db_path)
        
        # 创建表（如果不存在）
        create_weather_table(conn, table_name)
        
        # 检查是否已存在相同日期和地点的数据
        check_sql = f"""
        SELECT id FROM {table_name} 
        WHERE location = ? AND weather_date = ?
        """
        
        cursor = conn.execute(check_sql, (weather_data['location'], weather_data['weather_date']))
        existing_record = cursor.fetchone()
        
        if existing_record:
            # 更新现有记录
            update_sql = f"""
            UPDATE {table_name} SET
                current_weather = ?,
                temperature = ?,
                humidity = ?,
                wind = ?,
                forecast_data = ?,
                updated_time = CURRENT_TIMESTAMP
            WHERE id = ?
            """
            conn.execute(update_sql, (
                weather_data['current_weather'],
                weather_data['temperature'],
                weather_data['humidity'],
                weather_data['wind'],
                weather_data['forecast_data'],
                existing_record[0]
            ))
            logger.bind(tag=TAG).info(f"更新了{weather_data['location']}的天气数据")
        else:
            # 插入新记录
            insert_sql = f"""
            INSERT INTO {table_name} (
                location, weather_date, current_weather, temperature, 
                humidity, wind, forecast_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """
            conn.execute(insert_sql, (
                weather_data['location'],
                weather_data['weather_date'],
                weather_data['current_weather'],
                weather_data['temperature'],
                weather_data['humidity'],
                weather_data['wind'],
                weather_data['forecast_data']
            ))
            logger.bind(tag=TAG).info(f"新增了{weather_data['location']}的天气数据")
        
        conn.commit()
        conn.close()
        return True
        
    except Exception as e:
        logger.bind(tag=TAG).error(f"保存天气数据到数据库失败: {e}")
        return False


@register_function("save_weather_to_db", SAVE_WEATHER_TO_DB_FUNCTION_DESC, ToolType.SYSTEM_CTL)
def save_weather_to_db(conn, system_alias: str, location: str = None, lang: str = "zh_CN"):
    """将天气数据保存到数据库主函数"""
    try:
        logger.bind(tag=TAG).info(f"开始保存天气数据，目标系统: {system_alias}, 位置: {location}")
        
        # 检查数据库配置是否存在
        if "plugins" not in conn.config or "save_weather_to_db" not in conn.config["plugins"]:
            raise Exception("配置中缺少plugins.save_weather_to_db节点")
        
        save_weather_config = conn.config["plugins"]["save_weather_to_db"]
        if "databases" not in save_weather_config:
            raise Exception("配置中缺少plugins.save_weather_to_db.databases节点")
        
        databases_config = save_weather_config["databases"]
        
        # 查找匹配的数据库配置
        target_db_config = None
        for db_key, db_config in databases_config.items():
            if db_config.get("alias") == system_alias:
                target_db_config = db_config
                break
        
        if not target_db_config:
            available_systems = [db_config.get("alias", db_key) for db_key, db_config in databases_config.items()]
            return ActionResponse(
                Action.REQLLM,
                f"抱歉，我找不到名为'{system_alias}'的业务系统。可用的系统有：{', '.join(available_systems)}",
                None
            )
        
        # 获取天气信息
        weather_config = conn.config["plugins"]["get_weather"]
        api_host = weather_config.get("api_host", "mj7p3y7naa.re.qweatherapi.com")
        api_key = weather_config.get("api_key", "a861d0d5e7bf4ee1a83d9a9e4f96d4da")
        default_location = weather_config["default_location"]
        client_ip = conn.client_ip
        
        # 确定查询位置
        if not location:
            if client_ip:
                ip_info = get_ip_info(client_ip, logger)
                location = ip_info.get("city") if ip_info and "city" in ip_info else default_location
            else:
                location = default_location
        
        # 获取城市信息
        city_info = fetch_city_info(location, api_key, api_host)
        if not city_info:
            return ActionResponse(
                Action.REQLLM,
                f"获取'{location}'的天气信息失败，请检查城市名称是否正确",
                None
            )
        
        # 获取天气页面并解析
        soup = fetch_weather_page(city_info["fxLink"])
        if not soup:
            return ActionResponse(
                Action.REQLLM,
                "获取天气信息失败，请稍后重试",
                None
            )
        
        city_name, current_weather, current_basic, forecast = parse_weather_info(soup)
        if not city_name:
            return ActionResponse(
                Action.REQLLM,
                "解析天气信息失败，请稍后重试",
                None
            )
        
        # 准备天气数据
        current_date = datetime.now().strftime("%Y-%m-%d")
        
        # 添加调试信息，查看解析出的数据结构
        logger.bind(tag=TAG).info(f"解析出的current_basic数据: {current_basic}")
        
        # 优化温度提取逻辑，尝试多种可能的字段名
        temperature = "未知"
        for temp_key in ["气温", "温度", "当前温度", "实时温度"]:
            if temp_key in current_basic:
                temperature = current_basic[temp_key]
                break
        
        # 如果current_basic中没有温度信息，尝试从forecast中获取当天温度
        if temperature == "未知" and forecast:
            today_forecast = forecast[0] if forecast else None
            if today_forecast and len(today_forecast) >= 4:
                high_temp, low_temp = today_forecast[2], today_forecast[3]
                if high_temp and low_temp:
                    temperature = f"{low_temp}~{high_temp}"
                elif high_temp:
                    temperature = f"{high_temp}"
        
        # 如果还是没有温度信息，尝试调用和风天气API获取实时温度
        if temperature == "未知":
            try:
                weather_api_url = f"https://{api_host}/weather/v7/now?key={api_key}&location={city_info['id']}"
                api_response = requests.get(weather_api_url, headers=HEADERS).json()
                if api_response.get("code") == "200" and api_response.get("now"):
                    now_weather = api_response["now"]
                    temp_value = now_weather.get("temp")
                    if temp_value:
                        temperature = f"{temp_value}°C"
                        logger.bind(tag=TAG).info(f"通过API获取到温度: {temperature}")
            except Exception as api_e:
                logger.bind(tag=TAG).warning(f"API获取温度失败: {api_e}")
        
        wind = current_basic.get("风力", current_basic.get("风", "微风"))
        humidity = current_basic.get("湿度", "适中")
        
        logger.bind(tag=TAG).info(f"最终提取的温度: {temperature}, 风力: {wind}, 湿度: {humidity}")
        
        # 格式化预报数据为JSON
        forecast_data = []
        for date, weather, high_temp, low_temp in forecast[:5]:
            forecast_data.append({
                "date": date,
                "weather": weather,
                "high_temp": high_temp,
                "low_temp": low_temp
            })
        
        weather_data = {
            "location": city_name,
            "weather_date": current_date,
            "current_weather": current_weather,
            "temperature": temperature,
            "humidity": humidity,
            "wind": wind,
            "forecast_data": json.dumps(forecast_data, ensure_ascii=False)
        }
        
        # 保存到数据库
        db_type = target_db_config.get("type", "sqlite")
        table_name = target_db_config["table_name"]
        
        if db_type == "mysql":
            success = save_weather_data_to_mysql(target_db_config, table_name, weather_data)
        elif db_type == "sqlite":
            db_path = target_db_config["database"]
            success = save_weather_data_to_sqlite(db_path, table_name, weather_data)
        else:
            return ActionResponse(
                Action.REQLLM,
                f"暂不支持{db_type}类型的数据库，目前支持MySQL和SQLite",
                None
            )
        
        if success:
            return ActionResponse(
                Action.REQLLM,
                f"天气数据保存成功！已将{city_name}的天气信息存储到{system_alias}中，数据包括：{current_weather}，{temperature}",
                None
            )
        else:
            return ActionResponse(
                Action.REQLLM,
                f"天气数据保存失败，请稍后重试",
                None
            )
            
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        logger.bind(tag=TAG).error(f"保存天气数据时发生错误: {str(e)}")
        logger.bind(tag=TAG).error(f"详细错误信息: {error_detail}")
        return ActionResponse(
            Action.REQLLM,
            f"保存天气数据时出现了问题: {str(e)}，请检查配置或稍后重试",
            None
        ) 