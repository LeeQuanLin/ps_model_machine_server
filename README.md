# Photoshop 控制服务

Windows 上的 FastAPI 服务。通过 COM 启动或正常关闭 `.env` 指定的 Photoshop。两个接口都要求 `Authorization: Bearer <token>`。

## 目录结构

```text
app/
  main.py                  FastAPI 入口
  api/                     接口路由和 Bearer Token 鉴权
  core/config.py           环境变量配置
  schemas/                 API 响应模型
  services/                Photoshop COM 调用及 PowerShell 脚本
scripts/                   命令行贴样机和模板检查脚本
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

从旧版升级时，已有的 `photoshop_control/.env` 请移到项目根目录并继续使用。

```powershell
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --workers 1
```

通过内网反向代理或 SSH 隧道连接远程服务。使用反向代理时由代理提供 HTTPS，避免 Bearer Token 明文传输。若用 Windows 任务计划程序开机后启动，选择在渲染账号登录时触发，并选择「仅当用户登录时运行」。保留单个 Uvicorn worker，启动和关闭请求会串行执行。

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

`401` 表示 Token 缺失或不匹配。`409` 表示 Photoshop 有未保存的文档、注册指向另一安装，或当前会话存在冲突的 Photoshop 进程。`503` 表示 COM 或 Windows 会话不可用。`504` 表示超时，可查看 Photoshop 桌面是否有弹窗。关闭请求始终使用正常退出流程，未保存的文档需要先保存或手动处理。

## 命令行贴样机

从项目根目录运行。省略 `--out` 时默认保存为 `data/<图案名>_<模板名>.png`。

```powershell
python -m scripts.mockup --art 'D:\artwork.png' --template 'D:\898.psd' --layer 21
```

`--prepare-only` 会生成可以在 Photoshop 中打开的 JSX。`data/archive/` 中的旧 JSX 和报告用于查阅历史结果。下载的源码包包含网格测试图，样机 PSD／PSB 需自行提供。
