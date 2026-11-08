# 远程发稿详解

本机公网 IP 会变（换网络、宽带重拨、代理换节点，一小时内能换好几次），而微信
`cgi-bin/token` 要求**调用方出口 IP 在公众号白名单里**。把一台**出口 IP 固定**的云服务器
加进白名单，本机换 IP 就不影响发稿。

本 Skill 默认 `--mode auto`：能连上远程就走远程，连不上就**静默回退本机**，
直到报错为止 —— 使用者不需要知道远程服务器的存在。

---

## 一、服务器需要什么

| 项目 | 要求 | 说明 |
|---|---|---|
| 系统 | 任意 Linux | CentOS 7 / Ubuntu / Debian 都行 |
| Python | ≥ 3.6 | `python3 -V` 能输出版本即可（脚本兼容 3.6） |
| curl | 任意版本 | 上传图片、调用 API 都用它 |
| jq | **不需要** | 服务器通常没有，脚本用 Python 解析 JSON |
| 出网 | 能访问 `api.weixin.qq.com` | 普通云服务器直连即可 |

一条命令自查：

```bash
python3 -V && curl -sS -o /dev/null -w '%{http_code}\n' https://api.weixin.qq.com
```

---

## 二、本机准备（一次性）

### 1. 免密登录

脚本用 `BatchMode=yes` + `PasswordAuthentication=no`，**不会提示输密码** —— 没配免密就是直接失败。

```bash
ssh-keygen -t ed25519 -C "wechat-mp-draft"   # 若还没有公钥
ssh-copy-id user@host                       # 装公钥
ssh user@host 'echo ok'                    # 验证免密
```

### 2. 配置服务器地址与凭证

编辑 `scripts/config.sh`（该文件已 gitignore）：

```bash
APPID="wx..."
APPSECRET="..."
AUTHOR="申导"

# 远程发稿服务器（可选）。未配置则全程本机发布。
PUBLISH_HOST="user@host"
```

> `PUBLISH_HOST` 写在配置里而不是代码里，所以**仓库里不会出现任何服务器地址**。
> 其他使用者拿到这个 Skill 时没有这一行，直接走本机模式，完全不受影响。

### 3. 公众号白名单

登录微信公众平台 → 设置与开发 → **基本配置** → 公众号开发信息 → **IP白名单** → 添加服务器出口 IP。

- 别加错到「安全中心 → 登录IP白名单」，那个只管后台登录，对 API 无效。
- 以微信报错里的 IP 为准（代理规则可能让不同域名走不同出口）。
- 加完通常 1–2 分钟生效，最长实测约 8 分钟。

### 4. 安装远程发布环境

```bash
python3 scripts/remote_publish.py --setup
```

会把 `remote_worker.py` + `wechat_html.py` + `config.sh`（权限 600）装到服务器的
`/root/.wechat-mp-draft/`，以后每次发稿不用再传凭证。

### 5. 体检

```bash
python3 scripts/remote_publish.py --probe
```

输出示例：

```
✅ 体检通过：服务器出口 IP 1.2.3.4 已在公众号白名单内
```

失败时会明确告诉你是「白名单没加」还是「服务器连不上」。

---

## 三、日常发稿

```bash
python3 scripts/remote_publish.py \
  --title "文章标题" \
  --html  article.html \
  --cover cover.jpg \
  --image img1.jpg --after-paragraph 3 \
  --image img2.jpg --after-marker "核心观点" \
  --image img3.jpg --after-paragraph 12 \
  --image img4.jpg --after-marker "写在最后" \
  --author "申导" --digest "摘要"
```

成功输出：

```
✅ 草稿已保存到公众号草稿箱（服务器提交，出口 IP 固定）
   media_id：xxxxx
   标题：...
   封面 media_id：...
   正文字符：18342
   正文图：4 张 → 全部换成 mmbiz 永久链接
   回查草稿：图片 4 张，非微信链接 0 个
   已剥离 body 顶部 H1：...
```

### 位置锚点

位置参数**紧跟在它前面那一个 `--image`** 之后，只作用于这一张图：

| 写法 | 含义 |
|---|---|
| `--image a.jpg --after-paragraph N` | a 插到第 N 个 `<p>` 之后（1 起算） |
| `--image b.jpg --after-marker "核心观点"` | b 插到标题文字之后（**写标题文字即可**，脚本自动定位到那个元素的标签后） |
| `--image c.jpg`（不写位置） | c 追加到文末 |

要逐张指定，就按「图 → 位置 → 图 → 位置」的顺序写；位置一旦不能省略。

批量精确控制用 `--spec`：

```json
[
  {"file": "/path/img1.jpg", "after_paragraph": 3},
  {"file": "/path/img2.jpg", "after_marker": "核心观点"},
  {"file": "/path/img3.jpg", "after_paragraph": 12},
  {"file": "/path/img4.jpg", "after_marker": "写在最后"}
]
```

---

## 四、模式与回退

| 情况 | 行为 |
|---|---|
| 没配 `PUBLISH_HOST` | 直接走本机 |
| 远程 ssh 连不上 / 免密登录失败 | 打印一行提示，**自动回退本机**，直到报错（不暴露服务器地址） |
| 远程连上了，但微信 API 报错（如 40164） | 不回退，直接报真实原因（回退也解决不了白名单问题） |
| `--mode remote` | 任何失败都不回退，直接报错 |
| `--mode local` | 强制本机发 |

> 回退发生在**发稿流程层面**：本机渲染 HTML、生图、转 JPEG、校验字符数，这些都不变，
> 变的只是"最后调微信 API 的那台机器"。所以回退后内容与远程完全一致。

---

## 五、链路细节

```
本机                                        服务器（/tmp/wechat-mp-draft.XXXXXX）
─────────────────────────────────          ───────────────────────────────────────────
渲染 HTML / 生图 / 转 JPEG
本机自检（字符数、列表、外链图）
── tar | ssh 传输 ────────────────────→   取 access_token（带重试，40164 立刻报错）
                                            上传封面 → thumb_media_id
                                            逐张上传正文图 → mmbiz.qpic.cn 永久 URL
                                            插入 <img>（按指定位置）
                                            标题去重 + 列表清洗 + 超限压缩
                                            draft/add 存草稿
                                            draft/get 回查图片数量
<── media_id 与回查结果 ─────────────      删除临时目录
```

### worker 是「用时才启动」，不是常驻

服务器上**没有任何常驻进程** —— 没有 systemd 服务、没有 cron、没有守护进程。

- 每次发稿，本机通过 `ssh` 拉起一次 `python3 remote_worker.py`，跑完（取 token → 传图 → 插图 → 存草稿 → 回查）立刻退出
- 临时目录 `/tmp/wechat-mp-draft.XXXXXX` 用完即删（`--keep` 可保留调试）
- 服务器磁盘上常驻的只是三个**文件**（不是进程）：`remote_worker.py`、`wechat_html.py`、`config.sh`（凭证，600 权限）
  - 这几个文件其实是每次发稿都会重新传一遍的，磁盘上这份只是备份，方便手工排查
- 空闲时服务器不占任何资源

### 设计要点

- **临时目录用完即删**（`--keep` 可保留调试）。
- 服务器上的凭证常驻 `/root/.wechat-mp-draft/config.sh`（600 权限）；没常驻时临时送一份，
  随临时目录一起删除。
- 正文图**不在本机上传**：图片先传到服务器再上传换永久链接，天然规避「混元临时外链被微信静默过滤」的坑。
- token 与发稿在同一台机器上，天然规避「token 与 IP 绑定」的限制。
- 服务器没有 jq 也没关系，worker 用 Python 的 `json` 解析。

---

## 六、排障

| 现象 | 原因 | 处理 |
|---|---|---|
| 提示「远程发布不可用（免密登录未成功）」 | 没配免密 / 主机不通 | `ssh user@host 'echo ok'` 验证；见下方清单 |
| 阶段停在 `get_access_token`，错误码 40164 | 服务器 IP 没加公众号白名单 | 加白名单，等 1–8 分钟 |
| 阶段停在 `get_access_token`，错误码 40001/42001 | token 过期/被顶替 | worker 已内置重试；连续失败再手动重跑 |
| 阶段停在 `upload_content_image`，40005 | 不是 JPG/PNG | `sips -s format jpeg` 转一下 |
| 阶段停在 `upload_content_image`，40009 | 图片超 1MB | 脚本会自动用 sips 压缩；无 sips 时手动转 |
| 阶段停在 `draft_add`，40007 | 封面 media_id 无效 | 重新发稿（脚本每次都会新传封面） |
| 正文里出现外链图 | HTML 里混进了非 mmbiz 的 `<img>` | 删掉那些标签，改用 `--image` 传本地图片 |
| 正文超过 2 万字符 | 渲染后样式占体积 | 脚本自动压缩；仍超限就精简正文 |

### 免密登录自查清单

```bash
ssh -o BatchMode=yes user@host 'echo 免密OK'    # 必须免密成功
ssh user@host 'python3 -V; curl -V | head -1'    # 依赖齐全
ssh user@host 'curl -s --max-time 10 https://ifconfig.me/ip'   # 能出网
```

### 首次连接的主机指纹

脚本用 `StrictHostKeyChecking=accept-new`，第一次连接自动记录指纹，无需交互。
若你的环境禁止自动写入 known_hosts，先手动执行一次 `ssh user@host` 接受指纹。

### 清理服务器残留

```bash
ssh user@host 'ls -d /tmp/wechat-mp-draft.* 2>/dev/null'   # 查看残留
ssh user@host 'rm -rf /tmp/wechat-mp-draft.*'              # 清理
```
