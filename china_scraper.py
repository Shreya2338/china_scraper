import requests

url = "http://www.icama.org.cn/zwgk/nyzj/nyzjcpdj/"

headers = {
    "User-Agent": "Mozilla/5.0"
}

response = requests.get(url, headers=headers)
response.encoding = "utf-8"

print(response.text[:2000])