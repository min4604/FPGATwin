# FPGATwin

**FPGA 開發板的數位分身** — 只要提供 RTL 原始碼與接腳約束檔，就能在電腦上模擬整塊開發板：看 LED 亮、撥開關、按按鈕、收發 UART，不需要實體板子。

*A digital twin for FPGA development boards — simulate your RTL together with its pin constraints on a virtual board (LEDs, switches, buttons, UART) on your PC, no hardware required.*

> ⚠️ **狀態：規劃階段（Pre-alpha）**，尚無可用功能。

## 特色（規劃中）

- **直接讀取廠商標準約束檔**：`.ucf`（ISE）、`.xdc`（Vivado），既有專案不需改寫成自訂格式
- **資料驅動的開發板定義**：每塊板子以一份 `board.yaml` 描述板上時脈、LED、開關、按鈕、UART 等硬體的腳位映射，新增板子不需改程式
- **以 [Verilator](https://www.veripool.org/verilator/) 為模擬核心**，支援 Verilog / SystemVerilog
- **Clean-room 原語模型**：依公開文件從零撰寫的常用 FPGA 原語（時脈緩衝器等）

## 預計支援的開發板

| 開發板 | FPGA | 狀態 |
| :--- | :--- | :--- |
| SP601 Evaluation Board | Spartan-6 XC6SLX16-2CSG324 | 規劃中 |

## 開發階段

1. **MVP**：純行為 RTL + `board.yaml` 腳位映射 + LED / 開關 / 按鈕 / UART + 基本緩衝器原語
2. **第二階段**：DCM / PLL 衍生時脈模型
3. **之後依需求**：SPI / BPI Flash、IIC EEPROM、Ethernet PHY 等周邊模型

## 專案結構

```text
src/fpgatwin/
├── cli.py                  # 命令列入口 `fpgatwin`
├── board.py                # board.yaml 載入
├── engine.py               # 模擬引擎介面、demo 引擎、步調控制
├── rtl/                    # 載入自訂 RTL：腳位對應、Verilator 建置、C++ harness、引擎
├── ui/                     # 本機網頁介面（HTTP + Server-Sent Events）
├── constraints/            # .ucf / .xdc 腳位約束解析
├── boards/<board_id>/      # 各開發板的 board.yaml
├── primitives/<family>/    # Clean-room FPGA 原語模型
└── runtime/                # C++ 模擬執行環境（開發中）
examples/                   # 範例 RTL + 約束檔
tests/                      # pytest 測試
```

## 開發環境

```bash
conda env create -f environment.yml   # 建立 fpgatwin 環境（Python 3.12，含 editable 安裝）
conda activate fpgatwin
pytest
fpgatwin boards                       # 列出支援的開發板
fpgatwin board xilinx_sp601           # 顯示板上硬體映射
fpgatwin ui                           # 開啟虛擬開發板網頁介面（demo 引擎）
```

套件本身支援 Python 3.10 以上。`environment.yml` 已包含 [Verilator](https://www.veripool.org/verilator/) 與 g++。

## 載入自己的 RTL

最簡單的方式是直接在網頁上選檔：執行 `fpgatwin ui`，在「設計檔案」區按「選擇檔案…」或「選擇資料夾…」，選好 `.v`/`.sv`（含 `.vh` 等 include 檔）與一個約束檔（`.ucf` 或 `.xdc`），再按「編譯並執行」。腳位對應表、警告與編譯錯誤會顯示在同一區；修改原始檔後再按一次「編譯並執行」即會重新讀取。「改用 Demo」可切回示範引擎。

也可以在命令列直接指定：

```bash
fpgatwin ui --rtl my_design.v --ucf my_design.ucf
```

FPGATwin 會用 Verilator 編譯設計、依 UCF 把 port 接到板上元件，然後在瀏覽器開啟虛擬開發板。終端機會先印出腳位對應表與警告（例如 port 沒有 LOC、腳位不在板子模型中）。

| 選項 | 說明 |
| :--- | :--- |
| `--rtl` | Verilog / SystemVerilog 檔案或資料夾，可列多個 |
| `--ucf` | 腳位約束檔：ISE `.ucf` 或 Vivado `.xdc`（`set_property PACKAGE_PIN`） |
| `--top` | 頂層模組名稱（預設由 Verilator 自動判斷） |
| `--board` | 開發板 id（預設 `xilinx_sp601`） |
| `--port` | 本機埠號（預設 `8600`） |
| `--speed` | 相對真實時間的模擬速度（預設 `1.0`；跑不到時會盡量快並顯示實際速率） |
| `--clock` | 僅 demo 引擎：驅動用的時脈 |
| `--no-browser` | 不自動開啟瀏覽器 |

只想檢查腳位對應、不開 UI：

```bash
fpgatwin build --rtl my_design.v --ucf my_design.ucf
```

### 目前支援範圍

- 頂層 port 接到板上的**時脈**（自動產生，差動時脈的 N 端自動反相）、**LED、DIP 開關、按鈕**
- 已內建的 Spartan-6 原語：`IBUF`、`IBUFG`、`IBUFDS`、`IBUFGDS`、`OBUF`、`BUFG`
- **UART**：模擬板上的 USB-UART 晶片（SP601 為 CP2103：FPGA TX = L12、RX = K14），以腳位層級收發 8N1 訊框，所以跑的是你 RTL 裡自己的 UART 邏輯。網頁的 UART 區可選鮑率（預設 115200）、以文字或 HEX 顯示、輸入文字送給 FPGA；停止位元錯誤會顯示「框架錯誤」，通常代表鮑率與設計不符。CTS 固定為允許傳送，RTS 不使用。範例見 `examples/sp601_uart_echo`
- 設計的 `$display` 輸出會印在終端機；`$finish` 會讓模擬停止並在畫面上提示
- 尚未支援：`inout` port、超過 64 位元的 port、DCM/PLL、UART 以外的周邊、`.xdc` 的萬用字元與 `-filter`、有 parity 或 2 個停止位元等非 8N1 的 UART 格式

編譯結果依內容快取在 `%LOCALAPPDATA%\fpgatwin\cache`（Linux/macOS：`~/.cache/fpgatwin`），可用環境變數 `FPGATWIN_CACHE` 改位置。第一次編譯約 10–20 秒，之後相同設計直接重用。

### Windows 備註

FPGATwin 直接呼叫 `verilator_bin` 與 g++，不經過 Verilator 的 Makefile，所以一般 PowerShell 就能用，不需要 Git Bash。針對 conda-forge 工具鏈的處理（皆自動套用）：

| 處理 | 原因 |
| :--- | :--- |
| 直接呼叫 `verilator_bin.exe` | perl 包裝程式轉傳參數時沒加引號，路徑有空格會被拆開 |
| `-std=gnu++17` | conda-forge g++ 16 預設 C++20 時，`std::string` 會連結失敗 |
| `-static` 靜態連結 | 動態連結 `libstdc++-6.dll` 時執行會 segfault |
| 非 ASCII 路徑改用 8.3 短路徑，無短路徑時複製到建置目錄 | Verilator 與 MinGW g++ 只能用系統字碼頁開檔 |

## 限制

本專案僅供**邏輯與功能驗證**，無法重現實體電氣特性，例如訊號完整性、時脈抖動、時序收斂，以及上電順序等硬體操作安全事項。模擬結果正確不代表設計在實體板上一定能正常運作。

## 授權

本專案採用 [Apache License 2.0](LICENSE) 授權。

## 商標聲明

FPGATwin 是獨立的開源專案，與 AMD、Xilinx 或任何 FPGA 廠商**無任何關聯**，亦未獲其認可或贊助。Xilinx、Spartan、Vivado、ISE 等為其各自擁有者之商標，本文件提及僅為說明相容性。
