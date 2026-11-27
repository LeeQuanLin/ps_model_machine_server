# Photoshop 样机工作台

FastAPI 提供生成样机、任务队列、运行日志和输出图四个页面。Celery 通过 Redis 接收任务，在 Windows 桌面会话中调用 Photoshop，对 PSD／PSB 工作副本出图。

## Windows 单机启动

FastAPI、Celery worker 和 Photoshop 运行在同一台 Windows 电脑上。Redis 使用 `.env` 中的 `CELERY_BROKER_URL`；已有待处理任务时请沿用原地址和数据库编号。

依赖和 `.env` 配置完成后，以平时使用 Photoshop 的 Windows 用户登录桌面，在项目目录运行：

```powershell
.\start-studio.ps1
```

脚本验证 Redis 连接和 Photoshop COM，复用已启动的本项目 API 和当前桌面下的项目 worker，并在后台启动缺少的进程。新启动的 API 监听 `0.0.0.0:8765`。本机访问 `http://127.0.0.1:8765/`，局域网设备访问 `http://<Windows电脑的局域网IP>:8765/`。启动日志保存在 `data/studio/logs/`。worker 出图期间请保持 Windows 用户处于登录状态。

如果旧版 API 只监听 `127.0.0.1`，重新运行新版脚本会核对占用端口的项目进程，重启该 API，并检查实际监听地址。看到 `API listening on 0.0.0.0:8765` 后，同一局域网内的电脑可使用输出的对应 `LAN URL` 访问。

### 首次安装与手动启动

需要 Windows、Python 3.12 或更新版本、已注册 COM 的 Photoshop，以及 API 和 worker 均可访问的 Redis。下面的命令从项目根目录运行。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

在 `.env` 设置访问令牌和 Redis 地址。已有项目可以沿用原来的 `PHOTOSHOP_API_TOKEN`。使用有密码的 Redis 时，连接串可写成 `redis://:密码@主机:6379/0`，密码中的特殊字符需要 URL 编码。

```dotenv
PHOTOSHOP_API_TOKEN=换成自己的随机令牌
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
```

把样机放在 `templates/` 下，子目录也会被扫描。启动 API：

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8765
```

在同一台 Windows 电脑上，以能正常打开 Photoshop 的已登录用户另开一个终端，启动一个串行 worker：

```powershell
.\.venv\Scripts\python -m celery -A app.worker:celery worker --pool=solo --concurrency=1 -Q photoshop.mockups -l INFO
```

打开 `http://127.0.0.1:8765/`，点击「连接工作台」输入令牌。页面将令牌保存在当前标签页的会话存储中。API 文档位于 `/docs`，业务接口使用 `Authorization: Bearer <令牌>`。

## 生成样机

| 图层 | 输入 | Photoshop 处理 |
| --- | --- | --- |
| A面 | PNG 或 JPEG，可选 | 替换同名嵌入式智能对象 |
| B面 | `.env` 预设色或自选颜色 | 为同名智能对象换上纯色图，或修改纯色填充层、像素层 |
| 枕套 | PNG 或 JPEG，可选 | 优先查找「枕套」，也识别「枕头」 |
| 背景 | A 面主题色或手选色 | 与 B 面使用相同的着色方式 |

A 面和枕套支持点击选图，也可以把单张 PNG 或 JPEG 拖到对应的上传框。提交成功后清空两张输入图片和输出文件名，提交失败可直接重试。

「输出文件名」可留空，下载时沿用样机名和任务 ID。填写后按该名称下载 PNG；一次提交生成多款时，依次添加 `-01`、`-02` 等序号。例如填写「春日花园」，单款下载为 `春日花园.png`，多款从 `春日花园-01.png` 开始。

在项目根目录的 `.env` 设置 B 面预设色，按显示顺序用英文逗号分隔。第一项是打开页面时的默认色，可以写 `名称:#RRGGBB` 或只写色值。保留外层引号。修改后重启 FastAPI 并刷新页面即可生效。

```dotenv
B_SIDE_PRESET_COLORS="雾蓝:#A4B6C9,米白:#E9E2D4,豆绿:#A3B59C"
```

上传 A 面后，浏览器会统计缩略图的主要颜色，忽略近乎透明的像素，并更新背景预览。手动模式会保留所选色值。没有 A 面图案或图案完全透明时，自动模式使用页面展示的米白色。图片层未上传时沿用模板原样。右侧显示四个图层的平铺缩略图，Photoshop 出图时按智能对象画布居中裁切、缩放图案，并沿用样机里的变形和光影。

样机模板默认选择「全部样机」，为目录中的每款模板各创建一条任务。选择「随机选择」时，每次提交从当前模板中随机抽取一款，连续两次可能选中同一款。选择「顺序选择」时，每次提交按下拉列表顺序取一款，到末尾后回到第一款。所有用户共用轮选进度，服务重启后继续；若上次选中的模板已移除，则从当前第一款开始。也可以直接指定某款模板。队列表格显示此次实际选中的样机名。

Worker 打开每个模板的私有副本后递归查找图层，同名时优先使用「贴图区」中的目标。共用内容的智能对象副本会同步更新。缺少的对应图层会跳过并记入日志。全部跳过时，队列表格显示「已跳过」。完成的任务可在「输出图」查看原图或下载 PNG。

## 队列与数据

1. 提交后，API 将任务和上传图片保存到本地，并返回 HTTP 202 与任务 ID 列表。
2. API 后台将待投递任务送入 Redis。投递失败后自动重试，API 重启后继续投递尚未发出的任务。
3. Worker 逐个生成原图与缩略图，将结果和跳过的图层记录在 SQLite。重复收到已完成的任务时复用结果。同一批次的所有任务结束后释放输入图片。

在项目根目录的 `.env` 中设置 `PHOTOSHOP_RESTART_AFTER_TASKS=10`，指定 Photoshop 每处理多少份样机自动重启一次。可填正整数，未配置时默认 10。修改后重启 Celery worker 生效。每份样机计一次，同一份样机替换多个图层也只计一次。Photoshop 返回生成、跳过或处理错误时均计数。计数保存在数据目录，重启 worker 后继续累计。

达到配置次数且任务结果写入后，worker 让 Photoshop 正常退出，等待旧进程结束，再启动 Photoshop 并通过 COM 确认就绪。如果调低次数后当前计数已达到新值，worker 会在处理下一份样机前重启 Photoshop。有未保存文档时，worker 会保留文档并在「运行日志」记录原因。下一条任务等待重启，每 60 秒重试；保存或关闭文档后即可继续。

| 路径 | 内容 |
| --- | --- |
| `templates/` | PSD、PSB 样机模板 |
| `data/studio/studio.sqlite3` | 新版队列、任务状态和日志 |
| `data/studio/outputs/<任务ID>/` | 原尺寸 PNG 与展示用缩略图 |
| `archive/legacy/` | 重构前的项目源码、管理页面、命令行脚本和文档 |

API 和 worker 使用同一份模板和数据目录。更新部署时，保留 `.env`、`templates/` 和 `data/`，安装新依赖并重启两个进程。旧版生成记录和文件保存在原数据目录中，新工作台独立记录此次重构后的任务。

## 单文件命令行

```powershell
python .\贴样机.py "样机.psb" -o "output.png" -l "A面" "正面.png" "B面" "背面.png" "枕头" "枕套.png"
```

独立脚本只依赖 Python 标准库、Windows PowerShell 和 Photoshop。命令行使用精确图层名，重名时可写完整路径，如 `贴图区/A面`。输出需指定一个新文件名。
