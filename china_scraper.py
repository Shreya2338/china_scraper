import requests
from bs4 import BeautifulSoup

url = "http://www.icama.org.cn/zwgk/nyzj/nyzjcpdj/"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "http://www.icama.org.cn/",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive"
}

session = requests.Session()
response = session.get(url, headers=headers)

response.encoding = "utf-8"

print(response.text[:1000])  # debug againpython china_scraper.py