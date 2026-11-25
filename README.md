# Photoshop 控制服务

Windows 上的 FastAPI 服务。通过 COM 启动或正常关闭 `.env` 指定的 Photoshop，并提供样机目录和缩略图。业务接口都要求 `Authorization: Bearer <token>`。

## 目录结构

```text
app/
  main.py                  FastAPI 入口
  api/                     接口路由和 Bearer Token 鉴权
  core/config.py           环境变量配置
  schemas/                 API 响应模型
  services/                Photoshop COM 调用及 PowerShell 脚本
scripts/                   命令行贴样机和模板检查脚本
templates/                 默认存放 PSD／PSB 样机模板
data/                      本地出图结果与打包文件
  fixtures/                网格测试输入
  archive/                 898 样机历史试跑的副本和记录
.env.example               环境变量示例
requirements.txt           API 运行依赖
```

## Windows 部署

在项目根目录或 ZIP 解压目录执行。使用已登录 Windows 桌面的渲染账号运行，需要 64 位 Python 3.10 或更新版本。

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
Copy-Item .\.env.example .\.env
& .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
notepad .\.env
```

将 `PHOTOSHOP_EXE` 填成绿色版 **实际的 `Photoshop.exe` 绝对路径**，并将生成的 Token 填到 `PHOTOSHOP_API_TOKEN`。也可以用同名系统环境变量覆盖 `.env`。

`PHOTOSHOP_TEMPLATE_DIR` 默认指向项目根目录下的 `templates/`。可以填绝对路径，也可以填相对于项目根目录的路径。Git 会忽略默认目录中的模板内容，以及项目内其他目录的 PSD 和 PSB 文件。

从旧版升级时，已有的 `photoshop_control/.env` 请移到项目根目录并继续使用。

```powershell
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --workers 1
```

通过内网反向代理或 SSH 隧道连接远程服务。使用反向代理时由代理提供 HTTPS，避免 Bearer Token 明文传输。若用 Windows 任务计划程序开机后启动，选择在渲染账号登录时触发，并选择「仅当用户登录时运行」。保留单个 Uvicorn worker，启动和关闭请求会串行执行。

### 处理 Windows Session 0 错误

如果出图提示 `Windows Session 0` 或 `Run this service in the logged-in Windows user desktop session.`，在服务器上查看监听进程所属的会话。端口按实际部署修改。

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Get-Process -Id $_ | Select-Object Id, ProcessName, SessionId }
Get-Process -Name Photoshop -ErrorAction SilentlyContinue |
    Select-Object Id, ProcessName, SessionId
```

通过原来的启动方式停止旧服务。用渲染账号登录服务器桌面（本机或远程桌面），在该桌面的 PowerShell 窗口中执行上面的 Uvicorn 命令。

如果平时通过 SSH 管理服务器，可以在服务器的项目目录注册并触发桌面计划任务。先让渲染账号登录一次 Windows 桌面。脚本默认用当前账号注册任务，SSH 账号与渲染账号不同时，指定 `-RenderUser '服务器名\渲染账号'`，并使用有权为该账号注册计划任务的账号执行。

```powershell
.\scripts\register_desktop_task.ps1
Start-ScheduledTask -TaskName 'PhotoshopMockupServer' -TaskPath '\'
```

默认监听 `127.0.0.1:8765`。已有部署使用其他监听地址或端口时，注册时传入对应的 `-BindAddress` 和 `-Port`。任务也会在渲染账号下次登录时自动启动。通过 SSH 触发时，任务仍在已登录的 Windows 桌面会话里运行。使用远程桌面时，可以断开连接并保留登录会话。

启动后用上面的命令确认端口监听进程的 `SessionId` 为非零。若 Photoshop 已打开，它的 `SessionId` 应与监听进程一致。然后重新提交失败的出图任务。

### 检查绿色版 COM 注册

绿色版必须提供 `Photoshop.Application` 的 COM 注册能力，注册的 EXE 必须与 `PHOTOSHOP_EXE` 一致。先在运行服务的账号下检查注册项：

```powershell
$classKey = Get-Item 'Registry::HKEY_CLASSES_ROOT\Photoshop.Application\CLSID'
$classId = $classKey.GetValue('')
$serverKey = Get-Item "Registry::HKEY_CLASSES_ROOT\CLSID\$classId\LocalServer32"
$serverKey.GetValue('ServerExecutable')
$serverKey.GetValue('')
```

`ServerExecutable` 如果有值，以该值为准。默认值可能带 `/Automation` 参数。注册项缺失时，使用当前 Photoshop 包提供的注册方式，或选用能注册 COM 的安装包。`.env` 只指定并校验目标路径。若 COM 指向同机另一套 Photoshop，接口会返回 `409`。

## 接口

文档地址为 `http://127.0.0.1:8765/docs`。点击 `Authorize` 后填入 Token 即可调用。也可以从 PowerShell 调用：

```powershell
$token = Read-Host 'Photoshop API token'
$headers = @{ Authorization = "Bearer $token" }
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8765/photoshop/start' -Headers $headers
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8765/photoshop/stop' -Headers $headers
```

| 接口 | 成功响应 | 重复调用 |
| --- | --- | --- |
| `POST /photoshop/start` | `status=running`，返回进程 PID 和 COM 读取到的版本 | 已运行时返回 `changed=false` |
| `POST /photoshop/stop` | 确认进程退出后返回 `status=stopped` | 当前会话中目标已关闭时返回 `changed=false` |
| `GET /templates` | 返回样机列表，包含 ID、文件名、相对路径、像素尺寸、文件大小和缩略图地址 | 每次读取本次启动时扫描的目录快照 |
| `GET /templates/{template_id}/thumbnail` | 返回 JPEG 或 PNG 缩略图 | ID 或缩略图不存在时返回 `404` |

### 管理后台

打开 `http://127.0.0.1:8765/`，输入 `.env` 中的 `PHOTOSHOP_API_TOKEN`。

- **样机管理**支持上传 PSD 或 PSB、刷新目录、搜索、预览缩略图和删除样机。最大上传大小为 8 GB。
- **生成样机**支持上传 PNG 或 JPEG（最大 50 MB）。选择样机后，在带缩略图的图层列表中点选要替换的图层，再提交出图任务。
- **生成图**展示排队、生成中、完成和失败状态，支持预览、下载原尺寸 PNG 和删除记录。PNG 文件与任务记录保存在 `data/renders/`。

提交出图后，服务在当前 Windows 桌面会话里通过已注册的 Photoshop COM 串行渲染。服务重启时，中断的任务会显示为失败。

`401` 表示 Token 缺失或不匹配。`409` 表示 Photoshop 有未保存的文档、注册指向另一安装，或当前会话存在冲突的 Photoshop 进程。`503` 表示 COM 或 Windows 会话不可用。`504` 表示超时，可查看 Photoshop 桌面是否有弹窗。关闭请求始终使用正常退出流程，未保存的文档需要先保存或手动处理。

### 样机列表和缩略图

服务每次启动时递归读取 `PHOTOSHOP_TEMPLATE_DIR` 中的 PSD 和 PSB。上传、启动或刷新目录时，优先使用模板同目录下的同名图片，例如 `898.psd` 旁边的 `898.png`、`898.jpg` 或 `898.jpeg`。没有可用的同名图片时，后台通过 Photoshop 将模板按原比例导出为短边 800 px 的同名 PNG。导出期间可使用模板内嵌预览。导出失败时会保留可用的原预览，点击「刷新目录」可以重试。读取缩略图地址时同样需要携带 Bearer Token。

新增或刷新样机时，服务同时读取名为「贴图区」的图层组，包含其中子组里的可替换智能对象。旧版样机没有这个组时，会识别顶层零不透明度的原始智能对象及它在画面中的可见副本。样机列表的 `replacement_layers` 包含每个可替换图层的名称、路径和 ID，提交 `POST /renders` 时可用 `layer_id` 指定。后台每次生成替换一个选项，共用该智能对象内容的副本会同步更新。

```powershell
Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8765/templates' -Headers $headers
```

## 命令行贴样机

从项目根目录运行。`--template` 支持绝对路径、当前目录中已有的相对路径和模板目录里的文件名。省略 `--template` 时使用模板目录中的 `898(1).psd`，默认图层 ID 为 `21`。省略 `--out` 时默认保存为 `data/<图案名>_<模板名>.png`。

```powershell
python -m scripts.mockup --art 'D:\artwork.png' --template '898(1).psd' --layer 21
```

`--prepare-only` 会生成可以在 Photoshop 中打开的 JSX。`data/archive/` 中的旧 JSX 和报告用于查阅历史结果。下载的源码包包含网格测试图，样机 PSD／PSB 需自行提供。
