# Spartan-6 原語模型（Clean-room）

本目錄將放置 Spartan-6 常用原語的行為模型，供 Verilator 編譯使用者的 RTL 時使用。

## 撰寫規則（必讀）

- **只能依公開文件**（Libraries Guide、User Guide 等）的介面與行為描述**從零撰寫**。
- **禁止**開啟、參考或複製任何廠商安裝目錄中的模擬原始碼（例如 ISE / Vivado 的 `unisims`、`simprims`、`XilinxCoreLib`）。
- 引用其他開源專案的模型時，必須是相容授權（例如 Yosys 的 ISC），並保留原版權聲明。
- 每個檔案開頭註明參考的公開文件名稱與版本。

## 預計收錄

| 階段 | 原語 |
| :--- | :--- |
| MVP（已完成） | `IBUF`、`OBUF`、`IBUFG`、`IBUFDS`、`IBUFGDS`、`BUFG` |
| 第二階段 | `DCM_SP`、`PLL_BASE` |
| 之後 | `RAMB16BWER`、`ODDR2`、`DSP48A1` 等 |
