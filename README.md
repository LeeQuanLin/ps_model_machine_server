# Photoshop 样机工作台

v2 以产品为单位组织出图。工作台提供「产品列表」「任务队列」「日志」「输出图管理」四个页面。Celery 通过 Redis 接收任务，在 Windows 桌面会话中调用 Photoshop，对 PSD／PSB 工作副本出图。上一版源码位于 [version/v1](version/v1/)。

## Windows 单机启动

FastAPI、Celery worker 和 Photoshop 运行在同一台 Windows 电脑上。Redis 使用 `.env` 中的 `CELERY_BROKER_URL`；已有待处理任务时请沿用原地址和数据库编号。

依赖和 `.env` 配置完成后，以平时使用 Photoshop 的 Windows 用户登录桌面，在项目目录运行：

```powershell
.\start-studio.ps1
```

脚本验证 Redis 连接和 Photoshop COM，每次运行都会完整重启本项目的 API 和 worker，再在后台启动新进程。正在出图的 worker 会先完成当前任务再退出，最多等待 180 秒。新启动的 API 监听 `0.0.0.0:8765`。本机访问 `http://127.0.0.1:8765/`，局域网设备访问 `http://<Windows电脑的局域网IP>:8765/`。启动日志保存在 `data/studio/logs/`。worker 出图期间请保持 Windows 用户处于登录状态。

侧边栏底部显示数据目录所在磁盘的「磁盘空间」总容量，并用进度条表示可用空间；「已占用」是项目数据目录（`data/studio`）的实际大小，不是整个磁盘的已用空间。单位按大小自动使用 MB/GB/TB；连接后每分钟刷新一次，删除输出图后会立即刷新。

脚本每次都会核对占用 8765 的项目进程并重启 API；如果端口被其他服务占用，会提示先停止。看到 `API listening on 0.0.0.0:8765` 后，同一局域网内的电脑可使用输出的对应 `LAN URL` 访问；浏览器里已打开的页面刷新一次即可。

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

把样机放在 `templates/` 下，子目录也会被扫描。详情页默认使用 `templates/详情页模板.psd`，也支持同名 PSB。使用其他文件时，在 `.env` 将 `DETAIL_PAGE_TEMPLATE` 设为相对 `templates/` 的路径，例如 `DETAIL_PAGE_TEMPLATE=详情页/长版.psb`。详情页模板需要有「替换区」分组，样机模板需要有「主体」分组才能生成被抽中的 SKU 透明图。

启动 API：

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8765
```

在同一台 Windows 电脑上，以能正常打开 Photoshop 的已登录用户另开一个终端，启动一个串行 worker：

```powershell
.\.venv\Scripts\python -m celery -A app.worker:celery worker --pool=solo --concurrency=1 -l INFO
```

打开 `http://127.0.0.1:8765/`，点击「连接工作台」输入令牌。页面将令牌保存在当前标签页的会话存储中。API 文档位于 `/docs`，业务接口使用 `Authorization: Bearer <令牌>`。

## 产品出图

1. 在「产品列表」点击「新增产品」，输入产品名，再点击「添加产品图」。
2. 输入 SKU 名、选择一种样机模板选取方式，上传图片并配置颜色。「提交并开始出图」会立刻将本次 SKU 投入队列，并返回产品配置窗口。关闭弹窗后，可从产品列表继续配置草稿。
3. 点击「保存产品」并返回产品列表。系统随机选一个 SKU，等其样机出图后生成一张 800 × 800 px 的透明图。全部 SKU 样机和透明图成功后，系统按 SKU 添加顺序把样机合成图填入详情页模板，从上到下切片，末片按剩余高度输出。
4. 在产品列表查看首个 SKU 的样机缩略图、SKU 进度和当前阶段。完成后可单独下载 `产品名.zip`，或勾选多个产品下载一个 ZIP。每个产品目录内含「样机图」「透明图」「详情页」。列表显示最近一次产品 ZIP 下载时间，初始显示「未下载」。

在项目根目录的 `.env` 设置 `DETAIL_PAGE_SLICE_HEIGHT=960` 可调整工作台详情页的切片高度。单位为像素，填写正整数，默认 960。修改后重启 worker 生效。

在「输出图管理」可查看和分别下载样机图、透明图和详情页切片。草稿或尚在生成的产品仍会使用其已有出图。产品流程结束后可单独删除输出，删除后该产品的完整 ZIP 将无法下载。

### SKU 图层和模板

| 图层 | 输入 | Photoshop 处理 |
| --- | --- | --- |
| A面 | PNG 或 JPEG，可选 | 替换同名嵌入式智能对象 |
| B面 | `.env` 预设色或自选颜色 | 为同名智能对象换上纯色图，或修改纯色填充层、像素层 |
| 枕套 | PNG 或 JPEG，可选 | 优先查找「枕套」，也识别「枕头」 |
| 背景 | A 面主题色或手选色 | 与 B 面使用相同的着色方式 |

A 面和枕套支持点击选图，也可以把单张 PNG 或 JPEG 拖到对应的上传框。样机合成图以 `SKU名.png` 命名，透明图以 `SKU名-透明图.png` 命名。

在项目根目录的 `.env` 设置 B 面预设色，按显示顺序用英文逗号分隔。第一项是打开页面时的默认色，可以写 `名称:#RRGGBB` 或只写色值。保留外层引号。修改后重启 FastAPI 并刷新页面即可生效。

```dotenv
B_SIDE_PRESET_COLORS="雾蓝:#A4B6C9,米白:#E9E2D4,豆绿:#A3B59C"
```

上传 A 面后，浏览器会统计缩略图的主要颜色，忽略近乎透明的像素，并更新背景预览。手动模式会保留所选色值。没有 A 面图案或图案完全透明时，自动模式使用页面展示的米白色。图片层未上传时沿用模板原样。右侧显示四个图层的平铺缩略图，Photoshop 出图时按智能对象画布居中裁切、缩放图案，并沿用样机里的变形和光影。

每个 SKU 只用一款样机，可以指定模板、随机选择，或按模板下拉列表顺序循环选取。「顺序选择」的进度由所有用户共用，重启后继续。队列表格会显示此次实际用到的样机。详情页模板从样机选项中排除。

Worker 打开每个模板的私有副本后递归查找图层，同名时优先使用「贴图区」中的目标。共用内容的智能对象副本会同步更新。缺少的对应图层会跳过并记入日志。全部跳过时，队列表格显示「已跳过」。如果有 SKU 或被抽中的透明图出图失败，产品会显示失败原因，仍可在「输出图管理」查看已成功生成的图片。

## 队列与数据

1. 提交 SKU 后，API 将任务和上传图片保存到本地，并返回 HTTP 202。
2. API 后台将待投递任务送入 Redis。投递失败后自动重试，API 重启后继续投递尚未发出的任务。
3. Worker 记录各阶段结果。前置任务完成时，系统在同一个 SQLite 事务中将后续任务转为待投递。保存产品后，SKU 样机和可能复用其素材的透明图任务完成时释放对应上传图片。

「已生成图片」的每张卡片可以单独删除。删除会移除该任务的输出文件和输出图列表入口，任务队列与运行日志仍保留，并记录「输出图已删除」。图片正在下载或被占用时会提示稍后重试。

队列名由 Windows 主机名和数据目录生成。即使多台电脑共用一个 Redis，每台也只处理自己的素材。升级时保留原数据目录，在出图电脑的已登录桌面运行 `.\start-studio.ps1`。脚本每次都会完整重启 API 和 worker，并校验 worker 订阅的是本机专用队列。首次启动新版 API 会自动补投旧队列中状态仍为「排队中」的本地任务，沿用原任务 ID 和素材。

多台电脑共用一个 Redis 时，每台电脑都必须运行同一份新版代码，并在升级后重启 API 和 worker；Python 进程不会自动加载新代码。不要手工用旧队列名（`photoshop.mockups`）启动 worker，否则它会抢走其他电脑的任务，而任务素材只存在提交任务的电脑上。启动脚本会先停掉本机所有项目 worker（包括旧队列），再重启 API 和当前队列 worker；正在出图时会等当前任务结束再退出。如果清理失败，脚本会打印 PID 和命令行，先按提示手工停止后重试。

如果某台电脑只作为浏览器客户端，不要在这台电脑上启动 API 和 worker，直接访问出图电脑输出的 `LAN URL`。如果它也要独立出图，就在那台电脑上部署同一份代码并运行它自己的 `.\start-studio.ps1`；两台电脑的队列互不相同。

在「运行日志」中可查看 worker 启动时使用的队列和数据目录、worker 就绪、实际监听的队列、任务接单、调用 Photoshop、跳过图层和失败原因。任务长时间只有投递记录时，在出图电脑再次运行启动脚本检查 worker 是否订阅了当前队列。正在出图的串行 worker 可能要等当前任务结束才能响应检查。后台进程的原始输出在 `data/studio/logs/` 的 `worker-*.err.log` 和 `worker-*.out.log`。

在项目根目录的 `.env` 中设置 `PHOTOSHOP_RESTART_AFTER_TASKS=10`，指定 Photoshop 每处理多少个任务自动重启一次。可填正整数，未配置时默认 10。修改后重启 Celery worker 生效。样机、透明图、详情页任务各计一次。Photoshop 返回生成、跳过或处理错误时均计数。计数保存在数据目录，重启 worker 后继续累计。

达到配置次数且任务结果写入后，worker 让 Photoshop 正常退出，等待旧进程结束，再启动 Photoshop 并通过 COM 确认就绪。如果调低次数后当前计数已达到新值，worker 会在处理下一份样机前重启 Photoshop。有未保存文档时，worker 会保留文档并在「运行日志」记录原因。下一条任务等待重启，每 60 秒重试；保存或关闭文档后即可继续。

| 路径 | 内容 |
| --- | --- |
| `templates/` | PSD、PSB 样机模板 |
| `data/studio/studio.sqlite3` | 产品、队列、任务状态和日志 |
| `data/studio/outputs/<任务ID>/` | 按任务保存样机图、透明图或详情页切片及缩略图 |
| `version/v1/` | 上一版工作台的源码、脚本、配置示例、测试和文档 |
| `archive/legacy/` | 重构前的项目源码、管理页面、命令行脚本和文档 |

API 和 worker 使用同一份模板和数据目录。升级时保留 `.env`、`templates/` 和 `data/`，在 Photoshop 所在的 Windows 桌面运行 `.\start-studio.ps1`。v2 启动后会自动更新 SQLite 表结构。之前的独立任务和输出仍能从任务队列、日志和输出图管理查看。若需要运行归档的 v1，请供其使用独立的数据目录和样机目录。

## 单文件命令行

```powershell
python .\贴样机.py "样机.psb" -o "output.png" -l "A面" "正面.png" "B面" "背面.png" "枕头" "枕套.png"
```

独立脚本只依赖 Python 标准库、Windows PowerShell 和 Photoshop。命令行使用精确图层名，重名时可写完整路径，如 `贴图区/A面`。输出需指定一个新文件名。

上例先导出原尺寸的 `output.png`。若样机有唯一的「主体」分组，Photoshop 会只显示该组，保留组内原有的图层可见性，将整张画布等比缩放至短边 800 px，再从中心裁剪并导出 `output-透明图.png`（800 × 800 px）。若缺少该组，脚本会提示并保留合成图。

## 详情页生成

```powershell
python .\详情页生成.py ".\templates\详情页模板.psd" -i "主图.png" "细节.png" -o ".\data\春日详情"
```

脚本支持 PSB 和 PSD。按 Photoshop 图层面板从上到下的顺序，将 `-i` 后的图片填入「替换区」分组的智能对象。遇到子分组时按同样顺序处理。每张图居中裁切以填满原智能对象。多出的输入直接忽略，缺图的槽位保留模板内容。

整页按原宽从上到下导出为 `001.png`、`002.png` 等。默认每片高 960 px，最后一片按剩余高度导出。可追加 `--slice-height 1200` 修改高度。`-o` 指定本次新建的输出目录。

在已登录 Photoshop 的 Windows 桌面运行以下测试。它使用项目中的「详情页模板.psd」和六张带编号条纹的色卡，验证导出尺寸和额外输入的处理，并将切片留在 `data/detail-page-test/` 下供查看。

```powershell
.\.venv\Scripts\python.exe .\tests\test_detail_page.py --photoshop
```
