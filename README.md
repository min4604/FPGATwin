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
├── constraints/            # .ucf / .xdc 解析（開發中）
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
```

套件本身支援 Python 3.10 以上。`environment.yml` 已包含 [Verilator](https://www.veripool.org/verilator/)、g++ 與 make。

### 在 Windows 上用 Verilator（conda-forge 版）

conda-forge 的 Windows 版 Verilator 預設的編譯器路徑指向打包機器上的 MSVC，需要手動覆寫。請在 **Git Bash** 中執行（Makefile 需要 POSIX shell）：

```bash
E="$(cygpath "$(conda run -n fpgatwin python -c 'import sys; print(sys.prefix)' | tr -d '\r')")"
export PATH="$E/Library/bin:$E/bin:$PATH"
perl "$E/Library/bin/verilator" --cc --exe --build -Wall \
    -MAKEFLAGS CXX=g++ -MAKEFLAGS LINK=g++ \
    -MAKEFLAGS AR=x86_64-w64-mingw32-ar -MAKEFLAGS PYTHON3=python \
    -MAKEFLAGS CFG_CXXFLAGS_STD=-std=gnu++17 \
    -LDFLAGS -static \
    <design.v> <sim_main.cpp>
```

各參數的原因：

| 參數 | 原因 |
| :--- | :--- |
| `CXX` / `LINK` / `AR` / `PYTHON3` | 套件內 `verilated.mk` 寫死了打包機器的 MSVC 與 Python 路徑 |
| `-std=gnu++17` | conda-forge g++ 16 預設 C++20 時，`std::string` 會連結失敗 |
| `-LDFLAGS -static` | 動態連結 `libstdc++-6.dll` 時執行會 segfault，靜態連結正常 |

C++ harness 需自行定義 `double sc_time_stamp() { return 0; }`。之後 `fpgatwin` 指令會自動處理這些參數。

## 限制

本專案僅供**邏輯與功能驗證**，無法重現實體電氣特性，例如訊號完整性、時脈抖動、時序收斂，以及上電順序等硬體操作安全事項。模擬結果正確不代表設計在實體板上一定能正常運作。

## 授權

本專案採用 [Apache License 2.0](LICENSE) 授權。

## 商標聲明

FPGATwin 是獨立的開源專案，與 AMD、Xilinx 或任何 FPGA 廠商**無任何關聯**，亦未獲其認可或贊助。Xilinx、Spartan、Vivado、ISE 等為其各自擁有者之商標，本文件提及僅為說明相容性。
