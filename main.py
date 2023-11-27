import asyncio
import json
import random
from datetime import datetime
from contextvars import copy_context
from functools import partial, wraps
from hashlib import md5
from pathlib import Path
from typing import Callable, Coroutine, ParamSpec, TypeVar
from urllib.parse import quote_plus

from aiohttp import ClientError, ClientSession
from bs4 import BeautifulSoup
from lxml import etree
from selenium.webdriver import Chrome, ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from src.log import get_logger
from src.stealth import js_stealth
from src.user_agent import get_user_agent_of_pc


# 待爬取的商品名
ITEM_NAME = quote_plus("RTX4060Ti")
DATA = Path("data")
IMAGES = DATA / "images"
IMAGES.mkdir(parents=True, exist_ok=True)
FILE = DATA / f"{ITEM_NAME}.json"
if not FILE.exists():
    FILE.write_text("[]")

COOKIES = []
USER_AGENT = get_user_agent_of_pc()


P = ParamSpec("P")
R = TypeVar("R")


def run_sync(call: Callable[P, R]) -> Callable[P, Coroutine[None, None, R]]:
    """将同步函数包装为异步函数"""

    @wraps(call)
    async def _wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        loop = asyncio.get_running_loop()
        pfunc = partial(call, *args, **kwargs)
        context = copy_context()
        result = await loop.run_in_executor(None, partial(context.run, pfunc))
        return result

    return _wrapper


def fix_cookies(cks: list[dict]) -> list[dict]:
    """对Cookies进行修饰以确保其正确性"""
    cookies = []
    for ck in cks:
        cookies.append(
            {
                "domain": ".jd.com",
                "name": ck.get("name"),
                "value": ck.get("value"),
                "expires": "",
                "path": "/",
                "httpOnly": False,
                "HostOnly": False,
                "Secure": False,
            }
        )
    return cookies


async def load_cookies():
    """
    从文件加载Cookies

    若文件不存在，则打开浏览器提示用户登录
    """
    fp = Path("cookies.json")
    if fp.exists():
        cks = json.loads(fp.read_text())
    else:
        driver = await create_driver(False, False)
        await run_sync(driver.get)(
            "https://passport.jd.com/new/login.aspx?ReturnUrl=https%3A%2F%2Fwww.jd.com%2F"
        )
        input()
        cks = driver.get_cookies()

    COOKIES[:] = fix_cookies(cks)
    fp.write_text(json.dumps(COOKIES))


def safe_run(
    call: Callable[P, Coroutine[None, None, R]], return_on_err: R
) -> Callable[P, Coroutine[None, None, R]]:
    """包装一个异步函数，捕获其运行错误并输出"""

    @wraps(call)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await call(*args, **kwargs)
        except Exception as e:
            logger = get_logger("SafeRun").opt(colors=True, exception=e)
            logger.error(f"运行函数 {call.__name__} 时发生错误")
            logger.error(f"    args: {args}, kwargs: {kwargs}")
            err_msg = f"    {e.__class__.__name__}: {e}"
            logger.error(err_msg.replace("<", "\\<"))
            return return_on_err

    return wrapper


async def scroll(driver: Chrome, grab: Callable[[], R]) -> R:
    """通过向网页发送向下翻页指令，加载全部数据"""

    # 向页面的body发送PAGE_DOWN指令
    send_page_down = run_sync(
        lambda: driver.find_element(By.TAG_NAME, "body").send_keys(Keys.PAGE_DOWN)
    )

    # 点击重试按钮，加载后30条数据
    @run_sync
    def click_btn():
        xpath = '//*[@id="J_scroll_loading"]/span/a'
        try:
            driver.find_element(By.XPATH, xpath).click()
        except:
            pass

    data = grab()
    for _ in range(12):
        await click_btn()
        await send_page_down()
        # 每次翻页时爬取一次当前页面数据
        newdata = grab()
        if 0 in newdata:  # type: ignore
            return data
        data = newdata
        # 随机等待0.5~1.5秒，规避反爬
        await asyncio.sleep(random.uniform(0.5, 1.5))
        await click_btn()
        # 触发反爬登录跳转
        # 增加Cookie后不会触发
        if "passport.jd" in driver.current_url or "cfe.m.jd" in driver.current_url:
            break
    return data


async def create_driver(headless: bool = True, load_cookies: bool = True):
    """创建driver"""
    logger = get_logger("Driver")
    options = ChromeOptions()
    options.add_argument("user-agent=" + USER_AGENT)  # 指定UserAgent
    if headless:
        logger.info("以无头模式创建driver")
        options.add_argument("--headless")  # 禁用窗口
    options.add_argument("--disable-gpu")  # 禁用GPU
    options.add_argument("disble-infobars")  # 关闭上方调试信息栏
    options.add_argument("log-level=4")  # 指定日志等级，减少输出
    options.add_argument("--incognito")  # 无痕模式
    options.add_argument("disable-cache")  # 禁用缓存
    options.add_argument("--disable-extensions")  # 禁用插件
    options.add_argument("--disable-popup-blocking")  # 禁用弹窗
    options.add_argument("--disable-redirects")  # 禁用跳转(效果甚微)
    options.add_argument("--disable-blink-features")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option(
        "excludeSwitches", ["enable-automation"]
    )  # 反爬的某个选项(?)
    svc = Service(executable_path="./chromedriver.exe")

    # 创建WebDriver
    driver = await run_sync(Chrome)(options=options, service=svc)
    # 执行CDP命令，将webdriver标记设为undefined
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
    )
    # 执行CDP命令，加载js模块stealth，反反爬
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": js_stealth},
    )
    # 最大化窗口
    driver.maximize_window()
    # 打开京东首页并写入Cookies
    await run_sync(driver.get)("https://jd.com/")
    if load_cookies and COOKIES:
        logger.info("向浏览器添加cookies")
        for ck in COOKIES:
            driver.add_cookie(ck)
    await run_sync(driver.get)("https://jd.com/")
    return driver


async def jd_spider(page: int):
    # 使用loguru模块的日志记录器格式化输出
    logger = get_logger(f"Spider-{page+1}").opt(colors=True)
    logger.info(f"开始爬取页面: <g>{page+1}</g>")
    url = f"https://search.jd.com/Search?keyword={ITEM_NAME}&page={page*2+1}"
    logger.info(f"页面URL: <c>{url}</c>")
    # 创建WebDriver
    # 将参数中headless改为True即可隐藏浏览器窗口
    driver = await create_driver(headless=False)
    # 打开目标网页
    await run_sync(driver.get)(url)

    def grab():
        tree = etree.HTML(driver.page_source, None)
        # 通过XPath定位HTML元素，解析商品数据
        names = tree.xpath('//*[@id="J_goodsList"]/ul/li/div/div[3]/a/em')
        prices = tree.xpath('//*[@id="J_goodsList"]/ul/li/div/div[2]/strong/i/text()')
        hrefs = [
            f"https:{i}"
            for i in tree.xpath('//*[@id="J_goodsList"]/ul/li/div/div[1]/a/@href')
        ]
        comments = tree.xpath('//*[@id="J_goodsList"]/ul/li/div/div[4]/strong/a/text()')
        shops = tree.xpath('//*[@id="J_goodsList"]/ul/li/div/div[5]/span/a/text()')
        img_urls = [
            f"https:{i}"
            for i in tree.xpath('//*[@id="J_goodsList"]/ul/li/div/div[1]/a/img/@src')
        ]
        # 对于商品名，使用BeautifulSoup模块进一步解析，提取纯文本
        names = [
            BeautifulSoup(etree.tostring(name).decode("utf-8"), "lxml")
            .get_text(strip=True)
            .replace("\n", " ")
            .replace("\t", " ")
            .strip()
            for name in names
        ]
        data = (names, prices, hrefs, comments, shops, img_urls)
        info = tuple(map(len, data))
        # 输出本次解析获取到的数据量
        logger.info(f"爬取数据: <c>{info}</c>")
        return data

    result = await scroll(driver, grab)
    driver.close()
    # 检查是否触发反爬登录跳转，加入Cookies后不会触发
    if "passport.jd" in driver.current_url and min(map(len, result)) < 20:
        logger.warning("触发反爬跳转，重新开始爬取")
        return await jd_spider(page)
    # 检查是否触发人机验证
    if "cfe.m.jd" in driver.current_url:
        logger.warning("触发反爬验证码，请在弹出窗口通过验证后输入回车")
        # 打开一个新的窗口，提示用户进行人机验证
        driver_cfe = await create_driver(False)
        driver_cfe.get(driver.current_url)
        driver_cfe.execute_script(f'alert("触发反爬验证码，请在在浏览器中通过验证后，回到控制台输入回车");')
        input()
        driver_cfe.close()
        # 验证完成后重启当前爬取流程
        logger.warning("重新爬取当前页面...")
        return await jd_spider(page)

    # 将爬取到的数据格式化为字典数组
    names, prices, hrefs, comments, shops, img_urls = result
    newdata = [
        {
            "name": name,
            "price": price,
            "href": href,
            "comment": comment,
            "shop": shop,
            "img_url": img_url,
        }
        for name, price, href, comment, shop, img_url in zip(
            names, prices, hrefs, comments, shops, img_urls
        )
    ]
    logger.info(f"保存 <y>{len(newdata)}</y> 条数据到本地")
    # 将数据追加到本地JSON文件
    data = json.loads(FILE.read_text("utf-8"))  # type: list
    data.extend(newdata)
    FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # 下载页面中爬取到的图片链接
    logger.info(f"开始下载图片: len=<y>{len(img_urls)}</y>")
    # 使用异步HTTP库 aiohttp 的 ClientSession 创建会话
    # 会话使用随机User-Agent和与浏览器相同的Cookies
    async with ClientSession(
        headers={"User-Agent": get_user_agent_of_pc()},
        cookies={i["name"]: i["value"] for i in COOKIES},
    ) as session:
        # 定义函数用于保存单张图片
        async def get_img(url: str, name: str):
            key = 0
            while True:
                filename = md5((name + str(key)).encode("utf-8")).hexdigest()
                fp = IMAGES / (filename + "." + url.split(".")[-1])
                if not fp.exists():
                    fp.write_bytes(b"")
                    break
                key += 1
            fp.parent.mkdir(parents=True, exist_ok=True)
            try:
                async with session.get(url) as resp:
                    fp.write_bytes(await resp.read())
            except ClientError as e:
                print("下载图片失败:", e)
                print("图片URL:", url)
            except Exception as e:
                print("保存图片失败:", e)
                print("图片路径:", fp)

        # 每次创建5个协程，交由asyncio.gather()并发下载图片
        step = 5
        for i in range(len(img_urls) // 5):
            coros = [
                get_img(url, shops[idx] + names[idx])
                for idx, url in enumerate(img_urls[i * step : (i + 1) * step])
            ]
            await asyncio.gather(*coros)

    # 输出提示结束当前页面爬取
    logger.info(f"页面 <g>{page+1}</g> 爬取完成")


async def main():
    logger = get_logger("Main").opt(colors=True)
    logger.info("正在初始化京东爬虫...")
    # 初始化Cookies
    await load_cookies()

    # 使用safe_run包装函数
    spider_func = safe_run(jd_spider, None)
    total = 150  # 爬取共计150页数据
    step = 6  # 每次创建6个协程，并发爬取商品数据
    start = datetime.now()  # 记录开始时间
    logger.info(f"开始爬取京东商品: <g>{ITEM_NAME}</g>")
    logger.info(f"单次爬取页面数: <g>{step}</g>")
    for i in range(total // step):
        coros = [spider_func(page) for page in range(i * step, (i + 1) * step)]
        # 使用asyncio.gather()并发处理step个页面
        await asyncio.gather(*coros)

    # 计算耗时
    seconds = round((datetime.now() - start).total_seconds())
    minutes = seconds // 60
    hours = minutes // 60
    timestr = (
        str(hours).rjust(2, "0")
        + ":"
        + str(minutes % 60).rjust(2, "0")
        + ":"
        + str(seconds % 60).rjust(2, "0")
    )
    logger.success(f"商品 <g>{ITEM_NAME}</g> 爬取完成")
    logger.success(f"耗时: <y>{timestr}</y>")


if __name__ == "__main__":
    asyncio.run(main())
