import requests
from bs4 import BeautifulSoup

url = "http://www.icama.org.cn/zwgk/nyzj/nyzjcpdj/"

headers = {
    "User-Agent": "Mozilla/5.0"
}

response = requests.get(url, headers=headers)
response.encoding = "utf-8"

soup = BeautifulSoup(response.text, "html.parser")

links = soup.find_all("a")

data = []

for link in links:
    title = link.text.strip()
    href = link.get("href")

    if title and href and "html" in href:
        data.append({
            "title": title,
            "link": "http://www.icama.org.cn" + href
        })

print("TOTAL ITEMS:", len(data))

for item in data[:10]:
    print(item)