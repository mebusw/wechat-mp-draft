---
name: wechat-mp-draft
description: 代写微信公众号文章并保存到公众号草稿箱（每篇正文配图不少于 4 张）。支持「远程服务器发稿」模式——本机公网 IP 常变时，由固定 IP 的服务器调用微信 API，免疫 IP 白名单变动；服务器不可用时自动回退本机直发。使用场景：用户需要撰写公众号文章并直接发布到微信公众号后台草稿箱；或排查公众号草稿箱里的「标题在文章顶部重复显示两次」「标题行既出现在 title 字段又出现在 body」「正文图只剩 2 张」「40164 IP 不在白名单」等 markdown-renderer 与微信 API 问题。触发词："写公众号文章"、"保存到公众号草稿"、"微信文章"、"公众号发文"、"标题重复"、"草稿标题显示两次"、"IP 不在白名单"、"40164"、"远程发稿"。
---

# 微信公众号草稿 Skill

编写微信公众号文章并保存到草稿箱。

**核心机制：谁去调微信 API。** 微信 `cgi-bin/token` 只认**调用方出口 IP 是否在公众号白名单里**，而本机公网 IP 经常变（换网络、宽带重拨、代理换节点，一小时内能换好几次）。所以本 Skill 有两条发稿通道：

| 模式 | 谁调 API | 何时用 | 前置条件 |
|---|---|---|---|
| **远程**（推荐） | 固定 IP 的服务器 | 本机 IP 经常变 | 配好 `PUBLISH_HOST` + 服务器 IP 已加白名单 |
| **本机** | 本机 | 没配远程，或远程连不上 | 本机出口 IP 已加白名单 |

默认 `--mode auto`：**能连上远程就走远程，连不上就静默回退到本机**，直到报错为止 —— 使用者不需要知道远程服务器的存在。

---

## 前置条件

### 必备：公众号凭证

在 `scripts/config.sh` 里配置（该文件已被 `.gitignore` 忽略，不会进仓库）：

```bash
APPID="wx..."          # 微信公众平台 → 设置与开发 → 基本配置
APPSECRET="..."        # 同上
AUTHOR="申导"          # 可选，默认作者
```

### 可选：远程发稿服务器

在一台**出口 IP 固定**的云服务器上跑（普通 Linux 即可，只要能 ssh 免密登录、有 `python3` 和 `curl`）。
把服务器 IP 加进公众号「设置与开发 → 基本配置 → **IP 白名单**」——加一次，本机换 IP 也不影响发稿。

然后在 `scripts/config.sh` 里加一行：

```bash
PUBLISH_HOST="user@host"      # 例：root@1.2.3.4，可带 :端口
```

**没配这一行的使用者完全不受影响**：脚本直接走本机模式，不会尝试连接任何服务器。
（`PUBLISH_HOST` 写在 `config.sh` 里而不是代码里，所以仓库里不会出现任何服务器地址。）

---

## 标准工作流

1. 基于用户输入，调用 SKILL `/jackyshen-write-wechat-article` 或其他类似的技能，改写为微信公众号风格的文章，md 格式（首行为 `# 标题`）
2. **从 markdown 中删除首行 `# 标题`**，避免标题在 body 里重复显示（详见「问题 5」）
3. 调用 SKILL `/wechat-markdown-html-render` 渲染正文，得到 HTML（样式全部内联）
4. 调用 SKILL `/huny-img` 或其他可用的文生图 SKILL 配 **1 张封面图**（2.35:1 → 1203×512 像素）
5. 调用 SKILL `/huny-img` 或其他可用的文生图 SKILL 生成 **至少 4 张内容图片**（1280×720，转 JPEG 后约 100KB）
6. **发稿**（一条命令，远程/本机自动选择）：

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

> **位置参数的写法**：`--after-paragraph` / `--after-marker` 只作用于**紧跟在它前面那一个 `--image`**，
> 所以要逐张指定位置，就得把位置参数写在每张图后面。不写位置则默认追加到文末。

> **和旧流程最大的区别**：正文图**不再需要在本机先上传再插入**。图片以本地文件形式传给脚本，
> 由**服务器上传换永久链接后插入正文** —— 这样整条链路只有一台机器在调微信 API，
> 既避开了混元临时链接被静默过滤的老坑，也避开了本机 IP 变动。
>
> 如果正文里已经有 `<img>`（比如上一轮发布留下的 mmbiz 链接），脚本会接受它们；
> 但如果出现**非 mmbiz 的外链**，会直接报错并让你改用 `--image` 传入本地图片。

---

## 发稿命令详解

### `scripts/remote_publish.py`

```bash
# 默认：能连远程就走远程，连不上自动回退本机
python3 scripts/remote_publish.py --title T --html a.html --cover c.jpg --image i.jpg ...

# 强制本机（远程配了但你想直发）
python3 scripts/remote_publish.py --mode local ...

# 强制远程（远程不可用时直接报错，不回退）
python3 scripts/remote_publish.py --mode remote ...

# 体检：检查 ssh 通不通、服务器 IP 在不在白名单（不发稿）
python3 scripts/remote_publish.py --probe

# 一次性安装远程发布环境（把 worker + 凭证装到服务器常驻目录）
python3 scripts/remote_publish.py --setup

# 只做本机组装与校验，不连服务器
python3 scripts/remote_publish.py --dry-run ...
```

| 参数 | 说明 |
|---|---|
| `--title` | 文章标题（≤32 字） |
| `--html` | 渲染后的正文 HTML 文件 |
| `--cover` | 封面图片 |
| `--image` | 正文图片，可重复。位置参数**紧跟在它后面**作用于这一张：`--after-paragraph N` 或 `--after-marker "标题文字"` |
| `--spec` | JSON 数组，精确控制每张图位置：`[{"file":"a.jpg","after_paragraph":3},{"file":"b.jpg","after_marker":"核心"}]` |
| `--author` / `--digest` | 作者 / 摘要 |
| `--mode` | `auto`（默认）/ `remote` / `local` |
| `--host` | 临时指定远程服务器；默认读 `config.sh` 的 `PUBLISH_HOST` 或环境变量 `WECHAT_PUBLISH_HOST` |
| `--no-compress` | 不自动压缩正文（默认超 2 万字符时自动压缩） |

### 远程发布做了什么

```
本机                                   服务器（/tmp/wechat-mp-draft.XXXXXX）
────────────────────────────────        ─────────────────────────────────────────
渲染 HTML / 生图 / 转 JPEG
本机自检（字符数、列表、外链图）
── tar | ssh 传输 ─────────────────→  取 access_token（带重试）
                                       上传封面 → thumb_media_id
                                       逐张上传正文图 → mmbiz 永久 URL
                                       插入 <img>（按指定位置）
                                       标题去重 + 列表清洗 + 超限压缩
                                       draft/add 存草稿
                                       draft/get 回查图片数量
<── 返回 media_id 与回查结果 ──────    （随后删除临时目录）
```

- **worker 不是常驻进程**：每次发稿才通过 ssh 拉起一次，跑完立刻退出；服务器上没有 systemd 服务、没有 cron、没有守护进程，空闲时零占用。
- 服务器上的凭证常驻在 `/root/.wechat-mp-draft/config.sh`（权限 600），由 `--setup` 安装。
  没常驻配置时，脚本会临时送一份过去，**随临时目录一起删除**。
- 服务器只需要 `python3`（≥3.6）和 `curl`，**不需要 jq**（服务器通常没有）。
- 临时目录用完即删；调试加 `--keep` 保留。

### 回退规则

| 情况 | 行为 |
|---|---|
| 没配 `PUBLISH_HOST` | 直接走本机 |
| 远程 ssh 连不上 / 免密登录失败 | 打印一行提示，**自动回退本机**，直到报错（不暴露服务器地址） |
| 远程连上了，但微信 API 报错（如 40164） | 不回退，直接报出真实原因（回退也解决不了白名单问题） |
| `--mode remote` | 任何失败都不回退，直接报错 |

---

## 关键问题与解决方案

### ❌ 问题 1：40007 invalid media_id

**两种触发场景：**
- **封面 `thumb_media_id`：** 是必填字段；封面图片必须是**永久素材**（通过 `add_material` 接口上传），不能直接用外部 URL。
- **草稿 `media_id` 已失效：** 草稿在公众号后台被打开 / 编辑 / 删除后，`media_id` 立即失效（即使是几分钟前刚返回的）。`draft/update` 和 `draft/get` 都会返回 40007。

**解决：**
- 封面图：先上传永久素材：
  ```bash
  curl -F "media=@cover.jpg" "https://api.weixin.qq.com/cgi-bin/material/add_material?access_token=TOKEN&type=image"
  ```
- 草稿失效：调 `cgi-bin/draft/batchget` 拿当前 id 列表（可能为空，说明被删了），再决定是 `update` 已有还是重新 `add`：
  ```bash
  curl -s -X POST "https://api.weixin.qq.com/cgi-bin/draft/batchget?access_token=$TOKEN" \
    -H "Content-Type: application/json" -d '{"offset":0,"count":10,"no_content":1}'
  ```

### ❌ 问题 2：IP 不在白名单（40164）

**症状：** `cgi-bin/token` 返回 `{"errcode":40164,"errmsg":"invalid ip 111.x.x.x ipv6 ::ffff:111.x.x.x, not in whitelist"}`。

**这就是本机 IP 常变带来的主要麻烦。** 三种解法：

1. **（推荐）走远程发稿**：把固定服务器的 IP 加进白名单一次，本机换 IP 不再受影响。`remote_publish.py` 默认会自动这么做。
2. **手动加白名单**：登录微信公众平台 → 设置与开发 → **基本配置** → 公众号开发信息 → IP白名单 → 添加服务器 IP
3. **临时直发**：本机 IP 恰好在白名单里时，用 `--mode local`。

**注意：**
- 不要错加到「**安全中心** → 登录IP白名单」—— 那个只控制后台登录，对 `cgi-bin/token` API 无效。
- **以报错里那个 IP 为准**，不是 `ipify` 查出来的（两者可能不同：代理规则只对部分域名生效，例如 ClashX 里 `qq.com` 走 DIRECT，微信看到的 IP 就是本机当前公网 IP）。
- 出口 IP 会变（换网络、宽带重拨、代理换节点），**一次把新旧两个都加进白名单**。
- 新加的 IP 通常立即生效，但偶尔有 1–2 分钟传播延迟（实测最长约 8 分钟），失败时先等再重试，别误判成加错了地方。
- 多账号情况：IP、AppID、白名单位置都核对过仍然失败，多半是加到了错误的公众号账号，对照 `config.sh` 里的 AppID 前缀检查。

**连带坑（一起踩过）：**
- ⚠️ **access_token 与申请它的 IP 绑定**：IP 一变，立刻报 42001 / 40001。取 token 要用**带重试的循环**，拿到就马上用。远程模式天然规避（token 与发稿都在同一台机器上）。
- ⚠️ **别反复刷新 token**：每次调 `cgi-bin/token` 都会让上一个 token 失效。取一次、用到底；过期了再取。
- ⚠️ **token 有效期 2 小时**，发多篇稿时复用同一个 token，别每篇取一次。

### ❌ 问题 3：HTML 内容格式错误

**解决：**
- HTML 中的换行符会导致 JSON 解析失败
- 必须将换行符替换为空格
- 使用 `tr '\n' ' '` 处理

### ❌ 问题 4：有序/无序列表在公众号编辑器里渲染异常

公众号编辑器的 HTML ingester 有两个非标准行为，会让 `<ol>` / `<ul>` 列表静默崩坏。

| 症状 | 根因 | 修复 |
|---|---|---|
| 项目左侧没有 `1. 2. 3.` 编号或 `•` 符号，行间还多出空行 | `<li>` 内含块级子元素 `<section>` —— marker 被嵌套块"抢走"，section 的 `margin-top/bottom` 还顶出空行 | `<li>` 直接放文字，section 样式合并到 `<li>` 上 |
| 编号有了但变成 `1. (空) / 2. real / 3. (空) / 4. real / …`，N 个真实项目渲染成 ~2N+1 行 | `<ol>` / `<ul>` 内 `<li>` 兄弟之间的空白文本节点（空格、换行、Tab）被当成额外空 `<li>` | `<ol>↔<li>`、`</li>↔<li>`、`</li>↔</ol>` 三处空白全部 strip，**只 strip `\n` 不够**，普通空格也会触发 bug |

正确做法是用 [wechat-markdown-html-render](../wechat-markdown-html-render/) 渲染（该 skill 已内置这两条规则）。
`wechat_html.py`（本 skill 共享库）也内置了同样的清洗，`remote_publish.py` 与 `add_draft.sh` 发稿前都会自动执行。

**辨别 marker 是否真的缺失：** 从公众号编辑器复制出来的纯文本本来就**不带** `1. 2. 3.` —— 那些是 CSS `::marker` 伪元素生成的、不参与剪贴板。一定要看编辑器**视觉预览区**，不要拿复制出的文本下结论。

### ❌ 问题 5：标题在 body 里重复显示（顶部出现两次）

**症状：** 公众号草稿里，文章顶部标题下面，紧接着又出现一行大号彩色「标题」字样；视觉上同一行字渲染了两次。

**根因：** markdown 源文件以 `# 标题` 开头（来自 `/jackyshen-write-wechat-article` 的标准输出），`/wechat-markdown-html-render` 把这个 H1 渲染进 body 里的 `<h1>` 节点；但发稿时又把同一字符串作为 `title` 字段传给 API。公众号后台把 `title` 字段渲染为文章标题、把 `content` 字段直接显示在标题下面 —— 所以同一标题出现两次。

**解决：** 永远只把标题作为 `title` 参数传入，body 必须从非 H1 内容开始。
`wechat_html.py` 的 `strip_leading_h1()` 已内置自动剥离（检测到 body 开头是 `<h1>`，可能嵌在 `<section id="nice">` 包装里，就把它移除），远程 worker 与 `add_draft.sh` 都会执行，并回显：

```
✅ 标题去重: body 顶部的 <h1> 已自动剥离（与 title 参数一致），避免重复显示
```

**不要绕过这个剥离。** 如果你想在 body 顶部显示一个不同的大标题（例如「卷首语」或栏目名），用 `## 二级标题` 写 —— 它不会被剥离，也不会和 `title` 冲突。

**辨别是否真的没剥离：** 发稿后检查 payload，正文里 `<h1` 应该是 0 次。

### ❌ 问题 6：正文看起来「没插图」，但 `<img>` 标签其实在

**症状：** 草稿保存成功、`draft/get` 也能查到 `<img>`，但公众号后台/预览里图片不显示，正文像纯文字。

**根因：** 图片 URL 里残留了 JSON 转义的反斜杠 —— `http:\/\/mmbiz.qpic.cn\/...`。
`upload_content_image.sh` 的原始输出是 JSON，URL 中的 `/` 被写成 `\/`。用 `jq -r` 取会自动还原，
但用 `grep -o '"url":"[^"]*"' | sed` 手工抠出来的是**未还原**的字符串，直接拼进 `<img src="...">`
后浏览器/微信无法解析，静默失败（不报错、不占位）。

**排查：** `grep -o '<img src="[^"]*"' article_final.html` —— URL 里只要出现 `\` 就是这个问题。

**修复：**
```python
html = html.replace('\\/','/')   # 或一开始就用 jq -r '.url' 取值
```
> 远程发布已自动还原（worker 内部做 `.replace("\\/", "/")`），本机直发时请照上面处理。

### ❌ 问题 7：配图数量不足 4 张 / 改稿时草稿更新失败

**症状 A：草稿里的图比本地少。** 本地 HTML 4 张图，`draft/get` 只剩 2 张（甚至是上一轮的旧图）。
**根因：** 混元给的是 `aiart-*.cos.ap-guangzhou.myqcloud.com` 临时外链，微信**静默过滤**外链图片。
**解决：** 每张图必须先换成 `mmbiz.qpic.cn` 永久 URL 再插入。远程发布已自动完成这一步；
本机直发时用 `upload_content_image.sh` 逐张上传再 `insert_content_image.py` 插入。
插入后必查：
```bash
grep -o '<img src="[^"]*"' article_final.html | grep -v 'mmbiz.qpic.cn'   # 必须为空
```

**症状 B：`draft/update` 返回 47001（数据格式错误）。**
**根因：** 正文里带单引号（`font-family: 'PingFang SC'`）时，经 shell 引号拼 JSON 会被截断。
**解决：** 把 JSON 写进文件再传参，正文里的换行符替换成空格：
```bash
python3 - << 'PY'
import json, io
html = io.open("article_final.html", encoding="utf-8").read().replace("\n", " ")
payload = {"articles": [{"title": "标题", "author": "作者", "digest": "摘要",
                        "content": html, "content_source_url": "",
                        "thumb_media_id": THUMB_ID, "need_open_comment": 1,
                        "only_fans_can_comment": 0, "show_cover_pic": 0}]}
io.open("payload.json", "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False))
PY
curl -s -X POST "https://api.weixin.qq.com/cgi-bin/draft/update?access_token=$TOKEN" \
  -H "Content-Type: application/json" -d @payload.json
```
> 同理，`draft/add` 也要用文件传参；正文超过 1 万字符时改走 payload 文件。

**症状 C：`draft/add` 报 40007 invalid media_id，但封面明明传了。**
**根因：** 封面 `thumb_media_id` 无效。常见原因是按名字查素材时用了错方法：`material/batchget_material` **必须用 POST**（GET 返回 43002），查不到就重复上传，素材库里堆一堆同名图。
**解决：** 用 POST 查素材库复用已有封面：
```bash
curl -s -X POST "https://api.weixin.qq.com/cgi-bin/material/batchget_material?access_token=$TOKEN" \
  -H "Content-Type: application/json" -d '{"type":"image","count":20,"offset":0}'
```

**症状 D：改稿后草稿箱里出现两份。**
**根因：** `draft/update` 失败后改走 `draft/add`，旧草稿没删。
**解决：** 重建后立刻删旧的（删不掉说明它已失效，那也正常）：
```bash
curl -s -X POST "https://api.weixin.qq.com/cgi-bin/draft/delete?access_token=$TOKEN" \
  -H "Content-Type: application/json" -d '{"media_id":"旧MEDIA_ID"}'
```

### ❌ 问题 8：清理素材库时把草稿一起删没了

**症状：** 清理重复封面素材后，草稿箱里的文章少了；或删完素材再 `draft/get` 报 40007。

**根因：** 两件事叠加——
1. **用户在后台打开/编辑过草稿后，WeChat 会重新上传封面**，`thumb_media_id` 就变了（草稿 `media_id` 不变）。
   拿旧的 `thumb_media_id` 去判断「这个素材还有没有人用」，会误判成孤儿。
2. **删除被草稿引用的永久素材，会连带把草稿删掉** —— 草稿的封面就是那个素材。

**解决：** 清理前先取当前真实引用关系，只删真正的孤儿：
```bash
# 1) 取所有草稿当前的 thumb_media_id
curl -s -X POST "https://api.weixin.qq.com/cgi-bin/draft/batchget?access_token=$TOKEN" \
  -H "Content-Type: application/json" -d '{"offset":0,"count":30,"no_content":0}' \
| python3 -c "import json,sys;[print(i['content']['news_item'][0]['thumb_media_id']) for i in json.load(sys.stdin)['item']]"

# 2) 素材库按名字分组（注意 batchget_material 必须 POST）
curl -s -X POST "https://api.weixin.qq.com/cgi-bin/material/batchget_material?access_token=$TOKEN" \
  -H "Content-Type: application/json" -d '{"type":"image","count":20,"offset":0}'

# 3) 只删「不在上面那组 id 里」的同名素材；删完立刻 batchget 复核草稿数量
```
> 更稳的做法：**先删草稿、再删素材**；或先备份草稿内容（本地留一份 final.html），万一被连带删掉可以一键重建
> （正文图的 `mmbiz.qpic.cn` 链接不会因删素材而失效，只有封面需要重传）。

**症状 E：草稿「自己不见了」。** 后台编辑过、或者本地记录过久的草稿，可能已经不在 `batchget` 列表里。
**处理：** 以 `batchget` 的实时结果为准，不要依赖之前记下的 `media_id`；本地保留每篇的 final.html + 配图，可随时重建。

### ❌ 问题 9：远程服务器连不上 / 免密登录失败

**症状：** `remote_publish.py` 打印「远程发布不可用（免密登录未成功），已自动回退到本机发布」，随后本机报 40164。

**原因与排查：**
- **没配 `PUBLISH_HOST`** → 本来就不会走远程（属正常行为，直接本机发稿）。
- **没做免密登录**：ssh 需要能免密上去。先在本机配好密钥：
  ```bash
  ssh-keygen -t ed25519                      # 若还没有公钥
  ssh-copy-id user@host                      # 把公钥装到服务器
  ssh user@host 'echo ok'                   # 验证免密可用
  ```
  脚本用 `BatchMode=yes` + `PasswordAuthentication=no`，**不会提示输密码** —— 所以没配免密就是直接失败。
- **首次连接要确认主机指纹**：脚本用 `StrictHostKeyChecking=accept-new`，第一次会自动记录指纹。
- **服务器缺依赖**：只要 `python3`（≥3.6）和 `curl`：
  ```bash
  ssh user@host 'python3 -V; curl -V | head -1'
  ```
- **服务器 IP 没加白名单**：远程能连上但微信报 40164 → 把服务器出口 IP 加进公众号白名单。

### ❌ 问题 10：正文图在服务器上上传失败

**症状：** 阶段停在 `upload_content_image`，错误码 40005 / 40009。

- **40005**：只支持 JPG/PNG。用 `sips -s format jpeg` 转一下。
- **40009**：图片超过 1MB。`remote_publish.py` 会在本机自动用 sips 压缩（>1MB 时自动转 JPEG），
  没有 sips 的环境会给出明确提示，让你手动转。
- 图片本身损坏 / 扩展名与真实格式不符也会失败，必要时先 `file -b --mime-type` 确认。

---

## 成稿自检（发稿前过一遍）

- **文字润色**：写完稿子用 `/humanizer-zh` SKILL 过一遍 —— 删空泛铺垫与重复、去掉原稿之外的编造细节、统一中文标点、保留事实、确定程度与第一人称语气（不要把「可能是」改成「是」）。
- **标题一致性**：`title` 参数、摘要、正文首句、评论区引导里的说法一致；标题只进 `title`。
- **数据一致性**：正文里出现的数字与来源稿一致（系列稿还要保证脱敏后的口径一致）。
- **配图**：正文图 ≥ 4 张、全部是 `mmbiz.qpic.cn` 永久链接、插入点与上下文自然衔接。
- **字符数**：正文 < 2 万字符（发稿命令会在超限时自动压缩并提示）。

---

## 完整使用示例

```bash
# ===== 推荐：一条命令发稿（远程优先，自动回退）=====
python3 scripts/remote_publish.py \
  --title "AI 时代，敏捷教练该教什么" \
  --html  article.html \
  --cover cover.jpg \
  --image img1.jpg --after-paragraph 3 \
  --image img2.jpg --after-marker "核心观点" \
  --image img3.jpg --after-paragraph 12 \
  --image img4.jpg --after-marker "写在最后" \
  --author "申导" \
  --digest "当交付速度不再是瓶颈，敏捷教练的价值到底在哪里"

# ===== 本机直发（老流程，需要 token；本机 IP 必须在白名单里）=====
TOKEN=$(./scripts/get_access_token.sh | tail -1 | jq -r '.access_token')
THUMB_ID=$(./scripts/upload_image.sh "$TOKEN" cover.jpg | tail -1 | jq -r '.media_id')
URL_1=$(./scripts/upload_content_image.sh "$TOKEN" img1.jpg | tail -1 | jq -r '.url')
URL_2=$(./scripts/upload_content_image.sh "$TOKEN" img2.jpg | tail -1 | jq -r '.url')
URL_3=$(./scripts/upload_content_image.sh "$TOKEN" img3.jpg | tail -1 | jq -r '.url')
URL_4=$(./scripts/upload_content_image.sh "$TOKEN" img4.jpg | tail -1 | jq -r '.url')

cat > /tmp/img_spec.json <<EOF
[
  {"url": "$URL_1", "after_paragraph": 3},
  {"url": "$URL_2", "after_marker": "核心观点"},
  {"url": "$URL_3", "after_paragraph": 12},
  {"url": "$URL_4", "after_marker": "写在最后"}
]
EOF
./scripts/insert_content_image.py --input article.html --output article_final.html --spec /tmp/img_spec.json
FINAL_CONTENT=$(cat article_final.html)
./scripts/add_draft.sh "$TOKEN" "文章标题" "$FINAL_CONTENT" "$THUMB_ID" "作者" "摘要"
```

### 发稿后必做：回查草稿确认图片真的进去了

`draft/add` 返回 `media_id` **只代表入库**，不代表图片可用。
`remote_publish.py` 已自动回查并打印图片张数；本机直发则手动查：

```bash
curl -s -X POST "https://api.weixin.qq.com/cgi-bin/draft/get?access_token=$TOKEN" \
  -H "Content-Type: application/json" -d "{\"media_id\":\"$MEDIA_ID\"}" \
| python3 -c "import json,sys,re;c=json.load(sys.stdin)['news_item'][0]['content'];\
print(len(re.findall(r'<img',c)));[print(m[:90]) for m in re.findall(r'<img[^>]*>',c)]"
```

- 正常：数量 = 预期张数（**≥ 4**），URL 以 `https://mmbiz.qpic.cn/...` 开头。
- 数量少于预期 → 多半是混元临时 URL 没换永久链接，或 URL 带转义反斜杠。
- 微信会把 `src` 改写成 `data-src`（编辑器懒加载，属正常，不是丢图）。
- 数量对但图不显示 → 见「问题 6」的转义问题。

---

## API 参数说明

### 新增草稿必填字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| title | string | 是 | 标题，不超过32字 |
| content | string | 是 | HTML内容，不超过2万字符 |
| thumb_media_id | string | 是 | 封面图片永久素材ID |
| author | string | 否 | 作者，不超过16字 |
| digest | string | 否 | 摘要，不超过128字 |
| show_cover_pic | number | 否 | 是否显示封面，0/1 |
| need_open_comment | number | 否 | 是否打开评论，0/1 |
| only_fans_can_comment | number | 否 | 是否仅粉丝可评论，0/1 |

### 支持的 HTML 标签

- `<p>` - 段落
- `<br>` - 换行
- `<section>` - 区块
- `<img>` - 图片（正文 url 必须来自 `media/uploadimg`，外部 url 会被过滤）
- `<strong>`, `<b>` - 加粗
- `<span style="...">` - 带样式的文本
- `<a href="...">` - 链接
- `<h1>`-`<h6>` - 标题

## 错误码速查

| 错误码 | 说明 | 解决 |
|--------|------|------|
| 40001 | access_token 过期 | 重新获取 |
| 40005 | 文件类型非法（uploadimg） | 仅支持 JPG/PNG |
| 40007 | media_id 无效 | 检查封面图是否上传正确 |
| 40009 | 图片尺寸非法（uploadimg） | 压缩图片至 ≤1MB |
| 40164 | IP 不在白名单 | 加服务器 IP 到白名单；或走远程发稿 |
| 44002 | POST 数据为空 | 检查请求体 |
| 47001 | 数据格式错误 | 检查 JSON 格式 |

## 注意事项

1. **token 有效期 2 小时**，过期需重新获取
2. **封面图必须先上传**，不能直接引用外部 URL
3. **HTML 内容需转义**，避免 JSON 解析失败
4. **IP 白名单必须配置**，否则无法调用 API；本机 IP 常变就用远程发稿
5. **内容大小限制**：正文 < 2万字符，< 1MB
6. **OL/UL 列表会被公众号编辑器篡改**：发稿前会自动 strip 列表内空白；若用其他渠道上传，必须确保 `<li>` 内**不嵌套块级元素**（特别是 `<section>`），并且 `<ol>/<ul>` 与 `<li>` 之间、`<li>` 兄弟之间**零空白**。详见「问题 4」。
7. **草稿 `media_id` 在用户后台操作后立即失效**：`draft/update` 链路只在「同一会话、刚 add 完、用户未介入」时可靠；间隔较长或不确定时，先 `batchget` 再决定 update 还是 add。
8. **标题分离**（核心约定）：标题**只**作为 `title` 参数传入，**不允许**以 `<h1>` 形式留在 body 里。`/jackyshen-write-wechat-article` 输出的 markdown 第一行 `# 标题` 既是标题来源，也必须被剔除。发稿时会自动剥离 body 开头的 `<h1>`（即使嵌在 `<section id="nice">` 包装里），但更稳妥的做法是**渲染前手动从 markdown 中删除 `# 标题` 行**。详见「问题 5」。
9. **正文图片 url 必须来自 `media/uploadimg`**，外部图片 url 会被微信过滤（混元临时链接也会被过滤）。远程发布已自动完成"上传→换永久链接→插入"，本机直发必须自己走完 4.1–4.3。
10. **每篇正文至少 4 张图**：正文图数量下限是 4 张（不含封面），插入点要覆盖头部 / 中前段 / 中后段 / 结尾收束前。
11. **2 万字符是硬限制，且渲染后极易超出**：`wechat-markdown-html-render` 的输出**没有 `<style>` 块**，样式全部内联在元素的 `style` 属性里（实测占 71%）。一篇 ~2800 字的 md 渲染后 22.5k 字符，直接提交会被拒。`wechat_html.py compress_html()` 已内置**视觉等价**压缩 recipe（详见 `wechat_html.py` 与「压缩」一节），发稿命令在超限时自动执行。

    **实测（2026-09-24，6 篇批量发布）**：基础 recipe（去 `data-tool`、合并 padding 等）只减 14%，对 3000+ 字长文不够用。markup（内联 style）占总体积 ~83%，真正的空间在那里。在基础 recipe 之上叠加视觉等价替换（font-size/line-height/text-align 由 `<section id="nice">` 继承；padding:0px 无意义），实测减重 **45%**，且渲染结果肉眼无差异。实测 6 篇：30198→16662、22214→12501、18266→10471、17794→10087、17323→10317、9348→5280。**注意顺序**：先跑基础 recipe，再跑等价替换（基础 recipe 生成的 `padding: 8px 0;` 是 `<p>` 规则的匹配前提）。插入 4 张正文图会再增加约 900 字符，压缩时要留余量。
12. **配图分辨率（huny-img 实测）**：比例字符串（`16:9`、`2.35:1`）会被 API 以 `InvalidParameter` 拒绝，**必须传像素**；`2.35:1` 的封面不要用 `1024:436`（短边 <512 被拒），用 `1203:512`。正文图 `1280:720` 可用，但生成的是 PNG 约 2.7MB，**超过 `uploadimg` 的 1MB 上限**，需先转 JPEG：`sips -s format jpeg -s formatOptions 72 in.png --out out.jpg`（约 100KB）。
13. **脚本输出混了状态行，取字段必须 `| tail -1 | jq`**：`get_access_token.sh`、`upload_image.sh`、`upload_content_image.sh` 都会先打印「✅ xxx 成功」这类中文状态行，再打印 JSON。直接 `| jq -r '.xxx'` 会报 `parse error: Invalid numeric literal`，必须用 `| tail -1 | jq -r '...'`。
14. **IP 白名单生效有延迟**：出口 IP 变化后调用 `cgi-bin/token` 会返回 `40164`，在公众号后台添加 IP 后**约 8 分钟才生效**，刚加完立刻重试仍会失败，别误判为加错了地方。沙箱环境里 `env -u HTTP_PROXY...` 直连与换 ClashX 代理都改不了出口 IP（实测两者出口 IP 相同），只能走白名单或远程发稿。

## 文件结构

```
wechat-mp-draft/
├── SKILL.md                    # 本文件
├── scripts/
│   ├── config.sh               # 凭证 + 可选 PUBLISH_HOST（已 gitignore）
│   ├── config.template.sh      # 配置模板
│   ├── remote_publish.py       # 发稿驱动：远程优先，自动回退本机（主入口）
│   ├── remote_worker.py        # 发布 worker：取 token → 传图 → 插图 → 存草稿 → 回查（本地/服务器通用）
│   ├── wechat_html.py          # 共享库：标题去重 / 列表清洗 / 压缩 / 插图 / 自检
│   ├── get_access_token.sh     # 获取 token（本机直发用）
│   ├── upload_image.sh         # 上传封面图片（永久素材，本机直发用）
│   ├── upload_content_image.sh # 上传正文图片（获取可在 <img> 中使用的 URL，本机直发用）
│   ├── insert_content_image.py # 将 <img> 标签按指定位置插入 HTML
│   └── add_draft.sh            # 保存草稿（本机直发用）
└── references/
    ├── api_reference.md        # API 详细文档
    ├── remote_publish.md       # 远程发稿详解（服务器准备、免密、排障）
    └── sample_cover.png
```
