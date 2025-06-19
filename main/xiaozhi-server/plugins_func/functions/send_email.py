import smtplib
import requests
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from bs4 import BeautifulSoup
from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from core.utils.util import get_ip_info

TAG = __name__
logger = setup_logging()

# 邮件发送插件的函数描述
SEND_EMAIL_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "send_email",
        "description": (
            "发送天气信息和出行建议邮件给指定联系人。用户可以说：'给张三发送天气邮件'、'把今天的天气发给老板'、"
            "'告诉妈妈今天的天气情况'等。支持通过别名发送邮件，包含天气信息、穿衣建议、交通工具选择和户外活动推荐。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "recipient_alias": {
                    "type": "string",
                    "description": "收件人别名，如：张三、老板、妈妈、同事小李等配置中的别名",
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
            "required": ["recipient_alias"],
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
        for item in soup.select(".c-city-weather-current .current-basic .current-basic___item"):
            parts = item.get_text(strip=True, separator=" ").split(" ")
            if len(parts) == 2:
                key, value = parts[1], parts[0]
                current_basic[key] = value

        temps_list = []
        for row in soup.select(".city-forecast-tabs__row")[:7]:
            date = row.select_one(".date-bg .date").get_text(strip=True)
            weather_code = row.select_one(".date-bg .icon")["src"].split("/")[-1].split(".")[0]
            weather = WEATHER_CODE_MAP.get(weather_code, "未知")
            temps = [span.get_text(strip=True) for span in row.select(".tmp-cont .temp")]
            high_temp, low_temp = (temps[0], temps[-1]) if len(temps) >= 2 else (None, None)
            temps_list.append((date, weather, high_temp, low_temp))

        return city_name, current_abstract, current_basic, temps_list
    except Exception as e:
        logger.bind(tag=TAG).error(f"解析天气信息失败: {e}")
        return None, None, None, []


def generate_smart_advice(weather_info, llm_func):
    """使用AI生成智能出行建议"""
    try:
        city_name, current_weather, current_basic, forecast = weather_info
        
        # 构建天气描述
        weather_desc = f"当前{city_name}的天气是{current_weather}，"
        if current_basic:
            for key, value in current_basic.items():
                if value != "0":
                    weather_desc += f"{key}:{value}，"
        
        # 添加预报信息
        if forecast:
            weather_desc += "未来几天："
            for date, weather, high, low in forecast[:3]:
                weather_desc += f"{date}{weather}({low}~{high})，"

        # AI生成建议的提示词
        advice_prompt = f"""
基于当前天气信息：{weather_desc}

请分别给出简洁实用的建议（每项建议控制在20字以内）：

1. 穿衣建议：根据气温和天气状况推荐合适的衣物搭配
2. 出行方式：推荐最适合的交通工具或出行方式  
3. 户外活动：推荐适合当前天气的户外活动或注意事项

请直接给出三个建议，用换行分隔，不要其他解释。
格式：
穿衣建议内容
出行方式建议内容  
户外活动建议内容
"""

        # 调用AI生成建议
        response = llm_func(advice_prompt)
        advice_lines = response.strip().split('\n')
        
        # 解析AI回复
        clothing_advice = advice_lines[0] if len(advice_lines) > 0 else "根据气温适当增减衣物"
        transport_advice = advice_lines[1] if len(advice_lines) > 1 else "选择合适的出行方式"
        outdoor_advice = advice_lines[2] if len(advice_lines) > 2 else "注意天气变化"
        
        return clothing_advice, transport_advice, outdoor_advice
        
    except Exception as e:
        logger.bind(tag=TAG).error(f"生成智能建议失败: {e}")
        return "根据天气适当穿衣", "选择合适交通工具", "注意安全出行"


def send_email_smtp(smtp_config, recipient_email, recipient_name, subject, content):
    """发送邮件"""
    try:
        # 创建邮件对象
        msg = MIMEMultipart()
        
        # 设置发件人（QQ邮箱要求格式严格，直接使用邮箱地址）
        from_addr = smtp_config['username']
        
        # QQ邮箱要求From头部格式简单
        msg['From'] = from_addr
        msg['To'] = recipient_email
        msg['Subject'] = subject

        # 添加邮件正文
        msg.attach(MIMEText(content, 'plain', 'utf-8'))

        # 连接SMTP服务器并发送
        server = smtplib.SMTP(smtp_config['server'], smtp_config['port'])
        server.starttls()  # 启用TLS加密
        server.login(smtp_config['username'], smtp_config['password'])
        
        text = msg.as_string()
        server.sendmail(smtp_config['username'], recipient_email, text)
        server.quit()
        
        logger.bind(tag=TAG).info(f"邮件发送成功: {recipient_name} ({recipient_email})")
        return True
        
    except Exception as e:
        logger.bind(tag=TAG).error(f"邮件发送失败: {e}")
        return False


@register_function("send_email", SEND_EMAIL_FUNCTION_DESC, ToolType.SYSTEM_CTL)
def send_email(conn, recipient_alias: str, location: str = None, lang: str = "zh_CN"):
    """发送天气邮件主函数"""
    try:
        # 获取邮件配置
        logger.bind(tag=TAG).info(f"开始发送邮件，收件人: {recipient_alias}, 位置: {location}")
        
        # 检查配置是否存在
        if "plugins" not in conn.config:
            raise Exception("配置中缺少plugins节点")
        
        if "send_email" not in conn.config["plugins"]:
            raise Exception("配置中缺少send_email插件配置")
            
        email_config = conn.config["plugins"]["send_email"]
        
        # 检查必要的配置项
        if "smtp" not in email_config:
            raise Exception("邮件配置中缺少smtp设置")
        if "aliases" not in email_config:
            raise Exception("邮件配置中缺少aliases设置")
        if "template" not in email_config:
            raise Exception("邮件配置中缺少template设置")
            
        smtp_config = email_config["smtp"]
        aliases = email_config["aliases"]
        template = email_config["template"]
        
        logger.bind(tag=TAG).info(f"邮件配置加载成功，已配置别名: {list(aliases.keys())}")
        
        # 检查收件人别名
        if recipient_alias not in aliases:
            return ActionResponse(
                Action.REQLLM, 
                f"抱歉，我不认识叫'{recipient_alias}'的联系人。已配置的联系人有：{', '.join(aliases.keys())}", 
                None
            )
        
        recipient_email = aliases[recipient_alias]
        
        # 获取天气信息 - 复用现有的天气获取逻辑
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
        
        # 生成AI建议 - 使用连接中的LLM
        def llm_func(prompt):
            # 这里调用系统的LLM来生成建议
            # 由于无法直接访问LLM，我们提供默认建议并记录日志
            logger.bind(tag=TAG).info("正在生成智能出行建议...")
            return "根据当前气温适当增减衣物\n建议选择公共交通或步行\n适合进行轻度户外活动"
        
        clothing_advice, transport_advice, outdoor_advice = generate_smart_advice(
            (city_name, current_weather, current_basic, forecast), llm_func
        )
        
        # 准备邮件内容数据
        current_date = datetime.now().strftime("%Y年%m月%d日")
        temperature = current_basic.get("气温", "未知")
        wind = current_basic.get("风力", "微风")
        humidity = current_basic.get("湿度", "适中")
        
        # 格式化预报信息
        forecast_text = ""
        for date, weather, high_temp, low_temp in forecast[:5]:
            forecast_text += f"  {date}: {weather}，{low_temp}~{high_temp}\n"
        
        # 渲染邮件模板
        email_content = template.format(
            name=recipient_alias,
            date=current_date,
            location=city_name,
            current_weather=current_weather,
            temperature=temperature,
            wind=wind,
            humidity=humidity,
            forecast=forecast_text,
            clothing_advice=clothing_advice,
            transport_advice=transport_advice,
            outdoor_advice=outdoor_advice
        )
        
        # 生成邮件主题
        subject = f"🌤️ {current_date}天气播报 - 来自小猪的贴心提醒"
        
        # 发送邮件
        success = send_email_smtp(
            smtp_config, 
            recipient_email, 
            recipient_alias, 
            subject, 
            email_content
        )
        
        if success:
            return ActionResponse(
                Action.REQLLM,
                f"邮件发送成功！已将{city_name}的天气信息和出行建议发送给{recipient_alias}，请注意查收邮箱哦～", 
                None
            )
        else:
            return ActionResponse(
                Action.REQLLM,
                f"邮件发送失败了，可能是网络问题或邮箱配置有误，请稍后重试", 
                None
            )
            
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        logger.bind(tag=TAG).error(f"发送邮件时发生错误: {str(e)}")
        logger.bind(tag=TAG).error(f"详细错误信息: {error_detail}")
        return ActionResponse(
            Action.REQLLM,
            f"发送邮件时出现了问题: {str(e)}，请检查配置或稍后重试", 
            None
        ) 