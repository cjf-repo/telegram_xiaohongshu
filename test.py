from openai import OpenAI
import base64
import mimetypes
import json

# ===== 这里改成你的信息 =====
API_KEY = "ms-396263fe-326d-4cbe-b7fa-7d97b581b17b"
BASE_URL = "https://api-inference.modelscope.cn/v1"
MODEL_NAME = "moonshotai/Kimi-K2.5"
IMAGE_PATH = r"/mnt/e/telegram_media_downloader/Y.JIA伊嘉STUDIO （批发频道）/2026_01/4301 - AgAD6gtrG8zxsVY.jpg"                # 改成你的本地图片路径
PROMPT = "请描述这张图片里有什么，尽量简洁。"
# =========================


def image_file_to_data_url(image_path: str) -> str:
    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type:
        mime_type = "image/jpeg"

    with open(image_path, "rb") as f:
        image_bytes = f.read()

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"


def main():
    client = OpenAI(
        api_key=API_KEY,
        base_url=BASE_URL,
    )

    data_url = image_file_to_data_url(IMAGE_PATH)

    try:
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url
                            }
                        }
                    ]
                }
            ]
        )

        print("===== 原始返回 =====")
        try:
            print(resp.model_dump_json(indent=2))
        except Exception:
            print(resp)

        print("\n===== 提取结果 =====")
        if resp and resp.choices and resp.choices[0].message:
            msg = resp.choices[0].message
            content = getattr(msg, "content", None)
            if content:
                print(content)
            else:
                print("message 存在，但 content 为空：")
                print(msg)
        else:
            print("没有拿到有效的 choices/message")

    except Exception as e:
        print("调用失败：")
        print(type(e).__name__, str(e))


if __name__ == "__main__":
    main()