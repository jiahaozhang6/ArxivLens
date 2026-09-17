# ArxivLens 每日科研论文助手

ArxivLens 是一个可在 Windows 和 Ubuntu 原生运行的 arXiv 论文订阅、云端 LLM 解读与科研阅读管理系统。它每天按主题检索论文，保存元数据和多次分析结果，在浏览器中按日期筛选，也可以通过 SMTP 发送当日摘要。

> **在线 Demo：** [打开 ArxivLens 每日科研论文助手](https://58.87.103.33:8888/)

本项目明确采用以下运行方式：

- 不使用 Docker。
- 不调用 Ollama、LM Studio 等本地模型。
- LLM 只使用用户配置的国内或国外云端 API。
- API 与定时 worker 分进程运行，避免 Web 多进程导致重复调度。

## 主要能力

- 多主题 arXiv Atom API 检索，支持回溯天数、每主题数量和检索式预览。
- arXiv 请求在 API 与 worker 进程之间统一限速；相同查询共享缓存，API 限流或故障时会合并最近缓存与官方分类 RSS 降级结果。
- 每日任务使用 API/worker 共享的系统文件锁；异常退出或服务重启后会自动回收遗留的“运行中”记录。
- 设备休眠或 worker 晚启动时，只要仍是计划日期，就会自动补跑且每天最多执行一次计划任务。
- 每日邮件会发送新增论文的有效解读；当天没有新增论文时发送简洁通知，并且不会重复调用 LLM。
- 按 arXiv ID 去重，识别论文版本更新，并在新版本出现后重新分析。
- 支持摘要解读或 PDF 正文解读；PDF 失败时自动回退到摘要。
- 结构化科研分析：摘要、研究问题、贡献、方法、实验、局限、阅读建议、相关性理由、关键词，以及相关性/新颖性/严谨性评分。
- 国内云模型：DeepSeek、通义千问、智谱 GLM、Kimi、硅基流动。
- 国外云模型：OpenAI、Anthropic Claude、Google Gemini，以及自定义 OpenAI-compatible 云 API。
- 首选模型异常时自动切换：先尝试全局默认模型，再按名称尝试其他已启用配置；定时批处理会暂时冷却故障配置，避免每篇论文重复等待同一故障接口。
- API 密钥和 SMTP 密码使用按用途隔离、支持轮换的版本化加密后存入数据库，前端不会回显。
- 管理员账号使用 scrypt 加盐密码哈希保护；访客仅获得签名只读会话，不能修改数据或调用云模型。
- 所有云模型调用均由服务端管理员鉴权；论文问答默认最多每分钟 8 次、同时只运行 1 个请求，阅读端不提供模型调用按钮。
- 每日计划、IANA 时区、多收件人邮件摘要、测试邮件和运行日志。
- 按发现日期、主题、阅读状态、分析状态和关键字筛选；支持星标、人工相关性、标签和个人笔记。
- 科研阅读端与后台管理端采用独立路由和独立布局，阅读时不会被管理菜单干扰。
- 独立论文精读页只展示已有论文与解读，不暴露任何云模型调用入口。流式 AI 问答、模型选择、自动备用模型切换、停止生成和持久化历史仅位于管理员后台论文详情中。
- 保存完整分析历史，支持针对单篇论文重新分析和 Markdown 导出。
- SQLite 默认存储，支持迁移到 PostgreSQL；内置 Alembic 迁移和一致性备份命令。

## 架构

```text
Browser
  -> FastAPI API + frontend/dist
       -> SQLite / PostgreSQL
       -> arXiv API
       -> cloud LLM APIs
       -> SMTP

APScheduler worker
  -> 每 30 秒刷新数据库中的计划配置
  -> 到点执行抓取、分析、存储和邮件任务
```

主要目录：

```text
backend/app/          FastAPI、数据模型、抓取、LLM、邮件和任务流水线
backend/alembic/      数据库迁移
backend/tests/        后端测试
frontend/src/         React 科研阅读端与后台管理端
scripts/              Windows PowerShell 与 Ubuntu Bash 运维脚本
data/                 默认 SQLite 数据库（被 Git 忽略）
backups/              SQLite 在线备份（被 Git 忽略）
logs/                 Windows/开发模式日志（被 Git 忽略）
```

## 环境要求

- Windows 10/11，或带 systemd 的 Ubuntu。
- `uv`。它会按 `backend/uv.lock` 建立 Python 3.11+ 环境。
- Node.js 20 或更高版本，用于构建前端。
- 能访问 arXiv、所选云模型 API 和 SMTP 服务的网络。

仓库已将 Python 默认索引配置为清华 PyPI，将 npm registry 配置为 npmmirror；Ubuntu 系统软件包可使用云厂商提供的国内镜像。本配置不改变锁文件中的依赖版本。

安装 `uv`：

```powershell
# Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```bash
# Ubuntu
curl -LsSf https://astral.sh/uv/install.sh | sh
```

安装后重新打开终端，确认 `uv --version`、`node --version` 和 `npm --version` 可用。

## Windows 快速开始

在项目根目录运行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup.ps1
```

脚本会执行以下操作：

1. 创建 `.env`，并生成随机且稳定的 `SECRET_KEY`。
2. 按锁文件安装后端与测试依赖。
3. 执行 Alembic 数据库迁移。
4. 安装前端依赖并生成 `frontend/dist`。

开发联调可以直接运行：

```powershell
.\scripts\dev.ps1
```

浏览器入口：

- 科研阅读端：`http://127.0.0.1:5173/#/`
- 论文精读页：从科研阅读端点击“打开精读”，进入 `/#/paper/<论文ID>`。
- 后台管理端：`http://127.0.0.1:5173/#/admin/daily`
- 局域网设备：将 `127.0.0.1` 替换为启动脚本显示的 LAN IPv4 地址。

首次打开会进入安全初始化页，请自行创建管理员用户名和至少 14 个字符的密码。管理员可访问阅读端与后台端；访客入口只能查看论文和已有解读。后端和 worker 日志位于 `logs/`，按 `Ctrl+C` 会停止本次启动的进程。

生产式本机运行需要两个终端：

```powershell
# 终端 1
.\scripts\start-api.ps1

# 终端 2
.\scripts\start-worker.ps1
```

此时 FastAPI 会直接提供已经构建的前端：

- 科研阅读端：`http://127.0.0.1:8000/#/`
- 后台管理端：`http://127.0.0.1:8000/#/admin/daily`

首次打开同样需要创建管理员账号。

### 局域网访问

默认 `.env` 和开发脚本监听 `0.0.0.0`。同一局域网设备可通过
`http://<LAN-IP>:5173/#/`（开发模式）或 `http://<LAN-IP>:8000/#/`（构建后运行）访问。
Windows 防火墙只需允许 TCP 5173、8000 的 `LocalSubnet` 入站；不要将这些端口直接映射到公网。

### Windows 自动启动

以下脚本为当前登录用户安装两个任务计划程序任务，并立即启动：

```powershell
.\scripts\install-windows-tasks.ps1
```

当前服务已经手动启动、不希望安装过程重复启动时使用：

```powershell
.\scripts\install-windows-tasks.ps1 -NoStart
```

- `ArxivLens-API`：登录后启动 Web/API。
- `ArxivLens-Worker`：延迟 15 秒启动每日调度进程。
- 日志：`logs\api.log`、`logs\worker.log`。

移除自动启动但保留项目和数据：

```powershell
.\scripts\uninstall-windows-tasks.ps1
```

## Ubuntu 快速开始

```bash
chmod +x scripts/*.sh
./scripts/setup.sh
```

开发联调：

```bash
./scripts/dev.sh
```

生产式前台运行：

```bash
# 终端 1
./scripts/start-api.sh

# 终端 2
./scripts/start-worker.sh
```

### Ubuntu systemd

先用普通运行用户执行 `setup.sh`，再安装服务：

```bash
./scripts/install-systemd.sh
```

如果需要指定服务用户：

```bash
ARXIV_DIGEST_USER=research ./scripts/install-systemd.sh
```

常用命令：

```bash
sudo systemctl status arxiv-digest-api arxiv-digest-worker
sudo journalctl -u arxiv-digest-api -u arxiv-digest-worker -f
sudo systemctl restart arxiv-digest-api arxiv-digest-worker
```

公网部署时让 API 继续监听 `127.0.0.1:8000`，由 Caddy 提供 HTTPS。仓库中的 `deploy/Caddyfile` 关闭了 80 端口自动跳转，并同时提供标准 443 主入口和 8888 备用入口。当前部署地址为 `https://paper.jiahaozhang.cn/`，备用地址为 `https://paper.jiahaozhang.cn:8888/`；其他服务器使用前应替换其中的域名和 IP。

服务器与云安全组需要开放 SSH、`443/tcp` 和 `8888/tcp`，不需要开放 80。域名入口由 Caddy 自动申请和续期公共证书；IP 形式的 `https://<SERVER-IP>:8888/` 仅作为内部 CA 回退入口，客户端需要信任 `deploy/arxiv-lens-root-ca.crt`。启用 HTTPS 后设置 `AUTH_COOKIE_SECURE=true`，并把 `CORS_ORIGINS` 和后台的“站点公开地址”更新为实际域名。

卸载 systemd 服务但保留数据：

```bash
./scripts/uninstall-systemd.sh
```

## 第一次使用

1. 打开“云模型”，添加一个云模型配置，填写平台实际提供的 API Key，并设为默认模型。
2. 点击“获取模型”，从该密钥有权访问的模型列表中选择；若平台没有开放模型列表接口，也可以继续手动填写模型 ID。
3. 保存后点击“测试连接”。这会产生一次很小的真实云 API 请求，用于验证鉴权和结构化 JSON 输出。
4. 打开“主题订阅”，创建一个或多个 arXiv 检索式。先点“测试检索”，确认能命中预期论文。
5. 打开“计划与邮件”，设置时区和每天运行时间。默认是 `Asia/Shanghai` 的 `08:00`。
6. 可选配置 SMTP，先保存再发送测试邮件。
7. 点击右上角“立即更新”完成第一次抓取。之后 worker 会按计划自动运行。

每日页面中的日期表示“本系统首次在某主题下发现论文的日期”，不是论文原始投稿日期。论文卡片内仍会显示正式发表日期和 arXiv 版本号。

## 云模型配置

内置预设只是可编辑的起点。云平台可能新增、下线或限制模型，请优先使用账户控制台中实际可用的模型 ID。

| 平台 | 区域 | 协议 | 预设模型示例 |
| --- | --- | --- | --- |
| DeepSeek | 国内 | OpenAI-compatible | `deepseek-v4-flash` |
| 阿里云百炼/通义千问 | 国内 | OpenAI-compatible | `qwen-plus` |
| 智谱 BigModel | 国内 | OpenAI-compatible | `glm-5.3` |
| Kimi | 国内/国际 | OpenAI-compatible | `kimi-k3` |
| 硅基流动 | 国内 | OpenAI-compatible | `deepseek-ai/DeepSeek-V3.2` |
| OpenAI | 国外 | OpenAI-compatible | `gpt-5.4-mini` |
| Anthropic | 国外 | Anthropic Messages | `claude-sonnet-5` |
| Google Gemini | 国外 | OpenAI-compatible | `gemini-3.8-flash` |

配置说明：

- `API Base URL` 填平台 API 根路径，不要填网页聊天地址。
- “获取模型”会调用平台的模型列表 API，并允许直接选择返回的模型；平台不支持该接口时仍可手动填写。
- `模型 ID` 必须与平台控制台完全一致。编辑配置且修改了 Base URL 或协议后，需要重新输入 API Key 才能获取模型，避免把已保存密钥发送到新的地址。
- `JSON mode` 不受支持时可关闭；系统仍会从普通文本中提取 JSON。
- `附加请求参数 JSON` 可传平台特有参数，但不能覆盖 `model`、`messages`、`system` 或 `stream`。
- 同一主题可以绑定专用模型；未绑定时使用全局默认模型。
- 项目不会扫描或连接本机模型端口。自定义配置也应指向用户信任的云端 HTTPS API。

## arXiv 检索式

常用字段：

- `cat:`：分类，例如 `cat:cs.AI`、`cat:cs.CL`、`cat:stat.ML`。
- `all:`：标题、摘要、作者等全部字段。
- `ti:`：标题。
- `abs:`：摘要。
- `au:`：作者。
- 使用 `AND`、`OR`、`ANDNOT` 和括号组合条件；短语放在英文双引号中。

示例：

```text
cat:cs.AI AND all:"scientific reasoning"

(cat:cs.CL OR cat:cs.AI) AND (all:"retrieval augmented generation" OR all:RAG)

cat:stat.ML AND abs:"causal representation learning"

au:"Geoffrey Hinton" AND cat:cs.LG
```

建议从较宽检索式开始，通过“相关性判断标准”告诉 LLM 哪些方向优先、哪些内容排除。arXiv 周末、节假日或某些小领域当天没有新稿是正常情况。

## PDF 与成本控制

默认只分析摘要，速度快且成本低。只有确实需要方法和实验细节的主题才建议启用“优先读取正文”。正文提取依次尝试 arXiv LaTeX 源码、官方 HTML 和 PDF，前一种不可用时会自动进入下一种。

可在 `.env` 调整：

```dotenv
LLM_CONCURRENCY=2
LLM_FALLBACK_MAX_PROFILES=3
LLM_PROFILE_COOLDOWN_SECONDS=600
PDF_MAX_PAGES=30
PDF_MAX_CHARS=120000
HTTP_TIMEOUT_SECONDS=90
```

控制费用的主要手段：

- 降低主题的“每次最多论文数”。
- 缩短回溯天数；首次运行可临时增大，稳定后改为 2 到 4 天。
- 摘要模式用于日常筛选，只给核心主题启用 PDF。
- 为批量初筛选择低成本模型，为少量重点论文手动重新选择高能力模型。
- 避免频繁点击模型连接测试和单篇重新分析；它们都是实际计费请求。

日常发现默认直接读取各学科的 arXiv Atom RSS，并在本地执行检索式过滤，不会先请求容易触发 429 的 Atom 搜索 API。RSS 分类快照由所有主题共享；RSS 不可用时使用 OpenAlex 恢复检索，最后才尝试带持久冷却的 Atom API。

默认只接收首次发布的 `new` 论文。主题高级设置可以开启“包含交叉分类投稿”，同时接收 `cross`。Atom API 一旦返回 429 会进入 6 至 24 小时持久冷却，服务重启不会清除冷却状态。

## 网络时间与每日计划

计划页面使用 `HH:mm` 时间选择器，并显示当前校准时间、时钟偏差和下一次运行倒计时。后端默认每 30 分钟从多个 NTP 服务获取时间并取中位偏差；若 UDP 123 被网络限制，则尝试 HTTPS `Date` 时间源。worker 每 30 秒依据校准时间检查计划，并为休眠、断网或短暂停机执行当日补跑。若计划任务因为论文源完全不可用而零结果，会在 30、90、180 分钟后进行最多三次自动补跑。

应用只在进程内计算时间偏差，不会修改 Windows 或 Ubuntu 的系统时钟。所有网络源都不可用时会明确显示“使用系统时间”，每日任务仍可运行。网络时间源和同步间隔可通过 `.env` 中的 `NETWORK_TIME_*` 配置调整。

## 邮件配置

系统使用标准 SMTP 用户名/密码或授权码，不包含 OAuth 登录流程。常见配置示例：

| 服务 | SMTP 主机 | 端口与安全方式 | 凭据 |
| --- | --- | --- | --- |
| QQ 邮箱 | `smtp.qq.com` | 465 + SSL/TLS | 邮箱授权码 |
| 163 邮箱 | `smtp.163.com` | 465 + SSL/TLS | 客户端授权码 |
| Gmail | `smtp.gmail.com` | 587 + STARTTLS，或 465 + SSL/TLS | 开启两步验证后的应用专用密码 |
| 学校/机构邮箱 | 由管理员提供 | 587 + STARTTLS 常见 | 专用 SMTP 凭据 |

SMTP 用户名通常是完整邮箱地址，不是发件人显示名称。QQ 邮箱的密码栏应填写在邮箱设置中生成的授权码，而不是网页登录密码。页面中的“发送测试”会直接使用当前表单，但不会自动保存；测试成功后仍需点击“保存邮件”。

部分 Microsoft 365、企业邮箱和学校邮箱已禁止基本密码认证；这类账号如果只允许 OAuth，当前 SMTP 方式不能使用，需要改用管理员提供的 SMTP Relay 或其他允许的发件账号。

“站点公开地址”用于生成邮件中的论文详情链接。仅本机阅读可保留 `http://localhost:8000`；部署到内网或反向代理后应改为真实 HTTPS 地址。

## 数据库、迁移与备份

默认数据库是：

```text
data/arxiv_digest.db
```

API 和 worker 启动时会使用文件锁串行执行 `alembic upgrade head`。代码更新后也可以主动运行：

```powershell
.\scripts\migrate.ps1
```

```bash
./scripts/migrate.sh
```

SQLite 在线一致性备份不要求停止服务：

```powershell
.\scripts\backup.ps1
.\scripts\backup.ps1 -Output E:\research-backups
```

```bash
./scripts/backup.sh
./scripts/backup.sh /srv/research-backups
```

备份完成后会执行 SQLite 完整性检查。默认文件保存在 `backups/arxiv-digest-YYYYMMDD-HHMMSS.db`。

恢复前必须停止 API 和 worker，然后把当前数据库及 `-wal`、`-shm` 边车文件移走，再将备份复制为 `data/arxiv_digest.db`。不要在进程运行时直接覆盖数据库。恢复后先执行迁移，再启动服务。

### 切换 PostgreSQL

先创建空数据库和专用用户，然后在 `.env` 设置异步连接串：

```dotenv
DATABASE_URL=postgresql+asyncpg://arxiv_user:strong_password@127.0.0.1:5432/arxiv_digest
```

再执行 `migrate.ps1` 或 `migrate.sh` 并重启 API/worker。PostgreSQL 备份应使用服务器对应版本的 `pg_dump`；本项目的 `backup` 脚本只处理 SQLite。

## 安全建议

- `.env`、`data/`、`backups/` 和 `logs/` 已被 Git 忽略，不要手动提交。
- `SECRET_KEY` 通过 HKDF-SHA256 分别派生 LLM 与 SMTP 加密密钥。轮换时先将旧值写入 `SECRET_KEY_PREVIOUS`，设置新的 `SECRET_KEY`，运行 `python -m app.secret_migration` 后再清空旧值。
- 管理员密码使用 scrypt 加盐哈希，原始密码不会写入数据库；旧 PBKDF2 哈希会在下次成功登录后自动升级。密码至少 14 个字符，建议使用 20 个字符以上的独立密码短语。
- 默认会话有效期为 24 小时，并绑定登录时的浏览器 User-Agent；连续 5 次密码错误会锁定 15 分钟，可在 `.env` 调整 `AUTH_SESSION_HOURS`、`AUTH_LOGIN_MAX_ATTEMPTS` 和 `AUTH_LOCK_MINUTES`。
- 访客会话由服务端签名且仅允许读取公开论文字段和已有解读。私人笔记、标签、收藏、已读状态、配置、任务与云模型接口均由后端拒绝访问。
- 只有通过 HTTPS 访问时才设置 `AUTH_COOKIE_SECURE=true`；在纯 HTTP 本机开发环境启用后，浏览器不会发送登录 Cookie。
- 默认只监听 `127.0.0.1`。如果把 `API_HOST` 改为 `0.0.0.0`，必须同时使用防火墙、反向代理 HTTPS 和访问控制；当前项目不内置多用户登录系统。
- 仅配置可信云平台 URL。自定义 API 会接收论文文本和研究主题说明。
- 定期把 SQLite 备份复制到另一块磁盘或受控备份空间，并定期验证恢复流程。
- 邮件、运行日志和个人笔记可能包含未公开研究方向，按科研数据管理要求控制访问。

忘记密码时，必须在运行 ArxivLens 的本机终端重置。该操作会撤销所有已登录会话：

```powershell
.\scripts\reset-admin-password.ps1
```

```bash
./scripts/reset-admin-password.sh
```

## 更新项目

更新代码后重新运行 setup 即可同步依赖、迁移数据库并重建前端：

```powershell
.\scripts\setup.ps1
```

```bash
./scripts/setup.sh
sudo systemctl restart arxiv-digest-api arxiv-digest-worker
```

更新前建议先运行一次备份。

## 故障排查

- 页面打不开：确认 API 正在运行，并检查 8000 端口是否被占用。
- 页面打开但计划不执行：API 不是调度器，必须同时运行 worker；查看“运行记录”和 worker 日志。
- 系统提示主题没有可用模型：启用一个默认模型，或给每个主题绑定已启用的模型。
- arXiv 没有结果：先在主题窗口测试检索，扩大回溯天数，检查字段、引号和布尔表达式。
- arXiv 返回 `429 Rate exceeded`：日常分类检索不会依赖 Atom API；Atom API 会进入 6 至 24 小时持久冷却，期间继续使用 RSS、OpenAlex 和七天内缓存。不要连续点击“立即更新”，并在 `.env` 填写真实 `ARXIV_CONTACT_EMAIL`。
- 任务长期显示“运行中”：新版会在 API/worker 启动或下一次运行前自动识别已释放的任务锁，并将被重启或异常退出中断的记录标记为失败；正常运行中的另一个进程不会被误回收。
- 云模型返回 400：核对模型 ID、Base URL、账户区域和余额；尝试关闭 JSON mode，并检查附加参数。
- 云模型返回 401/403：重新保存 API Key；Kimi 等平台的不同站点密钥可能不能混用。
- 提示无法解密：`.env` 中的 `SECRET_KEY` 与保存密钥时不同，应恢复原 `.env` 或重新录入所有密钥。
- 正文解读退回摘要：表示 LaTeX、HTML 和 PDF 三种方式都不可用，常见原因是下载受限、扫描版 PDF、文件超限或正文过短；详情页会保留每一级回退原因。
- SQLite 锁定：确认没有手工复制/恢复数据库，避免在网络共享盘上运行 SQLite；高并发或多主机部署应改用 PostgreSQL。
- 邮件失败：先保存配置，再测试；确认使用授权码而不是网页登录密码，并检查 SMTP 端口是否被网络阻断。

API 健康检查与交互文档：

```text
http://127.0.0.1:8000/api/health
http://127.0.0.1:8000/docs
```

## 开发验证

```powershell
cd backend
uv run ruff check app tests alembic
uv run pytest -q

cd ..\frontend
npm run lint
npm run build
```

真实 LLM 和 SMTP 测试需要在前端配置你自己的凭据。测试代码不会附带或使用任何默认 API Key。
