#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发稿驱动 —— 优先用固定 IP 的远程服务器调微信 API，不可用时自动回退到本机。

为什么需要远程这一层：
    微信 `cgi-bin/token` 要求**调用方出口 IP 在公众号白名单里**，而本机公网 IP
    常变（换网络 / 宽带重拨 / 代理换节点）。固定云服务器的出口 IP 不变，把它加进
    白名单一次，本机换 IP 也不影响发稿。

两种模式（同一套发布逻辑，只是"谁来调 API"不同）：
    remote  —— 本机只做渲染与生图，把素材送到服务器，由服务器调微信 API
    local   —— 全部在本机完成（服务器不可用时的回退路径）

模式选择：
    --mode auto    （默认）配置了远程服务器就先用远程，失败自动回退本机
    --mode remote  强制远程
    --mode local   强制本机

远程服务器通过 config.sh 的 PUBLISH_HOST 配置（或环境变量 WECHAT_PUBLISH_HOST）。
未配置 PUBLISH_HOST 的使用者，直接走本机模式 —— 不需要、也不会看到任何服务器地址。

用法：
    python3 remote_publish.py \
        --title "文章标题" --html article_final.html --cover cover.jpg \
        --image img1.jpg --after-paragraph 3 \
        --image img2.jpg --after-marker "核心观点" \
        --author "申导" --digest "摘要"

    python3 remote_publish.py --setup        # 安装远程发布环境（需先配 PUBLISH_HOST）
    python3 remote_publish.py --probe        # 远程体检：连通性 + IP 白名单，不发稿
    python3 remote_publish.py --dry-run ...  # 只做本机组装与校验
"""

from __future__ import print_function

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import wechat_html  # noqa: E402

REMOTE_PREFIX = "/tmp/wechat-mp-draft."
PERSIST_DIR = "/root/.wechat-mp-draft"
RESULT_MARK = "__RESULT__"
SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "PasswordAuthentication=no",
]
MAX_IMG_BYTES = 1024 * 1024


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def read_local_config():
    path = os.path.join(SKILL_DIR, "scripts", "config.sh")
    values = {}
    if not os.path.isfile(path):
        return values
    with io.open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    for key in ("APPID", "APPSECRET", "AUTHOR", "PUBLISH_HOST"):
        m = re.search(r'^\s*{}\s*=\s*["\']?([^"\'\s]+)'.format(key), text, re.M)
        if m:
            values[key] = m.group(1)
    return values


def resolve_host(args):
    """远程服务器地址来源：命令行 > 环境变量 > config.sh；都没有就没有远程。"""
    if args.host:
        return args.host.strip()
    env = os.environ.get("WECHAT_PUBLISH_HOST", "").strip()
    if env:
        return env
    return read_local_config().get("PUBLISH_HOST", "").strip() or None


# ---------------------------------------------------------------------------
# SSH / 传输
# ---------------------------------------------------------------------------
def ssh(host, remote_cmd, stdin_data=None, check=True):
    cmd = ["ssh"] + SSH_OPTS + [host, remote_cmd]
    proc = subprocess.run(
        cmd, input=stdin_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    if check and proc.returncode != 0:
        raise RuntimeError("ssh 执行失败：{}\n{}".format(remote_cmd, err.strip()))
    return out, err, proc.returncode


def pack_and_send(host, files, remote_dir):
    """用 tar | ssh 一次性把若干文件送过去（少连接，也不踩 shell 转义）。"""
    staging = tempfile.mkdtemp(prefix="wechat-pack-")
    try:
        names = []
        for local_path, remote_name in files:
            shutil.copy(local_path, os.path.join(staging, remote_name))
            names.append(remote_name)
        tar_path = os.path.join(staging, "payload.tar.gz")
        with tarfile.open(tar_path, "w:gz") as tar:
            for name in names:
                tar.add(os.path.join(staging, name), arcname=name)
        with open(tar_path, "rb") as handle:
            ssh(
                host,
                "mkdir -p {d} && tar xzf - -C {d}".format(d=remote_dir),
                stdin_data=handle.read(),
            )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def remote_dir_create(host):
    out, err, _ = ssh(host, "mktemp -d {}.XXXXXX".format(REMOTE_PREFIX))
    path = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if not path.startswith(REMOTE_PREFIX):
        raise RuntimeError("无法在服务器创建临时目录")
    return path


# ---------------------------------------------------------------------------
# 素材准备
# ---------------------------------------------------------------------------
def shrink_if_needed(path):
    """正文图超过 1MB 会被 uploadimg 拒绝（40009）。macOS 上用 sips 转 JPEG。"""
    size = os.path.getsize(path)
    if size <= MAX_IMG_BYTES:
        return path, None
    if not shutil.which("sips"):
        raise RuntimeError(
            "图片 {} 有 {} 字节，超过 1MB 上限，且本机没有 sips 可用于压缩。"
            "请先手动转成 JPEG（例如 sips -s format jpeg -s formatOptions 72 in.png --out out.jpg）".format(
                path, size
            )
        )
    out = os.path.splitext(path)[0] + ".jpg"
    subprocess.run(
        ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "72", path, "--out", out],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    new_size = os.path.getsize(out)
    if new_size > MAX_IMG_BYTES:
        raise RuntimeError("压缩后仍有 {} 字节，超过 1MB".format(new_size))
    return out, "{} -> {} ({} 字节)".format(os.path.basename(path), os.path.basename(out), new_size)


# ---------------------------------------------------------------------------
# 组装（本地做，和发稿方式无关）
# ---------------------------------------------------------------------------
class ImageAction(argparse.Action):
    """--image：追加一张正文图。"""

    def __call__(self, parser, namespace, values, option_string=None):
        items = getattr(namespace, self.dest, None)
        if items is None:
            items = []
        items.append({"file": values})
        setattr(namespace, self.dest, items)


class PositionAction(argparse.Action):
    """--after-paragraph / --after-marker：作用于它前面最近的那个 --image。"""

    def __call__(self, parser, namespace, values, option_string=None):
        images = getattr(namespace, "image", None) or []
        if not images:
            parser.error("{} 必须紧跟在 --image 之后使用".format(option_string))
        key = "after_paragraph" if option_string == "--after-paragraph" else "after_marker"
        images[-1][key] = values
        setattr(namespace, "image", images)


def build_args():
    parser = argparse.ArgumentParser(description="发稿驱动（远程服务器优先，不可用自动回退本机）")
    parser.add_argument("--mode", choices=["auto", "remote", "local"], default="auto")
    parser.add_argument("--host", default="", help="远程服务器 user@host[:port]（默认读 config.sh 的 PUBLISH_HOST）")
    parser.add_argument("--setup", action="store_true", help="安装远程发布环境")
    parser.add_argument("--probe", action="store_true", help="远程体检：连通性 + IP 白名单，不发稿")
    parser.add_argument("--keep", action="store_true", help="保留服务器临时目录（调试用）")
    parser.add_argument("--dry-run", action="store_true", help="只做本机校验与组装，不连接服务器")

    parser.add_argument("--title", help="文章标题")
    parser.add_argument("--html", help="渲染后的正文 HTML 文件")
    parser.add_argument("--cover", help="封面图片（2.35:1）")
    parser.add_argument("--image", action=ImageAction, default=[], help="正文图片，可重复")
    parser.add_argument("--after-paragraph", type=int, action=PositionAction,
                        help="紧跟在某个 --image 之后：在第 N 个 <p> 之后插入")
    parser.add_argument("--after-marker", action=PositionAction,
                        help="紧跟在某个 --image 之后：在指定标题之后插入")
    parser.add_argument("--spec", help="JSON 数组：精确指定每张正文图位置，如 "
                                       '[{"file":"a.jpg","after_paragraph":3}]')
    parser.add_argument("--author", default="")
    parser.add_argument("--digest", default="")
    parser.add_argument("--no-compress", action="store_true", help="不自动压缩正文")
    return parser.parse_args()


def collect_images(args):
    images = []
    if args.spec:
        with io.open(args.spec, "r", encoding="utf-8") as handle:
            for item in json.load(handle):
                if not item.get("file"):
                    raise RuntimeError("spec 条目缺少 file 字段")
                images.append(dict(item))
    for entry in args.image:
        item = dict(entry)
        if not any(k in item for k in ("after_paragraph", "after_marker", "mode")):
            item["mode"] = "append"
        images.append(item)
    return images


def stage(args):
    """校验素材并生成 manifest + 工作目录，返回 (staging_dir, manifest, notes)。"""
    html_path = os.path.abspath(args.html)
    cover_path = os.path.abspath(args.cover)
    if not os.path.isfile(html_path):
        raise RuntimeError("找不到 HTML 文件：{}".format(html_path))
    if not os.path.isfile(cover_path):
        raise RuntimeError("找不到封面图：{}".format(cover_path))

    images = collect_images(args)
    if len(images) < 4:
        print("⚠️  提醒：正文图只有 {} 张（SKILL 约定 ≥ 4 张，不含封面）".format(len(images)))

    with io.open(html_path, "r", encoding="utf-8") as handle:
        html = handle.read()

    external = [s for s in wechat_html.extract_image_srcs(html) if "mmbiz.qpic.cn" not in s]
    if external:
        raise RuntimeError(
            "正文里已有 {} 个非 mmbiz 图片地址，微信会静默过滤掉：\n   {}\n"
            "   请删掉这部分，改用 --image 传入本地图片，由上传后插入。".format(
                len(external), "\n   ".join(external[:5])
            )
        )

    compression = None
    if not args.no_compress and len(html) > wechat_html.CONTENT_CHAR_LIMIT:
        html, compression = wechat_html.compress_html(html)
        print("==> 正文 {} 字符 → 压缩后 {} 字符（省 {}）".format(
            compression["before"], compression["after"], compression["saved"]))
        if len(html) > wechat_html.CONTENT_CHAR_LIMIT:
            raise RuntimeError(
                "压缩后仍有 {} 字符，超过微信 {} 字符硬限制，请精简正文".format(
                    len(html), wechat_html.CONTENT_CHAR_LIMIT)
            )

    check = wechat_html.self_check(html)
    print("==> 本机自检：{} 字符 / 列表 {} 项 / 已有图片 {} 张".format(
        check["chars"], check["li"], check["img_count"]))

    staging = tempfile.mkdtemp(prefix="wechat-publish-")
    notes = []
    staged_images = []
    try:
        staged_html = os.path.join(staging, "article.html")
        with io.open(staged_html, "w", encoding="utf-8") as handle:
            handle.write(html)

        staged_cover = os.path.join(staging, "cover" + os.path.splitext(cover_path)[1].lower())
        shutil.copy(cover_path, staged_cover)

        for index, item in enumerate(images, 1):
            src = os.path.abspath(item["file"])
            if not os.path.isfile(src):
                raise RuntimeError("找不到正文图：{}".format(src))
            shrunk, note = shrink_if_needed(src)
            if note:
                notes.append(note)
            name = "img{}.{}".format(index, os.path.splitext(shrunk)[1].lower().lstrip("."))
            shutil.copy(shrunk, os.path.join(staging, name))
            entry = {"file": name}
            for key in ("after_paragraph", "after_marker", "mode"):
                if key in item:
                    entry[key] = item[key]
            staged_images.append(entry)

        manifest = {
            "publish_mode": "remote" if args.mode in ("auto", "remote") else "local",
            "title": args.title,
            "author": args.author,
            "digest": args.digest,
            "html": "article.html",
            "cover": os.path.basename(staged_cover),
            "images": staged_images,
        }
        with io.open(os.path.join(staging, "manifest.json"), "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
        return staging, manifest, notes, compression
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def worker_files(staging, manifest):
    files = [
        (os.path.join(HERE, "remote_worker.py"), "remote_worker.py"),
        (os.path.join(HERE, "wechat_html.py"), "wechat_html.py"),
        (os.path.join(staging, "manifest.json"), "manifest.json"),
        (os.path.join(staging, "article.html"), "article.html"),
        (os.path.join(staging, manifest["cover"]), manifest["cover"]),
    ]
    for entry in manifest["images"]:
        files.append((os.path.join(staging, entry["file"]), entry["file"]))
    return files


def credential_text():
    config = read_local_config()
    if not config.get("APPID") or not config.get("APPSECRET"):
        raise RuntimeError(
            "本机 scripts/config.sh 未配置 AppID / AppSecret，请先填写。"
        )
    return 'APPID="{a}"\nAPPSECRET="{s}"\nAUTHOR="{u}"\n'.format(
        a=config["APPID"], s=config["APPSECRET"], u=config.get("AUTHOR", "")
    )


def parse_result(*streams):
    """从输出里找结果行。stdout / stderr 都扫一遍 —— 远程环境里输出可能分流。"""
    for out in streams:
        if not out:
            continue
        for line in out.splitlines():
            if line.startswith(RESULT_MARK):
                try:
                    return json.loads(line[len(RESULT_MARK):])
                except ValueError:
                    continue
    return None


# ---------------------------------------------------------------------------
# 本机发布
# ---------------------------------------------------------------------------
def run_local(staging, manifest):
    """在本机执行发布（与远程同一套 worker 逻辑）。"""
    print("==> 本机发布模式（本机出口 IP 需在公众号白名单里）")
    cmd = (
        "cd {d} && PYTHONIOENCODING=utf-8 python3 {w} --dir {d} --manifest manifest.json"
    ).format(d=staging, w=os.path.join(HERE, "remote_worker.py"))
    proc = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    result = parse_result(out, err)
    if result is None:
        print(out)
        print(err)
        raise RuntimeError("本机发布没有拿到结果")
    return result


# ---------------------------------------------------------------------------
# 远程发布
# ---------------------------------------------------------------------------
def run_remote(host, staging, manifest, keep=False):
    """在远程服务器上执行发布。返回 (result, fallback_ok)。"""
    remote_dir = ""
    try:
        print("==> 远程发布模式")
        remote_dir = remote_dir_create(host)
        pack_and_send(host, worker_files(staging, manifest), remote_dir)

        check_out, _, _ = ssh(host, "test -f {}/config.sh && echo yes || echo no".format(PERSIST_DIR))
        if "yes" not in check_out:
            ssh(host, "cat > {d}/config.sh && chmod 600 {d}/config.sh".format(d=remote_dir),
                stdin_data=credential_text().encode("utf-8"))

        cmd = ("cd {d} && PYTHONIOENCODING=utf-8 {env}python3 remote_worker.py "
               "--dir {d} --manifest manifest.json").format(
                   d=remote_dir,
                   env=("WECHAT_API_BASE='{}' ".format(os.environ["WECHAT_API_BASE"]))
                       if os.environ.get("WECHAT_API_BASE") else "")
        out, err, _ = ssh(host, cmd, check=False)
        result = parse_result(out, err)
        if result is None:
            print(err.strip())
            raise RuntimeError("远程服务器没有返回结果")
        return result, True
    finally:
        if remote_dir and not keep:
            try:
                ssh(host, "rm -rf {}".format(remote_dir), check=False)
            except Exception:
                pass


def do_probe(host):
    print("==> 远程体检")
    remote_dir = ""
    try:
        remote_dir = remote_dir_create(host)
        pack_and_send(host, [
            (os.path.join(HERE, "remote_worker.py"), "remote_worker.py"),
            (os.path.join(HERE, "wechat_html.py"), "wechat_html.py"),
        ], remote_dir)
        check_out, _, _ = ssh(host, "test -f {}/config.sh && echo yes || echo no".format(PERSIST_DIR))
        if "yes" not in check_out:
            ssh(host, "cat > {d}/config.sh && chmod 600 {d}/config.sh".format(d=remote_dir),
                stdin_data=credential_text().encode("utf-8"))
        cmd = ("cd {d} && PYTHONIOENCODING=utf-8 python3 remote_worker.py --dir {d} --probe").format(
            d=remote_dir)
        out, err, _ = ssh(host, cmd, check=False)
        result = parse_result(out, err)
        if result is None:
            print("❌ 体检失败，未拿到结果")
            return 1
        if not result.get("ok"):
            print("❌ 体检未通过：{}".format(result.get("error")))
            print("   阶段：{}  错误码：{}".format(result.get("stage"), result.get("errcode")))
            if result.get("errcode") == 40164:
                print("   → 把服务器出口 IP 加入公众号「设置与开发 → 基本配置 → IP白名单」后等 1–8 分钟再试")
            return 1
        print("✅ 体检通过：服务器出口 IP {} 已在公众号白名单内".format(result.get("server_ip")))
        return 0
    finally:
        if remote_dir:
            ssh(host, "rm -rf {}".format(remote_dir), check=False)


def do_setup(host):
    files = [
        (os.path.join(HERE, "remote_worker.py"), "remote_worker.py"),
        (os.path.join(HERE, "wechat_html.py"), "wechat_html.py"),
    ]
    pack_and_send(host, files, PERSIST_DIR)
    ssh(host, "cat > {d}/config.sh && chmod 600 {d}/config.sh && chmod +x {d}/*.py".format(d=PERSIST_DIR),
        stdin_data=credential_text().encode("utf-8"))
    print("✅ 远程发布环境已安装（凭证 600 权限）")
    print("   以后发稿会自动走远程；服务器不可用时自动回退本机。")


# ---------------------------------------------------------------------------
# 结果展示
# ---------------------------------------------------------------------------
def report(result, notes, mode_label):
    if not result.get("ok"):
        print("❌ 发布失败（阶段：{}）".format(result.get("stage")))
        print("   错误码：{}".format(result.get("errcode")))
        print("   信息：{}".format(result.get("error")))
        if result.get("errcode") == 40164 and mode_label.startswith("服务器"):
            print("   → 若本机出口 IP 已在白名单，可用 --mode local 强制在本机发布。")
        if result.get("raw"):
            print("   原始返回：{}".format(result["raw"][:600]))
        return 1

    verify = result.get("verify", {})
    print("")
    print("✅ 草稿已保存到公众号草稿箱（{}）".format(mode_label))
    print("   media_id：{}".format(result.get("media_id")))
    print("   标题：{}".format(result.get("title")))
    print("   封面 media_id：{}".format(result.get("thumb_media_id")))
    print("   正文字符：{}".format(result.get("chars")))
    print("   正文图：{} 张 → 全部换成 mmbiz 永久链接".format(len(result.get("images", []))))
    if verify.get("img_count") is not None:
        print("   回查草稿：图片 {} 张，非微信链接 {} 个".format(
            verify.get("img_count"), verify.get("external_img")))
    if result.get("h1_stripped_from_body"):
        print("   已剥离 body 顶部 H1：{}".format(result["h1_stripped_from_body"]))
    for note in notes:
        print("   {}".format(note))
    print("   JSON: {}".format(json.dumps(result, ensure_ascii=False)))
    return 0


def main():
    args = build_args()

    if args.probe:
        host = resolve_host(args)
        if not host:
            print("❌ 未配置远程服务器（config.sh 的 PUBLISH_HOST 或环境变量 WECHAT_PUBLISH_HOST）")
            return 2
        return do_probe(host)

    if args.setup:
        host = resolve_host(args)
        if not host:
            print("❌ 未配置远程服务器：请在 scripts/config.sh 里设置 PUBLISH_HOST=\"user@host\"")
            return 2
        return do_setup(host)

    missing = [name for name in ("title", "html", "cover") if not getattr(args, name)]
    if missing:
        print("❌ 缺少必填参数：{}".format("、".join(missing)))
        return 2

    staging = None
    try:
        staging, manifest, notes, compression = stage(args)
        if args.dry_run:
            print("✅ --dry-run：本机组装完成，未连接服务器。")
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
            return 0

        host = resolve_host(args)
        mode = args.mode
        if mode == "auto" and not host:
            mode = "local"

        if mode in ("auto", "remote"):
            try:
                result, _ = run_remote(host, staging, manifest, keep=args.keep)
                return report(result, notes, "服务器提交，出口 IP 固定")
            except RuntimeError as exc:
                if mode == "remote":
                    print("❌ 远程发布失败：{}".format(exc))
                    return 1
                # auto：静默回退，不暴露服务器地址与原始报错
                print("⚠️  远程发布不可用（免密登录未成功），已自动回退到本机发布。")
            except Exception as exc:  # noqa: BLE001
                if mode == "remote":
                    print("❌ 远程发布失败：{}".format(exc))
                    return 1
                print("⚠️  远程发布不可用，已自动回退到本机发布。")

        result = run_local(staging, manifest)
        return report(result, notes, "本机提交")
    except RuntimeError as exc:
        print("❌ {}".format(exc))
        return 1
    finally:
        if staging:
            shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
