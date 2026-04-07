# -*- coding: utf-8 -*-
import requests
import json

url = "https://aigw.netease.com/v1/chat/completions"
headers = {
    "Authorization": "Bearer 831nu8vxwh9otek3.yco18hmkgb0tpayykqi6s47j8cvc9trj",
    "Content-Type": "application/json"
}

data = {
    "model": "deepseek-v3.2-chat-yd-251201",
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "描述这张图片的内容"
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "detail": "low",
                        "url": "https://nie.res.netease.com/nie/gw/15v1/img/logo1_d853983.png"
                    }
                }
            ]
        }
    ],
    "max_tokens": 1000,
    "temperature": 0.7,
    "stream": False
}

response = requests.post(url, headers=headers, json=data)

if response.status_code == 200:
    result = response.json()
    content = result["choices"][0]["message"]["content"]
    # Write to file to avoid encoding issues
    with open("test_api_result.txt", "w", encoding="utf-8") as f:
        f.write(content)
        f.write("\n\n--- Full Response ---\n")
        f.write(json.dumps(result, ensure_ascii=False, indent=2))
    print("OK - result written to test_api_result.txt")
else:
    with open("test_api_result.txt", "w", encoding="utf-8") as f:
        f.write(f"Error: {response.status_code}\n")
        f.write(response.text)
    print(f"FAILED - {response.status_code}")
