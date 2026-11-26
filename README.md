# 旅途账本

一个使用 Python 标准库和 Tkinter 编写的桌面旅行记账工具，支持多币种消费、AA 分摊、旅行预算、统计图表、CSV / Excel 导出及 JSON 备份恢复。

日常记账可离线使用。点击“查询最新汇率”时才会请求 Coinbase 的公开汇率接口；当前实现不需要 API Key，也不会在此请求中发送账本内容。仓库只包含程序源码和通用测试数据。

## 环境与依赖

- Python **3.10 或更新版本**，并带有 **Tcl/Tk（Tkinter）**。
- 图形界面运行和完整测试需要桌面环境。Windows 提供双击启动脚本和可选的 EXE 打包配置。
- 运行和测试均无第三方 Python 依赖，说明见 `requirements.txt`。Excel 报表直接使用标准库生成，无需安装 Excel、`openpyxl` 或 `pandas`。
- 只有自行打包 EXE 时才需要安装 `requirements-build.txt` 中的 PyInstaller。

下载或克隆项目后，在项目目录运行下面的启动命令即可。Windows Python 安装器中请保留 Tcl/Tk 支持，并按需勾选“Add Python to PATH”。可运行 `python -m tkinter` 检查 Tkinter，成功时会弹出一个小窗口；Tkinter 不通过 `pip install` 安装。

## 启动软件

**Windows 最简单的方法：双击当前文件夹里的 `启动旅行记账.bat`。**

也可以在当前文件夹的空白处右键，选择“在终端中打开”，输入下面这一行并按回车：

```powershell
python travel_ledger.py
```

如果 `python` 命令不可用，可尝试：

```powershell
py -3 travel_ledger.py
```

打开窗口后即可使用。关闭软件窗口就是退出，已成功保存的记录下次启动会自动恢复。

运行程序无需执行 `pip install`。首次运行从空白账本开始；个人数据不会随源码分发。

## 第一次记账

1. 点击右上角 **汇率与成员**。成员每行填一个名字，默认是“我”和“同行人”；只自己旅行时可以改成只有“我”。
2. 按需输入汇率并点击 **保存设置**。汇率方向是 **1 单位外币等于多少人民币**。人民币固定为 1；其他币种初始为 0，表示未设置，不能新增该币种的消费。
3. 点击 **添加消费**，填写日期、说明、分类、原币金额、币种及实际付款人。
4. 勾选这笔费用的分摊成员，点击 **保存消费**。每次成功添加、修改、删除记录或保存设置都会立即自动保存。

例如：如果你查到的美元汇率是“1 美元 = 7.20 人民币”，就在美元一栏输入 `7.20`，消费 `10` 美元会折合 `72.00` 元人民币。**这里的 7.20 只是演示数据，请填入你实际采用的汇率。** 韩元、印尼盾和泰铢也采用相同方向，不要把“1 人民币等于多少外币”直接填入。

金额应大于 0，最多两位小数；日期使用 `YYYY-MM-DD`，例如 `2026-11-26`。分类包括餐饮、住宿、交通、购物、门票、娱乐和其他。支持人民币 CNY、韩元 KRW、美元 USD、印尼盾 IDR、泰铢 THB。

## 修改、删除与查找

- **修改：** 单击一条记录，再点击“修改”，也可以双击记录。
- **删除：** 选中记录，点击“删除”，核对确认框后删除。
- **查找：** 在“搜索说明”中输入文字，或选择消费分类。顶部总支出、图表和 AA 始终统计全部记录。

每笔消费保存当时的汇率。以后修改设置里的汇率不会改变旧账。同币种修改金额或说明时保留原汇率；把记录改为另一币种时，使用该币种当前设置的汇率。涉及旧记录的成员不能直接删除或改名，需要先处理引用该成员的记录。

## AA 怎么计算

每笔记录由一人付款，在勾选的成员间均分。个人消费只勾选自己；几个人分别付款时，按实际付款人拆成多笔记录。

例如：我先付餐费 100 元，“我”和“同行人”共同分摊。两人各承担 50 元，AA 页面会建议“同行人 → 我，50.00 元”。再录入其他人垫付的费用后，软件会自动抵消往来金额，给出人民币转账建议。

净额 = 实际垫付 − 应承担金额。正数表示应收，负数表示应付。先将每笔消费四舍五入为人民币分，再均分；除不尽的分按该笔参与人顺序分配，确保总额一致。转账建议不会实际付款，也不记录已结算状态；这本账适合一次旅行使用，需要结算时按最终方案转账。

## 外观、预算与在线汇率

点击右上角“切换深色”或“切换浅色”，软件会记住这个账本的外观。“账本工具 → 旅行预算”可设置整趟旅行的人民币上限。主页显示剩余预算，超过时以醒目颜色显示超支金额。填 0 可关闭提醒。

在“汇率与成员”中点击“查询最新汇率”，可以一次获取四种外币的 Coinbase 参考汇率，界面显示本次查询时间。服务端决定报价更新频率。查询后仍可编辑，点击“保存设置”才会应用到新消费。

## 统计与报表

**支出统计** 可以按日、月、年切换。分类图汇总整趟旅行，走势图显示最近 14 个有记录的期间；“全部期间明细”可查看更早的合计。

在“账本工具 → 导出 Excel 报表”中选择保存位置，即可获得含旅行汇总、消费明细、按日统计、按月统计和按年统计的五页工作簿。金额单元格可在 Excel 中直接求和。

点击 **导出 CSV**，选择文件名和位置，即可导出全部消费记录，包括原币金额、币种、记账时汇率、人民币金额、付款人和参与人。文件采用带 BOM 的 UTF-8 编码，可用 Excel 打开中文。若文件正在 Excel 中打开，请先关闭它再覆盖导出。

## 数据保存与备份

在“账本工具 → 备份账本”中选择 JSON 文件位置即可备份消费、历史汇率、成员、预算及主题。“从备份恢复”会检查备份格式，在替换当前账本前把原账本保存到当前账本旁的 `backups` 文件夹。旧版本 JSON 也可以恢复，预算默认关闭，主题默认为浅色。

默认账本位于程序文件夹内：

```text
data/travel_ledger.json
```

第一次成功保存后自动创建。旁边的 `.lock` 文件用于防止同时打开同一本账，正常存在即可。请把项目放在有写入权限的文件夹中，并在退出软件后复制整个 `data` 文件夹作为备份。CSV 是消费报表，不能替代包含汇率、成员和记录的 JSON 账本备份。

保存失败时会弹出提示，本次修改不会被当成保存成功。数据文件损坏时程序会提示错误并保留原文件；请先备份并检查文件，或从备份恢复。

如果需要另一趟旅行的独立账本，可以在终端运行：

```powershell
python travel_ledger.py --data data/第二次旅行.json
```

以后用同一条命令打开该账本。默认双击启动的是 `data/travel_ledger.json`。`--data` 的相对路径按终端当前目录解析；打包 EXE 的默认账本则位于 EXE 旁的 `data/travel_ledger.json`。

## 项目文件与检查命令

| 文件 | 用途 |
| --- | --- |
| `travel_ledger.py` | Tkinter 图形界面和启动入口 |
| `ledger_core.py` | 金额计算、AA 分摊、JSON 保存和 CSV 导出 |
| `travel_reports.py` | 在线汇率查询及 Excel 报表生成 |
| `启动旅行记账.bat` | Windows 双击启动 |
| `assets/travel-ledger.ico` | 应用和 EXE 图标 |
| `travel_ledger.spec` | PyInstaller 打包配置，不包含个人账本 |
| `requirements.txt` | 运行环境与标准库依赖说明 |
| `requirements-build.txt` | 可选 EXE 打包依赖 |
| `.gitignore` | 个人数据、凭据、缓存及构建产物排除规则 |
| `tests/` | 自动检查程序的测试 |

代码中有中文注释。想了解程序时，可以先看 `ledger_core.py` 的 `add_record()`、`totals()`、`settlement()`，再看界面的 `RecordDialog`。

在当前文件夹运行自动检查：

```powershell
python -m unittest discover -s tests -v
```

测试使用临时账本，不修改你的正式数据。在线汇率测试使用模拟响应，不访问真实接口。图形界面测试需要能运行 Tkinter 的桌面环境。

仅检查核心账本逻辑时可以运行：

```powershell
python -m unittest discover -s tests -p test_ledger_core.py -v
```

## 可选：在 Windows 打包 EXE

在带有 Tcl/Tk 的 Python 环境中，从项目根目录运行：

```powershell
python -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv-build\Scripts\python.exe -m PyInstaller --noconfirm --workpath build --distpath dist travel_ledger.spec
```

`requirements-build.txt` 固定为现有本地打包环境中的 PyInstaller 6.22.3；它是可选构建工具，不是运行依赖。产物为 `dist/旅途账本.exe`，构建过程产生的 `build/`、`dist/` 和虚拟环境都已忽略。`travel_ledger.spec` 和图标属于打包源文件，应随源码提交。

## GitHub 提交范围与隐私

`.gitignore` 排除以下本地内容，文件仍保留在电脑上：

- 个人账本与备份：任意层级的 `data/`、`backups/`、`backup/`，以及 JSON、SQLite 等数据文件和锁文件。
- 导出报表：`exports/`、`reports/`、CSV、TSV、Excel 文件，以及可能携带数据的备份与压缩包。
- 凭据：`.env` 及其变体、`secrets/`、`credentials/`、密码和令牌文件、私钥、本机配置等。
- 虚拟环境、Python 缓存、日志、临时文件、覆盖率结果、`.qa/`、编辑器设置及临时笔记本。
- EXE、安装包、`build/`、`dist/` 和其他打包产物。

本项目没有需要发布的 JSON 源文件，因此默认忽略所有 JSON 文件，保护用 `--data` 另取文件名的账本与自行选择位置保存的备份。以后如需增加 JSON 配置或示例，请先使用虚构内容并单独检查忽略规则。请勿把 API Key、密码或真实账目直接写入源码、测试或文档；忽略规则不能过滤这些文件内部的内容，也不会自动移除已经被 Git 跟踪的文件。

提交前可在项目目录预览（以下命令均不会提交或上传）：

```powershell
git status --short --untracked-files=all
git -c core.quotepath=false ls-files --others --exclude-standard
git check-ignore -v data/travel_ledger.json dist/旅途账本.exe .env
```

预期提交内容为：本 README、`.gitignore`、两个依赖说明文件、三个应用 Python 模块、Windows 启动脚本、PyInstaller 配置、应用图标，以及 `tests/` 下的四个 Python 测试文件。
