"""Quick test: verify LLM API works for alignment"""
import requests
import json

url = "https://aigw-int.netease.com/v1/chat/completions"
headers = {
    "Authorization": "Bearer 831nu8vxwh9otek3.yco18hmkgb0tpayykqi6s47j8cvc9trj",
    "Content-Type": "application/json"
}

prompt = """你是一个文本对齐专家。请帮我判断以下ASR识别文本对应原文的哪些行。

ASR识别结果（可能有识别错误、同音字替换、漏字）：
  "皆さんこんにちは金です"

翻译参考（英文翻译）：
  "Hello everyone, I'm Kim."

候选原文行（带行号）：
  [0] 皆さんこんにちは。
  [1] アカネです。
  [2] 今回は不動産で働いている方にご協力いただいて、
  [3] 私が賃貸のお部屋を探している場面を撮影させてもらいました。

请找出ASR文本对应的原文行范围。一条ASR通常对应1-5行原文。

请以JSON格式回答：
{"start_line": <起始行号>, "end_line": <结束行号>, "explanation": "<简短说明>"}

只返回JSON，不要其他内容。"""

data = {
    "model": "deepseek-v3.2-chat-yd-251201",
    "messages": [{"role": "user", "content": prompt}],
    "max_tokens": 256,
    "temperature": 0.1,
    "stream": False
}

print("Testing LLM API...")
response = requests.post(url, headers=headers, json=data, timeout=30)
print(f"Status: {response.status_code}")

if response.status_code == 200:
    result = response.json()
    content = result["choices"][0]["message"]["content"]
    print(f"Response: {content}")
    # Try to parse JSON
    import re
    match = re.search(r'\{[^}]+\}', content)
    if match:
        parsed = json.loads(match.group())
        print(f"Parsed: {parsed}")
        print("SUCCESS - LLM API is working!")
    else:
        print("WARNING: Could not parse JSON from response")
else:
    print(f"FAILED: {response.text}")
