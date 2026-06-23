"""Vercel Serverless Function：接收反馈并写入飞书多维表格"""

import json
import os
from http.server import BaseHTTPRequestHandler

import requests

FEISHU_APP_ID = os.environ["FEISHU_APP_ID"]
FEISHU_APP_SECRET = os.environ["FEISHU_APP_SECRET"]
BASE_TOKEN = os.environ["BASE_TOKEN"]
TABLE_ID = os.environ["TABLE_ID"]

_TOKEN_CACHE = {"token": None, "expire": 0}


def get_token():
    import time

    if _TOKEN_CACHE["token"] and time.time() < _TOKEN_CACHE["expire"]:
        return _TOKEN_CACHE["token"]
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=10,
    )
    data = resp.json()
    token = data["tenant_access_token"]
    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expire"] = time.time() + data.get("expire", 7200) - 300
    return token


def classify_category(desc):
    d = desc
    if any(w in d for w in ["测评", "考试", "测试", "题目", "试题", "答案"]):
        return "测评"
    if any(w in d for w in ["内容", "资源", "课件", "视频", "音频", "播放", "课程内容"]):
        return "内容·资源"
    if any(w in d for w in ["账号", "登录", "密码", "注册", "数据", "记录", "进度"]):
        return "账号·数据"
    if any(w in d for w in ["教材", "版本", "课本", "教科书"]):
        return "教材版本"
    if any(w in d for w in ["课程", "体系", "大纲", "课程安排"]):
        return "课程体系"
    if any(w in d for w in ["硬件", "配件", "充电", "屏幕", "电池", "按键", "声音", "耳机", "维修", "保修"]):
        return "硬件·配件"
    if any(w in d for w in ["卡", "死机", "闪退", "崩溃", "bug", "Bug", "报错", "错误", "加载", "卡顿", "慢"]):
        return "性能·Bug"
    if any(w in d for w in ["应用", "App", "APP", "软件", "第三方"]):
        return "第三方应用需求"
    return "其他"


def classify_priority(desc):
    d = desc
    urgent = ["不能用", "打不开", "无法使用", "完全不可用", "坏了", "死机", "崩溃", "闪退", "报错", "充不进"]
    if any(w in d for w in urgent):
        return "高"
    low = ["建议", "希望", "要是", "如果能", "增加", "优化"]
    if any(w in d for w in low) and not any(w in d for w in urgent):
        return "低"
    return "中"


def parse_source(text):
    t = text.strip()
    if t.startswith("伴学师"):
        return "伴学师"
    if t.startswith("用户") or t.startswith("家长") or t.startswith("学生"):
        return "用户"
    if t.startswith("销售"):
        return "销售"
    if t.startswith("客服"):
        return "客服"
    if t.startswith("产品"):
        return "产品"
    return "伴学师"


def extract_items(text):
    lines = text.replace("\r", "\n").split("\n")
    items = []
    current = []

    for line in lines:
        line = line.strip()
        if not line:
            if current:
                items.append(" ".join(current))
                current = []
            continue
        prefixes = ["伴学师", "用户", "销售", "客服", "产品"]
        is_new = any(line.startswith(p) for p in prefixes)
        if is_new and current:
            items.append(" ".join(current))
            current = [line]
        else:
            current.append(line)

    if current:
        items.append(" ".join(current))

    return [i for i in items if len(i) > 1]


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)
            raw_text = data.get("text", "").strip()
            if not raw_text:
                self._json(400, {"ok": False, "error": "请输入反馈内容"})
                return

            items = extract_items(raw_text)
            if not items:
                self._json(200, {"ok": False, "error": "未识别到有效内容"})
                return

            from datetime import date

            today = date.today().isoformat()
            token = get_token()
            written = []
            failed = []

            for item in items:
                rec = {
                    "问题描述": item,
                    "反馈来源": parse_source(item),
                    "问题分类": classify_category(item),
                    "优先级": classify_priority(item),
                    "处理状态": "待确认",
                    "反馈日期": today,
                }
                try:
                    resp = requests.post(
                        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/records",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                        },
                        json={"fields": rec},
                        timeout=10,
                    )
                    rd = resp.json()
                    if rd.get("code") == 0:
                        written.append(item[:30])
                    else:
                        failed.append({"desc": item[:30], "error": rd.get("msg")})
                except Exception as e:
                    failed.append({"desc": item[:30], "error": str(e)})

            self._json(200, {
                "ok": True,
                "parsed": len(items),
                "written": len(written),
                "failed": len(failed),
            })

        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _json(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(body, ensure_ascii=False).encode())
