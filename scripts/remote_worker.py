#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""远程发布 worker —— 在远程服务器上执行「取 token → 传封面 → 传正文图 → 插图 → 存草稿 → 回查」。

必须能在 Python 3.6 上运行（CentOS 7 自带 3.6.8），所以：
  * 只用 stdlib（json / os / re / subprocess / sys / time）
  * 网络请求全部走系统 curl（服务器上没有 jq，也不依赖 requests）
  * 不使用 3.9+ / 3.10+ 语法

调用方式（通常由本机 scripts/remote_publish.py 通过 ssh 触发）：
    python3 remote_worker.py --dir <工作目录> --manifest manifest.json

结果以一行 JSON 输出（stdout 最后一行）：
    {"ok": true, ...}   成功
    {"ok": false, "stage": "...", "error": "..."}   失败
"""

from __future__ import print_function

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time

def _force_utf8(stream):
    """把一个文本流强制成 UTF-8。Python 3.7+ 用 reconfigure，3.6 用 TextIOWrapper。"""
    try:
        stream.reconfigure(encoding="utf-8")
        return stream
    except AttributeError:
        pass
    except Exception:
        return stream
    try:
        return io.TextIOWrapper(
            stream.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
    except Exception:
        return stream


# 服务器 locale 可能是 ASCII/C，中文日志与结果都要显式按 UTF-8 处理。
# 两个流必须各自独立处理 —— 早先版本用 for/else 包裹，结果把 stdout 也换成了
# stderr 的包装器，导致结果行跑到 stderr，调用方读不到。
sys.stdout = _force_utf8(sys.stdout)
sys.stderr = _force_utf8(sys.stderr)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import wechat_html  # noqa: E402  （同目录共享工具库）

# API 基址：默认官方地址；可用 WECHAT_API_BASE 覆盖（本地/服务器端联调用）
API = os.environ.get("WECHAT_API_BASE", "https://api.weixin.qq.com/cgi-bin").rstrip("/")
RESULT_MARK = "__RESULT__"


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def emit(payload):
    """输出最终结果行。"""
    sys.stdout.write(RESULT_MARK + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def log(message):
    sys.stdout.write("[remote] " + str(message) + "\n")
    sys.stdout.flush()


def curl(args, timeout=120, input_bytes=None):
    """调用 curl。JSON 请求体走 stdin（-d @-），避免非 ASCII 正文在 ASCII locale 下
    无法作为命令行参数传给子进程（UnicodeEncodeError）。"""
    proc = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout)] + args,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out = proc.stdout.decode("utf-8", "replace")
    if proc.returncode != 0:
        raise RuntimeError(
            "curl 失败（exit={}）：{}".format(proc.returncode, proc.stderr.decode("utf-8", "replace").strip())
        )
    return out


def load_config(workdir):
    """读取凭证：环境变量 → 工作目录 config.sh → /root/.wechat-mp-draft/config.sh"""
    appid = os.environ.get("APPID", "").strip()
    appsecret = os.environ.get("APPSECRET", "").strip()
    author = os.environ.get("AUTHOR", "").strip()

    candidates = [
        os.path.join(workdir, "config.sh"),
        "/root/.wechat-mp-draft/config.sh",
        os.path.join(HERE, "config.sh"),
    ]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        with io.open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        m = re.search(r'^\s*APPID\s*=\s*["\']?([^"\'\s]+)', text, re.M)
        if m and not appid:
            appid = m.group(1)
        m = re.search(r'^\s*APPSECRET\s*=\s*["\']?([^"\'\s]+)', text, re.M)
        if m and not appsecret:
            appsecret = m.group(1)
        m = re.search(r'^\s*AUTHOR\s*=\s*["\']?([^"\'\n]+)', text, re.M)
        if m and not author:
            author = m.group(1).strip().strip('"').strip("'")
    return appid, appsecret, author


# ---------------------------------------------------------------------------
# 微信 API 封装
# ---------------------------------------------------------------------------
class WeChatError(Exception):
    def __init__(self, stage, errcode, errmsg, raw=""):
        Exception.__init__(self, "{} {} {}".format(stage, errcode, errmsg))
        self.stage = stage
        self.errcode = errcode
        self.errmsg = errmsg
        self.raw = raw


def get_access_token(appid, appsecret, attempts=3, who="服务器"):
    """取 token。40164（IP 不在白名单）立刻失败并把 IP 报出来，别浪费重试。"""
    url = (
        API
        + "/token?grant_type=client_credential&appid={}&secret={}".format(appid, appsecret)
    )
    last = None
    for attempt in range(1, attempts + 1):
        raw = curl([url])
        try:
            data = json.loads(raw)
        except ValueError:
            raise WeChatError("get_access_token", "PARSE", "token 接口返回非 JSON", raw)
        if "access_token" in data:
            log("token 获取成功（第 {} 次尝试）".format(attempt))
            return data["access_token"], data.get("expires_in", 7200)
        last = data
        errcode = data.get("errcode")
        if errcode == 40164:
            errmsg = data.get("errmsg", "")
            m = re.search(r"invalid ip ([0-9.]+)", errmsg)
            ip = m.group(1) if m else _server_ip()
            raise WeChatError(
                "get_access_token",
                40164,
                "{}出口 IP {} 不在公众号白名单（{}）。"
                "请把该 IP 加入「设置与开发 → 基本配置 → IP白名单」，加完通常 1–8 分钟生效。".format(
                    who, ip, errmsg
                ),
                raw,
            )
        if errcode in (40001, 42001):
            time.sleep(2 * attempt)
            continue
        time.sleep(2 * attempt)
    raise WeChatError(
        "get_access_token",
        last.get("errcode") if last else "UNKNOWN",
        last.get("errmsg", "未知错误") if last else "未知错误",
        json.dumps(last, ensure_ascii=False) if last else "",
    )


def upload_material(token, path):
    """上传永久素材（封面图），返回 media_id。"""
    raw = curl(
        [
            "-X", "POST",
            "-F", "media=@{};type={}".format(path, _mime(path)),
            "{}?access_token={}&type=image".format(API + "/material/add_material", token),
        ]
    )
    data = _parse(raw, "upload_image")
    if "media_id" not in data:
        raise WeChatError("upload_image", data.get("errcode", "UNKNOWN"), data.get("errmsg", raw), raw)
    return data["media_id"]


def upload_content_image(token, path):
    """上传正文图片，返回 mmbiz 永久 URL。"""
    raw = curl(
        [
            "-X", "POST",
            "-F", "media=@{};type={}".format(path, _mime(path)),
            "{}?access_token={}".format(API + "/media/uploadimg", token),
        ]
    )
    data = _parse(raw, "upload_content_image")
    url = data.get("url")
    if not url:
        raise WeChatError("upload_content_image", data.get("errcode", "UNKNOWN"), data.get("errmsg", raw), raw)
    return url.replace("\\/", "/")  # 还原 JSON 转义，否则微信取不到图


def add_draft(token, payload):
    raw = curl(
        [
            "-X", "POST",
            "-H", "Content-Type: application/json; charset=utf-8",
            "-d", "@-",
            "{}?access_token={}".format(API + "/draft/add", token),
        ],
        input_bytes=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )
    data = _parse(raw, "draft_add")
    if "media_id" not in data:
        raise WeChatError("draft_add", data.get("errcode", "UNKNOWN"), data.get("errmsg", raw), raw)
    return data["media_id"]


def get_draft(token, media_id):
    raw = curl(
        [
            "-X", "POST",
            "-H", "Content-Type: application/json; charset=utf-8",
            "-d", "@-",
            "{}?access_token={}".format(API + "/draft/get", token),
        ],
        input_bytes=json.dumps({"media_id": media_id}, ensure_ascii=False).encode("utf-8"),
    )
    return _parse(raw, "draft_get")


def _parse(raw, stage):
    try:
        return json.loads(raw)
    except ValueError:
        raise WeChatError(stage, "PARSE", "接口返回非 JSON", raw)


def _mime(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".gif":
        return "image/gif"
    return "application/octet-stream"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run(workdir, manifest):
    with io.open(os.path.join(workdir, manifest["manifest"]), "r", encoding="utf-8") as handle:
        spec = json.load(handle)

    appid, appsecret, config_author = load_config(workdir)
    if not appid or not appsecret:
        raise WeChatError("config", "NO_CONFIG", "未找到 AppID / AppSecret")

    title = spec["title"]
    author = spec.get("author") or config_author
    digest = spec.get("digest") or ""

    with io.open(os.path.join(workdir, spec["html"]), "r", encoding="utf-8") as handle:
        html = handle.read()

    # 标题只走 title 字段
    html, stripped_h1 = wechat_html.strip_leading_h1(html)
    html = wechat_html.sanitize_lists(html)

    who = "服务器" if spec.get("publish_mode") == "remote" else "本机"
    token, expires = get_access_token(appid, appsecret, who=who)

    # 封面（永久素材）
    cover = spec.get("cover")
    thumb_media_id = ""
    if cover:
        thumb_media_id = upload_material(token, os.path.join(workdir, cover))
        log("封面上传成功 media_id={}".format(thumb_media_id))

    # 正文图片：先全部上传换成 mmbiz 永久链接，再插入
    image_specs = []
    uploaded = []
    for item in spec.get("images", []):
        url = upload_content_image(token, os.path.join(workdir, item["file"]))
        entry = {"file": item["file"], "url": url}
        for key in ("after_paragraph", "after_marker", "mode"):
            if key in item:
                entry[key] = item[key]
        image_specs.append(entry)
        uploaded.append(entry)
        log("正文图上传成功 {} -> {}".format(item["file"], url))

    if image_specs:
        html = wechat_html.insert_images(html, image_specs)

    # 字符数保护：超限时先做等价压缩
    compression = None
    if len(html) > wechat_html.CONTENT_CHAR_LIMIT:
        html, compression = wechat_html.compress_html(html)
        log("正文 {} 字符 → 压缩后 {} 字符".format(compression["before"], compression["after"]))
        if len(html) > wechat_html.CONTENT_CHAR_LIMIT:
            raise WeChatError(
                "compress",
                "TOO_LONG",
                "压缩后仍有 {} 字符，超过微信 {} 字符硬限制，请精简正文".format(
                    len(html), wechat_html.CONTENT_CHAR_LIMIT
                ),
            )

    check = wechat_html.self_check(html)
    if check["external_img"] > 0:
        raise WeChatError(
            "precheck",
            "EXTERNAL_IMG",
            "正文仍有 {} 个非 mmbiz.qpic.cn 的图片地址，微信会静默过滤掉".format(
                check["external_img"]
            ),
        )

    payload = {
        "articles": [
            {
                "title": title,
                "content": html,
                "author": author,
                "digest": digest,
                "content_source_url": "",
                "thumb_media_id": thumb_media_id,
                "need_open_comment": 1,
                "only_fans_can_comment": 0,
                "show_cover_pic": 1,
            }
        ]
    }
    media_id = add_draft(token, payload)
    log("草稿保存成功 media_id={}".format(media_id))

    # 回查：确认图片真的进去了
    verify = {}
    try:
        draft = get_draft(token, media_id)
        items = draft.get("news_item") or []
        if items:
            content = (items[0].get("content") or "")
            srcs = wechat_html.extract_image_srcs(content)
            verify = {
                "img_count": len(srcs),
                "img_srcs": srcs,
                "external_img": len([s for s in srcs if "mmbiz.qpic.cn" not in s]),
                "title": items[0].get("title"),
            }
            log("回查：草稿内图片 {} 张，全部为 mmbiz 链接：{}".format(
                verify["img_count"], verify["external_img"] == 0))
    except WeChatError as exc:
        verify = {"error": exc.errmsg}

    return {
        "ok": True,
        "media_id": media_id,
        "title": title,
        "author": author,
        "digest": digest,
        "thumb_media_id": thumb_media_id,
        "chars": len(html),
        "compression": compression,
        "h1_stripped_from_body": stripped_h1,
        "matches_title_param": bool(stripped_h1) and stripped_h1 == title,
        "self_check": check,
        "images": uploaded,
        "verify": verify,
        "server_ip": _server_ip(),
        "workdir": workdir,
    }


def _server_ip():
    """取服务器出口 IP（ipify 在部分云服务器上被拦，依次兜底）。"""
    for url in ("https://ifconfig.me/ip", "http://checkip.amazonaws.com"):
        try:
            ip = curl([url], timeout=10).strip()
            if re.match(r"^[0-9.]+$", ip):
                return ip
        except Exception:
            continue
    try:
        out = subprocess.run(
            ["hostname", "-I"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        ).stdout.decode("utf-8", "replace").strip()
        if out:
            return out.split()[0]
    except Exception:
        pass
    return ""


def run_probe(workdir):
    """只做连通性与白名单体检，不上传、不发稿。"""
    appid, appsecret, _ = load_config(workdir)
    if not appid or not appsecret:
        raise WeChatError("config", "NO_CONFIG", "未找到 AppID / AppSecret")
    token, expires = get_access_token(appid, appsecret)
    return {
        "ok": True,
        "probe": True,
        "token_ok": True,
        "expires_in": expires,
        "server_ip": _server_ip(),
        "workdir": workdir,
    }


def main():
    parser = argparse.ArgumentParser(description="远程发布 worker")
    parser.add_argument("--dir", required=True, help="工作目录")
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--probe", action="store_true", help="只体检（连通性 + IP 白名单），不发稿")
    args = parser.parse_args()

    workdir = os.path.abspath(args.dir)
    try:
        if args.probe:
            payload = run_probe(workdir)
        else:
            payload = run(workdir, {"manifest": args.manifest})
    except WeChatError as exc:
        payload = {
            "ok": False,
            "stage": exc.stage,
            "errcode": exc.errcode,
            "error": exc.errmsg,
            "raw": exc.raw[:2000],
            "server_ip": _server_ip(),
            "workdir": workdir,
        }
    except Exception as exc:  # noqa: BLE001
        payload = {
            "ok": False,
            "stage": "unexpected",
            "error": "{}: {}".format(type(exc).__name__, exc),
            "server_ip": _server_ip(),
            "workdir": workdir,
        }
    emit(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
